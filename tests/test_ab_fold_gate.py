"""Network-free tests for the disjoint-fold sign-stability A/B gate (candidate 47).

A single council member's true effect sits below the run-to-run noise floor, so
an aggregate CRPS delta can be a pure artifact of which days fall in the held-out
window. The fix is to split the window into DISJOINT folds and demand the
candidate beat the incumbent on EVERY fold. These tests pin that logic — the same
logic that CLOSED candidate 47 (AIFS-for-HK helped in aggregate but flipped sign
on a w60 disjoint fold) — and the per-day CRPS stream it consumes.

Run with:  PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import dataclasses
import unittest
from types import SimpleNamespace

from weather_council.council import Validation
from tools.ab_backtest import (_round_half_up, _fold_dates, _fold_crps,
                               _fold_bucket_hit, _print_fold_gate)


class TestValidationHasPerDayCrps(unittest.TestCase):
    def test_wf_crps_field_defaults_empty(self):
        f = {fld.name: fld for fld in dataclasses.fields(Validation)}
        self.assertIn("wf_crps", f)

        def _mk():
            return Validation(council_mae_high=None, council_mae_low=None,
                              naive_mae_high=None, naive_mae_low=None,
                              hit_rate_2c=None, test_days=0)
        # default_factory list, not a shared mutable
        v1, v2 = _mk(), _mk()
        self.assertEqual(v1.wf_crps, [])
        v1.wf_crps.append(("2026-06-01", "high", 0.1, 0.2))
        self.assertEqual(v2.wf_crps, [])  # independent instances


class TestRoundHalfUp(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(_round_half_up(21.5), 22)
        self.assertEqual(_round_half_up(21.49), 21)
        self.assertEqual(_round_half_up(22.0), 22)


class TestFoldSplit(unittest.TestCase):
    def test_disjoint_and_covering(self):
        dates = [f"2026-06-{d:02d}" for d in range(1, 11)]  # 10 unique days
        folds = _fold_dates(dates, 2)
        self.assertEqual(len(folds), 2)
        self.assertEqual(folds[0] & folds[1], set())        # disjoint
        self.assertEqual(folds[0] | folds[1], set(dates))   # covering
        self.assertEqual(len(folds[0]), 5)
        self.assertEqual(len(folds[1]), 5)

    def test_dedups_repeated_dates(self):
        # high+low contribute the same date twice; folds split UNIQUE dates.
        dates = ["2026-06-01", "2026-06-01", "2026-06-02", "2026-06-02"]
        folds = _fold_dates(dates, 2)
        self.assertEqual(folds[0], {"2026-06-01"})
        self.assertEqual(folds[1], {"2026-06-02"})


class TestFoldMetrics(unittest.TestCase):
    def test_fold_crps_means_only_in_fold(self):
        wf = [("2026-06-01", "high", 0.4, 1.0), ("2026-06-02", "high", 0.6, 1.0)]
        self.assertAlmostEqual(_fold_crps(wf, {"2026-06-01"}), 0.4)
        self.assertAlmostEqual(_fold_crps(wf, {"2026-06-01", "2026-06-02"}), 0.5)
        self.assertIsNone(_fold_crps(wf, {"2026-06-09"}))

    def test_fold_bucket_hit_uses_round_half_up(self):
        # point 21.6 -> 22, realized 21.5 -> 22 : hit. point 20.4 -> 20 vs 21 : miss.
        wf = [("2026-06-01", 21.6, 21.5), ("2026-06-02", 20.4, 21.0)]
        self.assertAlmostEqual(_fold_bucket_hit(wf, {"2026-06-01", "2026-06-02"}), 0.5)
        self.assertAlmostEqual(_fold_bucket_hit(wf, {"2026-06-01"}), 1.0)


def _val(wf_crps, wf_high):
    return SimpleNamespace(wf_crps=wf_crps, wf_high=wf_high)


class TestSignStabilityVerdict(unittest.TestCase):
    def test_all_folds_pass_is_sign_stable(self):
        # B beats A on CRPS in both folds, hit equal -> PASS.
        a = _val([("d1", "high", 0.50, 1.0), ("d2", "high", 0.60, 1.0)],
                 [("d1", 21.0, 21.0), ("d2", 22.0, 22.0)])
        b = _val([("d1", "high", 0.48, 1.0), ("d2", "high", 0.55, 1.0)],
                 [("d1", 21.0, 21.0), ("d2", 22.0, 22.0)])
        self.assertTrue(_print_fold_gate(a, b, 2))

    def test_one_fold_flipping_fails(self):
        # B worse on fold d1 (0.52 > 0.50), better on d2 -> FAIL (sign flip).
        a = _val([("d1", "high", 0.50, 1.0), ("d2", "high", 0.60, 1.0)],
                 [("d1", 21.0, 21.0), ("d2", 22.0, 22.0)])
        b = _val([("d1", "high", 0.52, 1.0), ("d2", "high", 0.50, 1.0)],
                 [("d1", 21.0, 21.0), ("d2", 22.0, 22.0)])
        self.assertFalse(_print_fold_gate(a, b, 2))

    def test_crps_better_but_bucket_hit_worse_fails(self):
        # B has lower CRPS but a worse bucket hit on a fold -> FAIL.
        a = _val([("d1", "high", 0.50, 1.0)], [("d1", 21.0, 21.0)])  # A hits
        b = _val([("d1", "high", 0.40, 1.0)], [("d1", 21.9, 21.0)])  # B misses (22 vs 21)
        self.assertFalse(_print_fold_gate(a, b, 1))


if __name__ == "__main__":
    unittest.main()
