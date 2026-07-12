"""KAT for weather_council/intraday_grade.py — the vocabulary-grade gate for which the
2026-07-12 Karachi miss (called '32 locked', settled 33) is the regression test.

unittest.TestCase (the repo gate is `python3 -m unittest discover`; the first version of
this file was pytest-style and silently ran ZERO tests — that itself is the regression this
header guards against).

Labels-only module: these tests assert GRADE and VOCABULARY, never a served number.
"""
from __future__ import annotations

import datetime as dt
import unittest

from weather_council.intraday_ceiling import (IntradayCeiling,
                                              peak_close_hour_from_history)
from weather_council.intraday_grade import (Grade, grade_lines, intraday_grade,
                                            peak_window_closed, sunset_local_hour)


def _ceil(banked_c, run_c, cur_f=None, ep_f=None, ep_n=None, state="holding",
          grain="C", pch=None):
    return IntradayCeiling(
        kind="sharpened", city="X", target="2026-07-12", sub_degree=False, grain=grain,
        hour=14, running_max_c=run_c, banked_running_max_c=banked_c,
        live_cur_f=cur_f, wu_daily_max_f=ep_f, wu_daily_max_n=ep_n,
        day_state=state, peak_close_hour=pch)


class TestGradeClassification(unittest.TestCase):

    def test_module_self_test(self):
        from weather_council import intraday_grade as m
        m._self_test()   # raises on any regression

    def test_karachi_lead_is_live_coinflip_never_lockable(self):
        # Banked endpoint 90F (32), cur_f 91F leads to 33, sustained on the tape. The read
        # is a live 32/33 coin-flip: both buckets named, not lockable, not dismissed.
        g = intraday_grade(_ceil(32.22, 32.78, cur_f=91, ep_f=90, ep_n=27, pch=13),
                           hour=14, endpoint_stable=True, lead_sustained=True)
        self.assertEqual(g.name, "leading_coinflip")
        self.assertEqual(g.coin_flip, (32, 33))
        self.assertFalse(g.may_say_locked)
        txt = " ".join(grade_lines(g))
        self.assertIn("SUSTAINED", txt)
        self.assertIn("NOT a probable over-read", txt)
        self.assertIn("banked", txt)
        self.assertIn("LEADING", txt)

    def test_single_read_lead_neither_banked_nor_dismissed(self):
        g = intraday_grade(_ceil(32.22, 32.78, cur_f=91, ep_f=90),
                           hour=14, lead_sustained=False)
        txt = " ".join(grade_lines(g))
        self.assertIn("SINGLE-READ", txt)
        self.assertIn("do not dismiss", txt.lower())
        self.assertFalse(g.may_say_locked)

    def test_rising_endpoint_blocks_lock_even_when_obs_declining(self):
        # The exact Karachi failure state: obs look declining, but the settlement
        # endpoint's max_f rose 90 -> 91F across reads. Rising HARD-BLOCKS 'locked'.
        g = intraday_grade(_ceil(32.78, 32.78, ep_f=91, ep_n=34, state="declining", pch=14),
                           hour=16, endpoint_rising=True)
        self.assertTrue(g.endpoint_rising)
        self.assertFalse(g.may_say_locked)
        self.assertEqual(g.name, "declining_provisional")
        self.assertIn("STILL RISING", " ".join(grade_lines(g, backtest_prob=0.96)))

    def test_mechanical_final_requires_every_condition(self):
        base = dict(hour=18, endpoint_stable=True)
        ok = intraday_grade(_ceil(32.22, 32.22, ep_f=90, state="declining", pch=14), **base)
        self.assertEqual(ok.name, "final")
        self.assertTrue(ok.may_say_locked)
        self.assertIn("LOCK (final)", " ".join(grade_lines(ok)))
        # Each missing condition demotes the grade:
        self.assertFalse(intraday_grade(_ceil(32.22, 32.22, state="declining", pch=14),
                                        hour=18).may_say_locked)               # not stable
        self.assertFalse(intraday_grade(_ceil(32.22, 32.22, state="declining"),
                                        **base).may_say_locked)                # pch unknown
        self.assertFalse(intraday_grade(_ceil(32.22, 32.22, state="declining", pch=14),
                                        hour=18, endpoint_stable=True,
                                        endpoint_rising=True).may_say_locked)  # rising
        self.assertFalse(intraday_grade(_ceil(32.22, 32.22, state="holding", pch=14),
                                        **base).may_say_locked)                # holding

    def test_holding_never_locks(self):
        g = intraday_grade(_ceil(32.22, 32.22, ep_f=90, state="holding", pch=14),
                           hour=18, endpoint_stable=True)
        self.assertEqual(g.name, "holding_provisional")
        self.assertFalse(g.may_say_locked)

    def test_post_sunset_unbanks_a_failed_lead(self):
        # Jeddah-shape: banked 35, cur_f 96F led to 36 but the endpoint never caught up.
        # Post-sunset the settlement is the BANKED 35 and the render says the lead failed.
        g = intraday_grade(_ceil(35.0, 35.56, cur_f=96, ep_f=95, state="declining"),
                           hour=20, post_sunset=True)
        self.assertEqual((g.name, g.banked_bucket, g.lead_failed), ("final", 35, True))
        self.assertIn("never banked", " ".join(grade_lines(g)))

    def test_settling_endpoint_headlines_every_block(self):
        # H2: the record that pays is quoted (value + n) before any grade line.
        g = intraday_grade(_ceil(32.22, 32.22, ep_f=90, ep_n=27, state="holding"), hour=14)
        first = grade_lines(g)[0]
        self.assertIn("settling surface", first)
        self.assertIn("90°F", first)
        self.assertIn("n=27", first)

    def test_grain_aware_render_for_san_francisco(self):
        g = intraday_grade(_ceil(21.11, 21.11, grain="F"), hour=12)   # 21.11°C = 70°F
        self.assertEqual((g.unit, g.banked_bucket), ("°F", 70))
        self.assertIn("70°F", " ".join(grade_lines(g)))

    def test_measured_lead_bank_rate_replaces_anecdotes(self):
        g = intraday_grade(_ceil(32.22, 32.78, cur_f=91, ep_f=90), hour=14,
                           lead_sustained=True, lead_bank_stat=(3, 4))
        self.assertIn("3/4 past uncorroborated leads", " ".join(grade_lines(g)))

    def test_no_live_fusion_falls_back_to_plain_banked(self):
        # Replay path: no split (banked_running_max_c None) -> banked from running_max.
        c = IntradayCeiling(kind="sharpened", city="X", target="2026-07-12",
                            sub_degree=False, grain="C", hour=14, running_max_c=32.0,
                            day_state="holding")
        g = intraday_grade(c, hour=14)
        self.assertEqual((g.name, g.banked_bucket), ("holding_provisional", 32))
        self.assertIsNone(g.coin_flip)
        self.assertFalse(g.may_say_locked)


