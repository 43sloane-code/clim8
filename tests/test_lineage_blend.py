"""Network-free tests for candidate 41 — the inverse-CRPS LINEAGE blend (ADDITIVE,
recommend-only). Proves the load-bearing claims with deterministic oracles and a
REPLAY on the real logged streams:

  * `build_lineages` is leak-free and correct (council = logged point;
    persistence = yesterday's obs; climatology = trailing mean of strictly-earlier
    obs; day 0 references are undefined);
  * inverse-CRPS weighting orders lineages by skill and floors a thin window to
    EQUAL weights + an underpowered flag;
  * the weighted-mixture sample and `blend_moments` realise the spec's within +
    between variance decomposition (between-lineage spread is captured for free);
  * POSITIVE control — adaptive inverse-CRPS weighting beats the naive equal-weight
    pool on a sharp+loose pair;
  * NEGATIVE control / overfit guard — when one lineage dominates, the blend CANNOT
    significantly beat that single lineage (no manufactured edge);
  * the leak-free walk-forward on the real logs is well-formed (paired deltas of the
    right length, weights normalised, a computable paired-CRPS bootstrap CI).

Stdlib unittest only. Run with:
    PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import csv
import os
import random
import unittest

from weather_council.lineage_blend import (
    LINEAGES, build_lineages, inverse_crps_weights, blend_moments, mixture_sample,
    walk_forward_blend, _trailing_compare, _self_test,
)
from tools.daily_healthcheck import _paired_bootstrap_ci

REPORTS = os.path.join(os.path.dirname(__file__), "..", "reports")
STREAMS = ("london_high.csv", "london_low.csv",
           "hong_kong_high.csv", "hong_kong_low.csv")


def _load(path):
    out = []
    with open(path) as fh:
        for r in csv.DictReader(fh):
            try:
                out.append((r["date"], float(r["point"]), float(r["realized"])))
            except (ValueError, KeyError):
                continue
    out.sort()
    return out


class TestLineageBlend(unittest.TestCase):
    def test_module_self_test(self):
        _self_test()

    def test_build_lineages_leakfree_and_correct(self):
        rows = [("2025-01-01", 10.0, 11.0),
                ("2025-01-02", 12.0, 13.0),
                ("2025-01-03", 14.0, 12.0)]
        L = build_lineages(rows)
        self.assertEqual(L["n"], 3)
        # council = logged point on every day.
        self.assertEqual(L["forecast"]["council"], [10.0, 12.0, 14.0])
        # persistence = yesterday's obs; undefined on day 0.
        self.assertIsNone(L["forecast"]["persistence"][0])
        self.assertEqual(L["forecast"]["persistence"][1], 11.0)
        self.assertEqual(L["forecast"]["persistence"][2], 13.0)
        # climatology = trailing mean of strictly-earlier obs; undefined day 0.
        self.assertIsNone(L["forecast"]["climatology"][0])
        self.assertAlmostEqual(L["forecast"]["climatology"][1], 11.0)        # mean(11)
        self.assertAlmostEqual(L["forecast"]["climatology"][2], 12.0)        # mean(11,13)
        # resid = realized − forecast (harness sign).
        self.assertAlmostEqual(L["resid"]["council"][0], 1.0)
        self.assertAlmostEqual(L["resid"]["persistence"][2], -1.0)           # 12 − 13

    def test_weights_order_by_skill_and_underpowered_fallback(self):
        # Powered window: lower trailing CRPS ⇒ higher weight.
        w, under = inverse_crps_weights({"council": [0.2] * 40, "persistence": [0.5] * 40,
                                         "climatology": [1.0] * 40})
        self.assertFalse(under)
        self.assertAlmostEqual(sum(w.values()), 1.0, places=12)
        self.assertGreater(w["council"], w["persistence"])
        self.assertGreater(w["persistence"], w["climatology"])
        # Thin window ⇒ EQUAL weights + flag.
        w2, under2 = inverse_crps_weights({"council": [0.3] * 5, "persistence": [0.4] * 5,
                                           "climatology": [0.5] * 5})
        self.assertTrue(under2)
        self.assertEqual(len(set(w2.values())), 1)
        self.assertAlmostEqual(sum(w2.values()), 1.0, places=12)

    def test_blend_moments_within_between_identity(self):
        means = {"council": 0.0, "persistence": 2.0, "climatology": -1.0}
        variances = {"council": 1.0, "persistence": 1.5, "climatology": 0.5}
        weights = {"council": 0.5, "persistence": 0.3, "climatology": 0.2}
        bm = blend_moments(means, variances, weights)
        self.assertAlmostEqual(bm["total"], bm["within"] + bm["between"], places=12)
        self.assertGreater(bm["between"], 0.0)                       # spread captured for free
        # within is the weighted mean of the variances.
        self.assertAlmostEqual(bm["within"], 0.5 * 1.0 + 0.3 * 1.5 + 0.2 * 0.5, places=12)

    def test_mixture_sample_captures_between_spread(self):
        # Two tight clouds at separated means ⇒ pooled spread reflects the gap.
        clouds = {"council": [-0.1, 0.0, 0.1], "persistence": [9.9, 10.0, 10.1],
                  "climatology": None}
        w = {"council": 0.5, "persistence": 0.5, "climatology": 0.0}
        s = mixture_sample(clouds, w, m=200)
        self.assertGreater(len(s), 0)
        # variance of the pooled sample is dominated by the between-mean gap (~25).
        self.assertGreater(statistics_variance(s), 10.0)

    def test_positive_control_adaptive_beats_equal_pool(self):
        rng = random.Random(411)
        n = 360
        dom = [rng.gauss(0.0, 0.4) for _ in range(n)]
        bad = [rng.gauss(0.0, 2.0) for _ in range(n)]
        cmp = _trailing_compare(dom, bad)
        self.assertLessEqual(cmp["mean_adapt"], cmp["mean_equal"] + 1e-9)

    def test_negative_control_no_edge_over_dominant_lineage(self):
        rng = random.Random(412)
        n = 360
        dom = [rng.gauss(0.0, 0.4) for _ in range(n)]
        bad = [rng.gauss(0.0, 2.0) for _ in range(n)]
        cmp = _trailing_compare(dom, bad)
        pt, lo, hi, _ = _paired_bootstrap_ci(cmp["delta_vs_x"])
        self.assertFalse(lo is not None and lo > 0.0)               # NOT a significant win

    def test_replay_real_streams_well_formed(self):
        """Spec acceptance: leak-free held-out walk-forward on the real logs yields
        well-formed paired deltas (council & best-single), normalised daily weights,
        and a computable paired-CRPS bootstrap CI."""
        for fname in STREAMS:
            path = os.path.join(REPORTS, fname)
            if not os.path.exists(path):
                self.skipTest(f"missing {fname}")
            rows = _load(path)
            self.assertGreaterEqual(len(rows), 30, fname)
            res = walk_forward_blend(rows)
            self.assertGreater(res["n_test"], 0, fname)
            self.assertEqual(len(res["deltas_council"]), res["n_test"])
            self.assertEqual(len(res["deltas_best"]), res["n_test"])
            self.assertEqual(len(res["weights"]), res["n_test"])
            for w in res["weights"]:
                self.assertAlmostEqual(sum(w.values()), 1.0, places=6, msg=fname)
            for k in res["weights"][-1]:
                self.assertIn(k, LINEAGES)
            pt, lo, hi, n = _paired_bootstrap_ci(res["deltas_best"])
            self.assertEqual(n, res["n_test"])
            self.assertIsNotNone(lo)
            self.assertLessEqual(lo, hi)


def statistics_variance(xs):
    import statistics
    return statistics.pvariance(xs)


if __name__ == "__main__":
    unittest.main()
