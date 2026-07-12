"""KAT for the per-member bias-break watch (weather_council/member_break.py +
tools/member_break_watch.py) — G1 of the 2026-07-12 driver audit, executing
ledger/preregistered/member_bias_break_watch.md.

Pins the registration's three named behaviors — synthetic break DETECTED, no-break
SILENT, recency-class seasonal drift NOT false-alarmed (the test is vs the frozen CI,
not vs zero) — plus pin immutability and the settled∧provenance join.
"""
from __future__ import annotations

import random
import unittest

from weather_council.member_break import (REF_N, ROLL_K, assess_all, assess_cell,
                                          extract_errors, pin_reference)


def _series(rng, n, mu, sd=1.0):
    return [rng.gauss(mu, sd) for _ in range(n)]


class TestMemberBreak(unittest.TestCase):

    def setUp(self):
        self.rng = random.Random(7)
        self.base = _series(self.rng, REF_N, 0.8)          # stable warm-bias regime

    def test_module_self_test(self):
        from weather_council import member_break
        member_break._self_test()

    def test_step_break_detected(self):
        step = self.base + _series(self.rng, ROLL_K, 2.8)  # +2σ pipeline step
        a = assess_cell(pin_reference(step), step, len(step))
        self.assertEqual(a["status"], "BREAK")

    def test_same_regime_stays_silent(self):
        same = self.base + _series(self.rng, 15, 0.8)
        a = assess_cell(pin_reference(same), same, len(same))
        self.assertEqual(a["status"], "OK")

    def test_seasonal_drift_does_not_false_alarm(self):
        # The registration's control: recency-class slow drift (0.03σ/day) stays inside
        # the frozen CI over a month — the break test is vs the CI, not vs zero.
        drift = self.base + [self.rng.gauss(0.8 + 0.03 * i, 1.0) for i in range(30)]
        a = assess_cell(pin_reference(drift), drift, len(drift))
        self.assertEqual(a["status"], "OK")

    def test_accruing_below_floors(self):
        self.assertEqual(assess_cell(pin_reference(self.base[:10]), self.base[:10],
                                     10)["status"], "ACCRUING")
        short = self.base + [0.8] * (ROLL_K - 1)           # pinned, thin post-ref
        self.assertEqual(assess_cell(pin_reference(short), short,
                                     len(short))["status"], "ACCRUING")

    def test_pins_frozen_once_written(self):
        same = self.base + _series(self.rng, 15, 0.8)
        cells = {("X", "ecmwf"): [(f"d{i:03d}", e) for i, e in enumerate(same)]}
        pins1, _ = assess_all(cells, {})
        step = self.base + _series(self.rng, ROLL_K, 2.8)
        pins2, res = assess_all({("X", "ecmwf"): [(f"d{i:03d}", e)
                                                  for i, e in enumerate(step)]}, pins1)
        self.assertEqual(pins2["X|ecmwf"], pins1["X|ecmwf"])   # never moved in code
        self.assertEqual(res["X|ecmwf"]["status"], "BREAK")

    def test_extract_errors_raw_vs_actual_and_join(self):
        rows = [("X", "2026-07-01", 30.0,
                 [{"member_id": "ecmwf", "raw_high": 31.0},
                  {"member_id": "gfs", "raw_high": 29.5},
                  {"member_id": "broken", "raw_high": None}]),
                ("X", "2026-07-02", None, [{"member_id": "ecmwf", "raw_high": 31.0}])]
        e = extract_errors(rows)
        self.assertEqual(e[("X", "ecmwf")], [("2026-07-01", 1.0)])
        self.assertAlmostEqual(e[("X", "gfs")][0][1], -0.5)
        self.assertNotIn(("X", "broken"), e)               # no raw vote, no series
        self.assertNotIn(("X", "ecmwf-unsettled"), e)

    def test_cli_empty_join_asserts_nothing(self):
        # Today's live state: 0 settled provenance rows -> 0 cells, no pins written,
        # no alert — the watch arms itself instead of inventing a baseline.
        pins, results = assess_all({}, {})
        self.assertEqual((pins, results), ({}, {}))


if __name__ == "__main__":
    unittest.main()
