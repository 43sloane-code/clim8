"""Network-free tests for the C7 realized-outcome edge scoring and settlement (edge.py / storage.py).

Stdlib unittest only. Run with:
    PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import datetime as dt
import math
import random
import statistics as st
import unittest

from weather_council import scoring
from weather_council.scoring import crps_sample, crps_gaussian, interval_coverage, quantile, pit
from weather_council.compare import residual_calibration, compare_high, MIN_RESIDUALS
from weather_council.market import WeatherMarket, MarketBucket
from weather_council.agents import Vote, MemberSpec, Skill
from weather_council.council import Council


class TestC7EdgeScoring(unittest.TestCase):
    """C7 realized-outcome scorer: strictly-proper scores and the edge gate."""

    def _snap(self, realized, council, market):
        """One settled snapshot. council/market are {label: prob} dicts; the
        bucket ladder is their shared keys."""
        labels = list(council.keys())
        return {
            "place": "Testville", "target_date": "2026-06-01",
            "realized_label": realized,
            "buckets": [{"label": b, "lo": None, "hi": None,
                         "model_prob": council[b], "market_prob": market[b]}
                        for b in labels],
        }

    def test_brier_and_logloss_values(self):
        from weather_council.edge import _brier, _logloss
        # Perfect forecast: Brier 0, log loss 0.
        probs = {"A": 1.0, "B": 0.0}
        self.assertAlmostEqual(_brier(probs, ["A", "B"], "A"), 0.0)
        self.assertAlmostEqual(_logloss(probs, "A"), 0.0)
        # Even forecast over two buckets: Brier 0.5, log loss ln 2.
        even = {"A": 0.5, "B": 0.5}
        self.assertAlmostEqual(_brier(even, ["A", "B"], "A"), 0.5)
        self.assertAlmostEqual(_logloss(even, "A"), math.log(2))

    def test_missing_bucket_prob_scored_as_zero(self):
        from weather_council.edge import _brier, _logloss, EPS
        probs = {"A": 0.7}                      # B unpriced
        # B realized: Brier counts 0.7^2 (A) + 1^2 (B as 0) = 1.49.
        self.assertAlmostEqual(_brier(probs, ["A", "B"], "B"), 0.7 ** 2 + 1.0)
        # Log loss on the unpriced realized bucket uses the EPS floor, not +inf.
        self.assertAlmostEqual(_logloss(probs, "B"), -math.log(EPS))

    def test_score_snapshot_maps_realized(self):
        from weather_council.edge import score_snapshot
        s = score_snapshot(self._snap("A", {"A": 0.8, "B": 0.2},
                                      {"A": 0.5, "B": 0.5}))
        self.assertEqual(s.realized_label, "A")
        self.assertAlmostEqual(s.council_p_realized, 0.8)
        self.assertAlmostEqual(s.market_p_realized, 0.5)
        self.assertLess(s.council_logloss, s.market_logloss)   # council sharper here

    def test_unsettled_or_offladder_snapshot_skipped(self):
        from weather_council.edge import score_snapshot
        self.assertIsNone(score_snapshot(
            {"realized_label": None, "buckets": [{"label": "A"}]}))
        self.assertIsNone(score_snapshot(
            {"realized_label": "Z",                 # outside the ladder
             "buckets": [{"label": "A", "model_prob": 1.0, "market_prob": 1.0}]}))

    def test_edge_unvalidated_below_min_settled(self):
        from weather_council.edge import score_snapshots, MIN_SETTLED
        # Council strictly better, but too few days to certify.
        snaps = [self._snap("A", {"A": 0.9, "B": 0.1}, {"A": 0.6, "B": 0.4})
                 for _ in range(MIN_SETTLED - 1)]
        r = score_snapshots(snaps)
        self.assertFalse(r.is_edge_validated)
        self.assertIn("not enough", r.note)
        self.assertEqual(r.n, MIN_SETTLED - 1)

    def test_edge_validated_when_council_dominates(self):
        from weather_council.edge import score_snapshots, MIN_SETTLED
        # Council always sharper toward the realized bucket than the market, on a
        # comfortable margin and enough days — the CI on the gain must clear zero.
        snaps = [self._snap("A", {"A": 0.85, "B": 0.15}, {"A": 0.55, "B": 0.45})
                 for _ in range(MIN_SETTLED)]
        r = score_snapshots(snaps)
        self.assertTrue(r.is_edge_validated)
        self.assertLess(r.council_logloss, r.market_logloss)
        self.assertLess(r.council_brier, r.market_brier)
        self.assertIsNotNone(r.logloss_diff_ci)
        self.assertGreater(r.logloss_diff_ci[0], 0)            # CI excludes zero
        self.assertIn("VALIDATED", r.note)

    def test_no_edge_when_market_wins(self):
        from weather_council.edge import score_snapshots, MIN_SETTLED
        # Market is the sharper forecaster — no edge regardless of n.
        snaps = [self._snap("A", {"A": 0.55, "B": 0.45}, {"A": 0.85, "B": 0.15})
                 for _ in range(MIN_SETTLED)]
        r = score_snapshots(snaps)
        self.assertFalse(r.is_edge_validated)
        self.assertIn("no edge", r.note)

    def test_empty_report_is_honest(self):
        from weather_council.edge import score_snapshots
        r = score_snapshots([])
        self.assertEqual(r.n, 0)
        self.assertFalse(r.is_edge_validated)
        self.assertIsNone(r.council_brier)

    def test_bootstrap_ci_is_seed_reproducible(self):
        from weather_council.edge import _bootstrap_ci, BOOTSTRAP_SEED
        diffs = [0.1, 0.2, -0.05, 0.3, 0.15, 0.0, 0.25, -0.1, 0.2, 0.05]
        a = _bootstrap_ci(diffs, 2000, BOOTSTRAP_SEED)
        b = _bootstrap_ci(diffs, 2000, BOOTSTRAP_SEED)
        self.assertEqual(a, b)                                  # deterministic
        self.assertLessEqual(a[0], a[1])

class TestC7Settlement(unittest.TestCase):
    """The snapshot ledger settles realized buckets against the verdict's anchor
    station (the record the market pays out on), not a face-value reading."""

    def test_bucket_for_reading_respects_open_tails(self):
        from weather_council.storage import _bucket_for_reading
        ladder = [{"label": "18 or below", "lo": None, "hi": 18},
                  {"label": "19", "lo": 19, "hi": 19},
                  {"label": "20", "lo": 20, "hi": 20},
                  {"label": "21 or above", "lo": 21, "hi": None}]
        self.assertEqual(_bucket_for_reading(ladder, 17), "18 or below")
        self.assertEqual(_bucket_for_reading(ladder, 19), "19")
        self.assertEqual(_bucket_for_reading(ladder, 25), "21 or above")

    def test_roundtrip_settles_against_anchor_station(self):
        import tempfile, types, os
        from pathlib import Path
        from weather_council import storage

        # Isolate the ledger in a temp DB so the real verdicts.db is untouched.
        tmp = Path(tempfile.mkdtemp()) / "c7.db"
        orig = storage.DB_PATH
        storage.DB_PATH = tmp
        try:
            place = types.SimpleNamespace(
                latitude=22.3, longitude=114.2,
                label=lambda: "Hong Kong, HK")
            verdict = types.SimpleNamespace(
                place=place, target="2026-06-01",
                truth_source={"kind": "station", "station": {"id": "HKO"}})
            bucket = types.SimpleNamespace
            comparison = types.SimpleNamespace(
                market_title="Highest temperature in Hong Kong",
                grain="C",
                buckets=[bucket(label="30 or below", lo=None, hi=30,
                                model_prob=0.3, market_prob=0.4),
                         bucket(label="31", lo=31, hi=31,
                                model_prob=0.5, market_prob=0.35),
                         bucket(label="32 or above", lo=32, hi=None,
                                model_prob=0.2, market_prob=0.25)])
            storage.log_market_snapshot(verdict, comparison)

            # Anchor station reports a 31.2 °C high -> native reading 31 -> "31".
            fake_sources = types.SimpleNamespace(
                fetch_station_daily=lambda st: {"2026-06-01": (31.2, 24.0)})
            settled = storage.settle_market_snapshots(fake_sources)
            self.assertEqual(len(settled), 1)

            snaps = storage.fetch_settled_snapshots()
            self.assertEqual(len(snaps), 1)
            self.assertEqual(snaps[0]["realized_label"], "31")
            self.assertEqual(snaps[0]["place"], "Hong Kong, HK")
            self.assertEqual(len(snaps[0]["buckets"]), 3)

            # The settled snapshot flows straight into the C7 scorer.
            from weather_council.edge import score_snapshot
            s = score_snapshot(snaps[0])
            self.assertEqual(s.realized_label, "31")
            self.assertAlmostEqual(s.council_p_realized, 0.5)
            self.assertAlmostEqual(s.market_p_realized, 0.35)
        finally:
            storage.DB_PATH = orig
            try:
                os.remove(tmp)
            except OSError:
                pass

if __name__ == "__main__":
    unittest.main()
