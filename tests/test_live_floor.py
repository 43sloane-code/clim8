"""Tests for the live-register floor fusion (sources._fuse_live_floor) and the settlement
cross-check (lock_logger.settle_cross_check) — the two 07-04 feed fixes.

Pins the safety contract: fusion can only RAISE the floor; the 24h register counts only when
it exceeds yesterday's max (attribution guard); missing inputs are no-ops; and a settled day
whose banked register floor implies a higher bucket gets a loud divergence warning, never a
silent rewrite."""
import unittest

from weather_council.sources import _fuse_live_floor
from tools.lock_logger import settle_cross_check


class TestFuseLiveFloor(unittest.TestCase):
    def test_current_reading_raises_floor(self):
        floor, note = _fuse_live_floor(30.0, 90.0, None, None)      # 90F = 32.2C > 30.0
        self.assertAlmostEqual(floor, 32.222, places=2)
        self.assertIn("live now 90", note)

    def test_register_counts_only_when_above_yesterday(self):
        # yesterday's max 31.1C (88F); register 92F=33.3C > yesterday -> attributable, raises
        floor, note = _fuse_live_floor(32.2, 90.0, 92.0, 31.1)
        self.assertAlmostEqual(floor, 33.333, places=2)
        self.assertIn("24h-register 92", note)
        # yesterday's max 33.9C (93F); register 92F NOT above it -> may be yesterday's, ignored
        floor, note = _fuse_live_floor(32.2, 90.0, 92.0, 33.9)
        self.assertAlmostEqual(floor, 32.222, places=2)             # only the current reading

    def test_never_lowers_and_none_safe(self):
        floor, note = _fuse_live_floor(33.0, 88.0, 89.0, 30.0)      # both below the floor
        self.assertEqual((floor, note), (33.0, None))
        floor, note = _fuse_live_floor(None, 86.0, None, None)      # no history floor yet
        self.assertAlmostEqual(floor, 30.0, places=1)
        self.assertEqual(_fuse_live_floor(31.0, None, None, None), (31.0, None))
        # register present but yesterday unknown -> unattributable -> ignored (conservative)
        floor, _ = _fuse_live_floor(31.0, None, 95.0, None)
        self.assertEqual(floor, 31.0)


class TestSettleCrossCheck(unittest.TestCase):
    def test_divergence_warns_and_stamps(self):
        rows = [{"target_date": "2026-07-04", "hour": 15, "kind": "sharpened",
                 "running_max_c": 33.3, "settled_bucket": 32, "modal_bucket": 32},
                {"target_date": "2026-07-04", "hour": 14, "kind": "sharpened",
                 "running_max_c": 32.2, "settled_bucket": 32, "modal_bucket": 32}]
        w = settle_cross_check(rows)
        self.assertEqual(len(w), 1)
        self.assertIn("SETTLE DIVERGENCE 2026-07-04", w[0])
        self.assertIn("implies 33", w[0])
        self.assertEqual(rows[0]["register_bucket"], 33)            # stamped, not rewritten
        self.assertEqual(rows[0]["settled_bucket"], 32)             # settlement untouched

    def test_consistent_day_is_silent(self):
        rows = [{"target_date": "2026-07-03", "hour": 15, "kind": "sharpened",
                 "running_max_c": 31.1, "settled_bucket": 31, "modal_bucket": 31}]
        self.assertEqual(settle_cross_check(rows), [])
        self.assertNotIn("register_bucket", rows[0])


if __name__ == "__main__":
    unittest.main()
