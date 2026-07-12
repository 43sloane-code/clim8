"""KAT for weather_council/intraday_tape.py — the persisted read-sequence that makes
endpoint motion, rule-G4 lead sustainment, and the lead-bank rate MECHANICAL instead of
a human judgment across memoryless runs (the 2026-07-12 Karachi failure mode).
"""
from __future__ import annotations

import os
import tempfile
import unittest

from weather_council.intraday_tape import (append_read, cur_f_sustained,
                                           endpoint_motion, lead_bank_rate,
                                           load_reads)


class TestIntradayTape(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.p = os.path.join(self._td.name, "tape.jsonl")

    def tearDown(self):
        self._td.cleanup()

    def _karachi_day(self):
        # The literal 2026-07-12 sequence: endpoint 90°F(n=27) across two runs with a
        # refreshing 91°F cur_f, then the endpoint catches the peak at 91°F(n=34).
        append_read("karachi", "2026-07-12", "13:44Z", endpoint_f=90, endpoint_n=27,
                    cur_f=91, cur_ts="13:30", path=self.p)
        append_read("karachi", "2026-07-12", "13:59Z", endpoint_f=90, endpoint_n=27,
                    cur_f=91, cur_ts="13:55", path=self.p)

    def test_module_self_test(self):
        from weather_council import intraday_tape as m
        m._self_test()

    def test_sustained_lead_needs_refreshing_stamp(self):
        self._karachi_day()
        rows = load_reads("karachi", "2026-07-12", path=self.p)
        self.assertTrue(cur_f_sustained(rows))          # held 91 on distinct v3 stamps
        # London 07-11 stale replay: same cur_f but a FROZEN valid_local -> NOT sustained.
        append_read("london", "2026-07-11", "17:00Z", endpoint_f=88, endpoint_n=20,
                    cur_f=90, cur_ts="16:20", path=self.p)
        append_read("london", "2026-07-11", "17:30Z", endpoint_f=88, endpoint_n=20,
                    cur_f=90, cur_ts="16:20", path=self.p)
        self.assertFalse(cur_f_sustained(load_reads("london", "2026-07-11", path=self.p)))

    def test_single_read_is_never_sustained(self):
        append_read("jeddah", "2026-07-12", "13:00Z", endpoint_f=95, endpoint_n=18,
                    cur_f=96, cur_ts="12:50", path=self.p)
        self.assertFalse(cur_f_sustained(load_reads("jeddah", "2026-07-12", path=self.p)))

    def test_endpoint_motion_rising_blocks_and_stable_permits(self):
        self._karachi_day()
        rows = load_reads("karachi", "2026-07-12", path=self.p)
        self.assertEqual(endpoint_motion(rows), (False, True))    # 90,90
        append_read("karachi", "2026-07-12", "16:40Z", endpoint_f=91, endpoint_n=34,
                    cur_f=91, cur_ts="16:37", path=self.p)
        rows = load_reads("karachi", "2026-07-12", path=self.p)
        self.assertEqual(endpoint_motion(rows), (True, False))    # 90→91: RISING
        # A single defined read can neither rise nor be called stable.
        self.assertEqual(endpoint_motion(rows[:1]), (False, False))

    def test_lead_bank_rate_scores_only_completed_days(self):
        self._karachi_day()
        append_read("karachi", "2026-07-12", "16:40Z", endpoint_f=91, endpoint_n=34,
                    cur_f=91, cur_ts="16:37", path=self.p)        # lead banked
        append_read("london", "2026-07-11", "17:00Z", endpoint_f=88, endpoint_n=20,
                    cur_f=90, cur_ts="16:20", path=self.p)        # lead never banked
        self.assertEqual(lead_bank_rate(self.p, before_date="2026-07-13"), (1, 2))
        self.assertEqual(lead_bank_rate(self.p, before_date="2026-07-13",
                                        city="karachi"), (1, 1))
        # The open day (>= before_date) is excluded — an unsettled day cannot be scored.
        self.assertEqual(lead_bank_rate(self.p, before_date="2026-07-12"), (0, 1))

    def test_missing_file_and_foreign_rows_degrade_clean(self):
        self.assertEqual(load_reads("karachi", "2026-07-12", path=self.p), [])
        self.assertEqual(lead_bank_rate(self.p, before_date="2026-07-13"), (0, 0))
        self._karachi_day()
        self.assertEqual(load_reads("singapore", "2026-07-12", path=self.p), [])

    def test_nothing_observed_is_not_recorded(self):
        append_read("karachi", "2026-07-12", "06:00Z", endpoint_f=None, endpoint_n=None,
                    cur_f=None, cur_ts=None, path=self.p)
        self.assertEqual(load_reads("karachi", "2026-07-12", path=self.p), [])


if __name__ == "__main__":
    unittest.main()