class TestMechanicalInputs(unittest.TestCase):

    def test_peak_close_hour_is_data_derived_and_leak_free(self):
        hist = {f"d{i}": [(9, 20.0), (12, 24.0), (13, 26.0), (16, 25.0)]
                for i in range(10)}
        self.assertEqual(peak_close_hour_from_history(hist, q=0.95), 13)
        self.assertTrue(peak_window_closed(14, 13))
        self.assertFalse(peak_window_closed(13, 13))
        self.assertFalse(peak_window_closed(20, None))   # unknown clock never locks

    def test_sunset_matches_certified_lock_clocks(self):
        # Singapore Changi July sunset ~19:15 SGT (certified FINAL clock ~19:10);
        # London City ~21:15 BST in mid-July; Jeddah ~19:05 AST.
        self.assertAlmostEqual(
            sunset_local_hour(1.35, 103.99, dt.date(2026, 7, 12), 8.0), 19.25, delta=0.25)
        self.assertAlmostEqual(
            sunset_local_hour(51.505, 0.055, dt.date(2026, 7, 12), 1.0), 21.25, delta=0.25)
        self.assertAlmostEqual(
            sunset_local_hour(21.68, 39.15, dt.date(2026, 7, 12), 3.0), 19.1, delta=0.35)


if __name__ == "__main__":
    unittest.main()
