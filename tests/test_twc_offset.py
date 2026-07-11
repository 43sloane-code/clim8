"""KAT for the signed-offset estimator (weather_council/twc_offset.py, Plan 4 Phase 3).

The mathematical core — "which way, how much, how sure." Pins, in order of what would hurt most:
  * the THREE-GATE certification — a direction (ABOVE/BELOW) is asserted ONLY when n≥20 AND the
    binomial sign test is significant AND the bootstrap CI on the median excludes zero; a clean
    signal at n=19 is still UNMEASURED (the n gate dominates), and enough-but-null data is NEUTRAL;
  * the offset sign convention — TWC − actual; positive median ⇒ TWC runs ABOVE the oracle;
  * the median is robust to a single busted day (why it is primary, not the mean);
  * ties (|offset|≤EPS) are excluded from the sign test and counted separately;
  * the paired MAE delta says whether TWC is USEFUL, not merely biased;
  * the seeded bootstrap is deterministic; end-to-end estimate_offsets reads settled rows only,
    per (place, attr), low graded on actual_low.
Network-free; isolated temp DB.

Run with:  PYTHONPATH=. python3 -m unittest tests.test_twc_offset -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weather_council import storage, twc_offset
from weather_council.twc_offset import build_estimate, estimate_offsets, MIN_N, ALPHA


class TestBuildEstimate(unittest.TestCase):
    def test_module_selftest(self):
        twc_offset._selftest()

    def test_empty_is_unmeasured(self):
        e = build_estimate("X", "high", [])
        self.assertEqual(e.direction, "UNMEASURED")
        self.assertEqual(e.n, 0)
        self.assertFalse(e.is_certified)

    def test_nineteen_days_clean_bias_still_unmeasured(self):
        e = build_estimate("X", "high", [1.3] * 19)     # perfectly clean, but n<MIN_N
        self.assertEqual(e.n, 19)
        self.assertEqual(e.direction, "UNMEASURED")     # the n gate dominates the signal

    def test_above_when_all_three_gates_pass(self):
        planted = [1.3 + 0.1 * ((i % 5) - 2) for i in range(25)]     # 1.1..1.5, all positive
        e = build_estimate("X", "high", planted)
        self.assertEqual(e.direction, "ABOVE")
        self.assertGreater(e.ci_95[0], 0)               # CI excludes zero on the positive side
        self.assertLess(e.sign_test_p, ALPHA)
        self.assertAlmostEqual(e.median_offset, 1.3, delta=0.2)
        self.assertTrue(e.is_certified)

    def test_below_for_negative_bias(self):
        e = build_estimate("X", "low", [-0.9 + 0.1 * ((i % 5) - 2) for i in range(22)])
        self.assertEqual(e.direction, "BELOW")
        self.assertLess(e.median_offset, 0)
        self.assertLess(e.ci_95[1], 0)                  # CI excludes zero on the negative side

    def test_neutral_when_enough_data_but_no_bias(self):
        noise = [(-1) ** i * (0.4 + 0.05 * (i % 3)) for i in range(40)]
        e = build_estimate("X", "high", noise)
        self.assertGreaterEqual(e.n, MIN_N)
        self.assertEqual(e.direction, "NEUTRAL")        # measured, but no detectable direction
        self.assertLessEqual(e.ci_95[0], 0)
        self.assertGreaterEqual(e.ci_95[1], 0)
        self.assertFalse(e.is_certified)

    def test_sign_test_significance(self):
        allpos = build_estimate("X", "high", [0.5 + 0.1 * (i % 3) for i in range(20)])
        self.assertLess(allpos.sign_test_p, ALPHA)
        balanced = build_estimate("X", "high", [(-1) ** i * 0.5 for i in range(20)])
        self.assertGreater(balanced.sign_test_p, ALPHA)

    def test_ties_excluded_and_counted(self):
        offs = [1.0, 1.0, 1.0, 0.0, 0.0, -1.0]          # 3 above, 1 below, 2 ties
        e = build_estimate("X", "high", offs)
        self.assertEqual((e.n_above, e.n_below, e.n_ties), (3, 1, 2))

    def test_median_robust_to_one_busted_day(self):
        clean = [1.2] * 21
        busted = clean + [-50.0]                          # one wild outlier
        e_clean = build_estimate("X", "high", clean)
        e_busted = build_estimate("X", "high", busted)
        self.assertEqual(e_clean.direction, "ABOVE")
        # the median barely moves; the mean would be dragged negative
        self.assertAlmostEqual(e_busted.median_offset, 1.2, delta=0.1)
        self.assertLess(e_busted.mean_offset, e_busted.median_offset)

    def test_paired_mae_delta_sign(self):
        off = [0.2 * ((i % 3) - 1) for i in range(25)]   # small TWC errors
        twc_better = build_estimate("X", "high", off, [1.0] * 25)
        self.assertGreater(twc_better.paired_mae_delta, 0)      # council worse -> TWC better
        council_better = build_estimate("X", "high", [1.0 * ((i % 2) * 2 - 1) for i in range(25)],
                                        [0.1] * 25)
        self.assertLess(council_better.paired_mae_delta, 0)

    def test_bootstrap_ci_deterministic(self):
        planted = [1.3 + 0.1 * ((i % 5) - 2) for i in range(25)]
        self.assertEqual(build_estimate("X", "high", planted).ci_95,
                         build_estimate("X", "high", planted).ci_95)

    def test_label_formats(self):
        cert = build_estimate("X", "high", [1.3 + 0.1 * ((i % 5) - 2) for i in range(25)])
        self.assertIn("ABOVE, median +1.3", cert.label())
        unm = build_estimate("X", "high", [1.3] * 5)
        self.assertEqual(unm.label(), "UNMEASURED (n=5)")

    def test_sf_grain_is_fahrenheit(self):
        e = build_estimate("San Francisco, United States", "high", [1.0] * 22, grain="F")
        self.assertEqual(e.grain, "F")


class TestEstimateOffsetsEndToEnd(unittest.TestCase):
    def setUp(self):
        self._dir = Path(tempfile.mkdtemp())
        self._orig = storage.DB_PATH
        storage.DB_PATH = self._dir / "t.db"
        storage._connect().close()

    def tearDown(self):
        storage.DB_PATH = self._orig

    def _insert(self, place, td, fh, fl, ch, cl, ah, al):
        conn = storage._connect()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO tracked_forecasts "
                "(source, issued_at, place, target_date, fc_high, fc_low, council_high, "
                " council_low, actual_high, actual_low) VALUES ('twc','t',?,?,?,?,?,?,?,?)",
                (place, td, fh, fl, ch, cl, ah, al))
        conn.close()

    def test_reads_settled_rows_and_certifies_direction(self):
        # 22 settled days, TWC high = actual + 1.2 (ABOVE); low = actual − 0.6 (BELOW).
        for i in range(22):
            ah, al = 30.0 + (i % 3) * 0.1, 25.0
            self._insert("Singapore, Singapore", f"2026-05-{i+1:02d}",
                         ah + 1.2, al - 0.6, ah + 0.3, al, ah, al)
        by = {(e.place, e.attr): e for e in estimate_offsets("twc")}
        self.assertEqual(by[("Singapore, Singapore", "high")].direction, "ABOVE")
        self.assertEqual(by[("Singapore, Singapore", "low")].direction, "BELOW")

    def test_unsettled_rows_excluded(self):
        self._insert("Manila, Philippines", "2026-05-01", 31.0, 25.0, 30.0, 24.0, None, None)
        self.assertEqual(estimate_offsets("twc"), [])   # actual_high NULL -> not counted


if __name__ == "__main__":
    unittest.main()
