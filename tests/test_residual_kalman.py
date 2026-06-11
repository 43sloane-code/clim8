"""Network-free tests for candidate 40 — the residual local-level Kalman bias-drift
filter (ADDITIVE, recommend-only, on top of the #34 offset).

Proves the load-bearing claims with deterministic oracles and a REPLAY on the real
logged streams:

  * the forward filter is a valid local-level recursion (gains in (0,1); a
    constant residual is tracked to that constant; zero stays zero);
  * (q, s) MLE returns strictly positive variances and a finite log-likelihood;
  * POSITIVE control — a genuinely drifting bias is tracked and the Kalman beats
    the pooled cloud out-of-sample;
  * NEGATIVE control — pure i.i.d. noise yields NO significant improvement and a
    negligible effect (the overfit guard: the filter cannot manufacture an edge);
  * the leak-free walk-forward on the real logs produces a sane single-day update
    (steady-state gain well below 1.0) and a well-formed paired-CRPS delta.

Stdlib unittest only. Run with:
    PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import csv
import math
import os
import random
import unittest

from weather_council.residual_kalman import (
    kalman_local_level, fit_qs, kalman_one_step, walk_forward_kalman,
    WARMUP, CRPS_MIN, _self_test,
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


class TestResidualKalman(unittest.TestCase):
    def test_module_self_test(self):
        _self_test()

    def test_filter_recursion_sanity(self):
        f = kalman_local_level([2.0] * 80, q=0.05, s=1.0)
        self.assertTrue(all(0.0 < k < 1.0 for k in f["gains"]))
        self.assertAlmostEqual(f["b_filt"][-1], 2.0, delta=0.2)   # tracks the constant
        self.assertTrue(math.isfinite(f["loglik"]))
        f0 = kalman_local_level([0.0] * 80, q=0.05, s=1.0)
        self.assertAlmostEqual(f0["b_filt"][-1], 0.0, places=6)   # zero stays zero
        # Gains are monotone settling toward a steady state below 1.
        self.assertLess(f["gains"][-1], 1.0)

    def test_fit_returns_positive_variances(self):
        rng = random.Random(40)
        q, s, ll = fit_qs([rng.gauss(0, 1.3) for _ in range(150)])
        self.assertGreater(q, 0.0)
        self.assertGreater(s, 0.0)
        self.assertTrue(math.isfinite(ll))

    def test_one_step_is_leak_free_shape(self):
        # The one-step predicted mean uses only the prior; empty prior => diffuse.
        b0, v0 = kalman_one_step([], 0.05, 1.0)
        self.assertEqual(b0, 0.0)
        self.assertGreater(v0, 100.0)                              # diffuse variance
        b, v = kalman_one_step([1.0, 1.0, 1.0, 1.0, 1.0], 0.05, 1.0)
        self.assertGreater(b, 0.0)                                 # tracks the positive cloud
        self.assertGreater(v, 0.0)

    def test_positive_control_drift_is_tracked(self):
        rng = random.Random(401)
        b, drift = 0.0, []
        for _ in range(400):
            b += rng.gauss(0, 0.10)
            drift.append(b + rng.gauss(0, 0.6))
        rows = [(f"2025-{1+i//28:02d}-{1+i%28:02d}", 0.0, x) for i, x in enumerate(drift)]
        res = walk_forward_kalman(rows)
        self.assertGreaterEqual(res["n_test"], 30)
        pt, lo, hi, _ = _paired_bootstrap_ci(res["deltas"])
        self.assertGreater(pt, 0.0)                                # Kalman lower CRPS on real drift

    def test_negative_control_noise_no_manufactured_edge(self):
        rng = random.Random(402)
        noise = [rng.gauss(0, 0.8) for _ in range(400)]
        rows = [(f"2025-{1+i//28:02d}-{1+i%28:02d}", 0.0, x) for i, x in enumerate(noise)]
        res = walk_forward_kalman(rows)
        pt, lo, hi, _ = _paired_bootstrap_ci(res["deltas"])
        self.assertIsNotNone(lo)
        self.assertLessEqual(lo, 0.0)                              # NOT a significant win
        self.assertLess(abs(pt), 0.05)                            # effect negligible
        self.assertLess(res["steady_gain"], 0.6)                  # sluggish on noise

    def test_replay_real_streams_sane_and_well_formed(self):
        """Spec acceptance: leak-free held-out walk-forward on the real logs yields
        a sane single-day update (steady gain < 1.0, not chasing noise) and a
        paired-CRPS delta whose bootstrap CI is computable."""
        for fname in STREAMS:
            path = os.path.join(REPORTS, fname)
            if not os.path.exists(path):
                self.skipTest(f"missing {fname}")
            rows = _load(path)
            self.assertGreaterEqual(len(rows), 30, fname)
            res = walk_forward_kalman(rows)
            self.assertGreater(res["n_test"], 0, fname)
            self.assertEqual(len(res["deltas"]), res["n_test"])
            self.assertGreater(res["q"], 0.0)
            self.assertGreater(res["s"], 0.0)
            # Single-day-update sanity: the steady gain is the fraction of a day's
            # residual the bias moves by; a real drift filter is well below 1.0.
            self.assertLess(res["steady_gain"], 1.0, fname)
            pt, lo, hi, n = _paired_bootstrap_ci(res["deltas"])
            self.assertEqual(n, res["n_test"])
            self.assertIsNotNone(lo)
            self.assertLessEqual(lo, hi)


if __name__ == "__main__":
    unittest.main()
