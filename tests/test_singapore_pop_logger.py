"""Tests for the Singapore PoP point-in-time logger (tools/singapore_pop_logger).

Verifies the pure daytime-PoP extraction: TWC daypart is day/night-interleaved, so calendar-day k
maps to daypart index 2k; a past/absent daytime part yields None; the frozen 40% threshold tags
DRY/CONVECTIVE. Network fetch and disk write are not exercised."""
import unittest

from tools.singapore_pop_logger import _pick_pop, THRESHOLD, _selftest


class TestSingaporePopLogger(unittest.TestCase):
    def test_daytime_index_is_2k(self):
        valid = ["2026-07-02T07:00:00+0800", "2026-07-03T07:00:00+0800"]
        dpc = [None, 13, 62, 39]            # today-day (past), tonight, tomorrow-day, tomorrow-night
        dqpf = [None, 0.0, 0.35, 0.1]
        self.assertEqual(_pick_pop(valid, dpc, dqpf, "2026-07-03"), (62.0, 0.35))  # k=1 -> index 2

    def test_past_and_absent_guard_to_none(self):
        valid = ["2026-07-02T07:00:00+0800"]
        self.assertIsNone(_pick_pop(valid, [None, 13], [None, 0.0], "2026-07-02"))  # index 0 = None
        self.assertIsNone(_pick_pop(valid, [None, 13], [None, 0.0], "2026-07-09"))  # absent
        self.assertIsNone(_pick_pop([], [50], [0.1], "2026-07-02"))                 # empty

    def test_threshold_frozen_at_40(self):
        self.assertEqual(THRESHOLD, 40.0)     # pre-registered; changing it is a documented breakpoint

    def test_selftest_passes(self):
        self.assertEqual(_selftest(), 0)


if __name__ == "__main__":
    unittest.main()
