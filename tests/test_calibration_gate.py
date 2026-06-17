"""Network-free tests for candidate 43 — the healthcheck-v2 CALIBRATION gate.

Proves the four load-bearing claims of the gate, with deterministic oracles and a
REPLAY on the real logged streams (leak-free trailing-residual PIT, identical
construction to daily_healthcheck._walk_forward):

  * the no-scipy chi-square survival function matches published Χ² table values;
  * a calibrated (uniform) PIT stream is GREEN, an under-dispersed large stream is
    RED-and-blocks, the same shape on a small sample is AMBER-and-does-not-block,
    and a verification-log gap forces RED;
  * Bröcker & Smith consistency bars bracket the calibrated bin expectation;
  * the RED tier wires into candidate 42's contract as a hard refusal that emits
    NO bucket probabilities.

Stdlib unittest only. Run with:
    PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import csv
import datetime as dt
import os
import random
import unittest

from weather_council.calibration_gate import (
    chisq_sf, pit_histogram, pit_flatness_test, consistency_bars,
    log_gaps, calibration_tier, MAX_GAP_DAYS, DEFAULT_BINS, _self_test,
)
from weather_council.scoring import pit
from weather_council.bucket_contract import daily_contract

REPORTS = os.path.join(os.path.dirname(__file__), "..", "reports")
STREAMS = ("london_high.csv", "london_low.csv",
           "hong_kong_high.csv", "hong_kong_low.csv")
WARMUP = 10


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


def _leakfree_pits(rows, window):
    """The runner's leak-free PIT construction: each of the last `window` days'
    residual scored through ONLY strictly-earlier residuals."""
    resid = [rz - pt for _, pt, rz in rows]
    dates = [d for d, _, _ in rows]
    start = max(WARMUP, len(rows) - window)
    pits, win_dates = [], []
    for i in range(start, len(rows)):
        prior = resid[:i]
        if len(prior) >= WARMUP:
            pits.append(pit(prior, resid[i]))
            win_dates.append(dates[i])
    return pits, win_dates


class TestCalibrationGate(unittest.TestCase):
    def test_module_self_test(self):
        _self_test()

    def test_chisq_sf_matches_tables(self):
        # SF(0)=1; median of Χ²_1 ≈ 0.4549 -> SF ≈ 0.5; 95th pct of Χ²_5 ≈ 11.07.
        self.assertEqual(chisq_sf(0.0, 5), 1.0)
        self.assertAlmostEqual(chisq_sf(0.4549, 1), 0.5, places=3)
        self.assertAlmostEqual(chisq_sf(11.07, 5), 0.05, places=2)
        # Monotone non-increasing in the statistic.
        self.assertGreater(chisq_sf(1.0, 3), chisq_sf(5.0, 3))

    def test_histogram_and_flatness_shape(self):
        rng = random.Random(43)
        pits = [rng.random() for _ in range(500)]
        hist = pit_histogram(pits, DEFAULT_BINS)
        self.assertEqual(sum(hist), 500)
        self.assertEqual(len(hist), DEFAULT_BINS)
        flat = pit_flatness_test(pits, DEFAULT_BINS)
        self.assertEqual(flat["n"], 500)
        self.assertGreater(flat["pvalue"], 0.05)        # uniform => not rejected

    def test_tiers_uniform_underdispersed_small_and_gap(self):
        rng = random.Random(43)
        dates = [(dt.date(2025, 1, 1) + dt.timedelta(days=i)).isoformat()
                 for i in range(400)]

        # Calibrated, large, dense => GREEN, clear to emit.
        green = calibration_tier([rng.random() for _ in range(400)], dates)
        self.assertEqual(green["tier"], "GREEN")
        self.assertFalse(green["blocks_emit"])

        # Under-dispersed (mass at 0/1), large => RED, blocks emit.
        u = [(0.0 if rng.random() < 0.5 else 1.0) + rng.uniform(-0.08, 0.08)
             for _ in range(400)]
        u = [min(0.999, max(0.001, p)) for p in u]
        red = calibration_tier(u, dates)
        self.assertEqual(red["tier"], "RED")
        self.assertTrue(red["blocks_emit"])

        # Same miscalibration on a SMALL sample => AMBER, does not block, and the
        # recommended fix is parametric (never isotonic at that size).
        amber = calibration_tier(u[:40], dates[:40])
        self.assertEqual(amber["tier"], "AMBER")
        self.assertFalse(amber["blocks_emit"])
        self.assertIn("beta/Platt", amber["recalibration"])

        # A log gap > MAX_GAP_DAYS forces RED even with a flat PIT.
        gapped = dates[:50] + [(dt.date(2025, 1, 1) + dt.timedelta(days=60 + i)).isoformat()
                               for i in range(350)]
        g = calibration_tier([rng.random() for _ in range(400)], gapped)
        self.assertEqual(g["tier"], "RED")
        self.assertTrue(any("gap" in r for r in g["reasons"]))

    def test_consistency_bars_bracket_expectation(self):
        lo, hi = consistency_bars(400, 10, 0.05)        # E = 40 per bin
        self.assertLess(lo, 40)
        self.assertGreater(hi, 40)
        self.assertEqual(consistency_bars(0, 10, 0.05), (0, 0))

    def test_log_gaps_detector(self):
        dense = [(dt.date(2025, 1, 1) + dt.timedelta(days=i)).isoformat() for i in range(10)]
        self.assertEqual(log_gaps(dense, MAX_GAP_DAYS), [])
        sparse = ["2025-01-01", "2025-01-02", "2025-01-09"]   # 7-day jump
        gaps = log_gaps(sparse, MAX_GAP_DAYS)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0][2], 7)

    def test_red_tier_blocks_contract_emission(self):
        """The candidate-43 acceptance hook: a RED tier, fed to candidate 42's
        contract, refuses and emits NO bucket probabilities."""
        c = daily_contract("HK high", "2026-06-10", 28.9, 1.2,
                           calibration_red=True, calibration_reason="p=0.004 on n=240")
        self.assertTrue(c["refusal"])
        self.assertEqual(c["buckets"], {})
        self.assertTrue(c["refusal_reason"].startswith("REFUSED: calibration"))
        # Non-RED on the same central still serves a real pmf.
        ok = daily_contract("HK high", "2026-06-10", 28.9, 1.2)
        self.assertFalse(ok["refusal"])
        self.assertAlmostEqual(sum(ok["buckets"].values()), 1.0, places=3)

    def test_replay_real_streams_produce_a_tier(self):
        """Spec acceptance for candidate 43: replaying the last 60 logged days of
        each stream produces a valid tier with a histogram and consistency bar."""
        for fname in STREAMS:
            path = os.path.join(REPORTS, fname)
            if not os.path.exists(path):
                self.skipTest(f"missing {fname}")
            rows = _load(path)
            self.assertGreaterEqual(len(rows), 30, fname)
            pits, win_dates = _leakfree_pits(rows, 60)
            info = calibration_tier(pits, win_dates)
            self.assertIn(info["tier"], ("GREEN", "AMBER", "RED"))
            self.assertEqual(len(info["histogram"]), DEFAULT_BINS)
            self.assertEqual(sum(info["histogram"]), len(pits))
            lo, hi = info["consistency_bar"]
            self.assertLessEqual(lo, hi)
            # blocks_emit is exactly the RED predicate.
            self.assertEqual(info["blocks_emit"], info["tier"] == "RED")


if __name__ == "__main__":
    unittest.main()
