"""KAT for tools/probe_sf_cli_scale.py — the frozen, pre-registered, one-attempt
probe of the SF CLI-scale seam shift (ledger/preregistered/
sf_cli_scale_intraday_pmf.md, frozen 2026-07-27 BEFORE scoring). Pins the
bucket/quantize known answers, the arm-B-constructed-to-win gate PASS, the
zero-seam zero-delta DEAD, and the leak-freeness wiring (day D's row never
touches day D's cell) so the scored artifact can't silently rot.

unittest.TestCase (pytest-style bare functions run ZERO tests under the repo
gate)."""
import unittest

from tools.probe_sf_cli_scale import (LOG_FLOOR, MIN_HISTORY_DAYS,
                                      _mk_day, _selftest, argmax_bucket,
                                      bucket_pmf, evaluate, market_bucket,
                                      probe_cells, score_cell, sharpened_pmf)
from tools.mc_verdict_sim import SERVED_HOURS


class TestProbeMath(unittest.TestCase):
    def test_module_self_test(self):
        self.assertEqual(_selftest(), 0)

    def test_market_bucket_known_answers(self):
        self.assertEqual(market_bucket(69), 68)
        self.assertEqual(market_bucket(70), 70)
        self.assertEqual(market_bucket(71.9), 70)
        self.assertEqual(market_bucket(68.0), 68)

    def test_sharpened_pmf_resample_and_shift(self):
        pmf, rm_back = sharpened_pmf(60.4, [0.0, 0.2, 1.0])
        self.assertEqual(pmf, {60: 1 / 3, 61: 2 / 3})
        pmf_s, rm_back_s = sharpened_pmf(60.4, [0.0, 0.2, 1.0], shift_f=1.0)
        self.assertEqual(pmf_s, {61: 1 / 3, 62: 2 / 3})
        # C3: the banked running max comes back untouched by the shift.
        self.assertEqual((rm_back, rm_back_s), (60.4, 60.4))

    def test_argmax_tie_break_deterministic(self):
        self.assertEqual(argmax_bucket({68: 0.4, 70: 0.4, 72: 0.2}), 68)

    def test_score_cell_log_floor(self):
        hit, ls = score_cell({68: 1.0}, 62.0)
        import math
        self.assertFalse(hit)
        self.assertEqual(ls, math.log(LOG_FLOOR))
        hit, ls = score_cell(bucket_pmf({62: 0.5, 63: 0.5}), 62.0)
        self.assertTrue(hit)
        self.assertEqual(ls, 0.0)


class TestGateKnownAnswers(unittest.TestCase):
    def test_arm_b_constructed_to_win_passes(self):
        # rise 0 / catch +2 on every day: arm A serves bucket 60, arm B
        # serves bucket 62 = the actual CLI bucket, both halves.
        days = [_mk_day(f"2026-01-{i:02d}", 60.0, 60.0, 62.0)
                for i in range(1, 41)]
        cells, skipped = probe_cells(days)
        self.assertEqual(skipped, MIN_HISTORY_DAYS)
        self.assertEqual(len(cells), 10 * len(SERVED_HOURS))
        res = evaluate(cells, days)
        self.assertEqual(res["gate"]["verdict"], "PASS")
        self.assertEqual(res["pooled"]["arm_B"]["bucket_hit_rate"], 1.0)
        self.assertEqual(res["pooled"]["arm_A"]["bucket_hit_rate"], 0.0)
        for half in ("half1", "half2"):
            self.assertGreater(res[half]["delta_hit_B_minus_A"], 0)
            self.assertGreater(res[half]["delta_log_B_minus_A"], 0)

    def test_zero_seam_zero_delta_dead(self):
        days = [_mk_day(f"2026-01-{i:02d}", 60.0, 60.0, 60.0)
                for i in range(1, 41)]
        cells, _ = probe_cells(days)
        for c in cells:
            self.assertEqual(c["pmf_a"], c["pmf_b"])
        res = evaluate(cells, days)
        self.assertEqual(res["pooled"]["delta_hit_B_minus_A"], 0.0)
        self.assertEqual(res["pooled"]["delta_log_B_minus_A"], 0.0)
        self.assertEqual(res["gate"]["verdict"], "DEAD")
        self.assertIn("C1", res["gate"]["failed"])
        self.assertIn("C2", res["gate"]["failed"])

    def test_leak_freeness_day_row_never_touches_own_cell(self):
        # Days 0..29 quiet (rise 0, catch 0); day 30 loud (rise 4, catch 40).
        days = [_mk_day(f"2026-01-{i:02d}", 60.0, 60.0, 60.0)
                for i in range(1, 31)]
        days.append(_mk_day("2026-01-31", 60.0, 64.0, 104.0))
        days.append(_mk_day("2026-02-01", 60.0, 60.0, 60.0))
        cells, _ = probe_cells(days)
        for c in [c for c in cells if c["date"] == "2026-01-31"]:
            self.assertEqual(c["seam_est"], 0.0)          # own catch unseen
            self.assertEqual(c["pmf_a"], {60: 1.0})       # own rise unseen
            self.assertEqual(c["pmf_b"], {60: 1.0})
        for c in [c for c in cells if c["date"] == "2026-02-01"]:
            # day 31 legitimately sees day 30 (strictly earlier)
            self.assertAlmostEqual(c["seam_est"], 40.0 / 31)
            self.assertAlmostEqual(c["pmf_a"].get(64, 0.0), 1.0 / 31)
            self.assertAlmostEqual(c["pmf_b"][61], 30 / 31)
            self.assertAlmostEqual(c["pmf_b"][65], 1 / 31)

    def test_c3_floor_untouched_design_check(self):
        days = [_mk_day(f"2026-01-{i:02d}", 60.0 + i * 0.1, 61.0 + i * 0.1,
                        62.0 + i * 0.1) for i in range(1, 41)]
        cells, _ = probe_cells(days)
        res = evaluate(cells, days)
        self.assertIs(
            res["gate"]["C3_shift_never_touches_floor_design_check"], True)


if __name__ == "__main__":
    unittest.main()
