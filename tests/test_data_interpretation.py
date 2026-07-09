"""KAT for the informational DATA INTERPRETATION line (run.py `_data_interpretation_lines`).

It reads the readily-available record (settlement_ref recent highs/lows + v.records climatology
normals) and surfaces recent-trend / vs-climatology / recent-cool-or-warm regime for BOTH the
high AND the low, as an INFORMATIONAL lens. Descriptive only — it must never move the
verdict/pmf/pick (the regime signal is real but sub-bucket-width, D18). Network-free.

Run with:  PYTHONPATH=. python3 -m unittest tests.test_data_interpretation -v
"""
from __future__ import annotations

import types
import unittest

import run


def _verdict(high, low, normal_high, normal_low):
    return types.SimpleNamespace(
        high=high, low=low,
        records=types.SimpleNamespace(normal_high=normal_high, normal_low=normal_low))


def _ref(highs, lows=None):   # oldest -> newest °C
    lows = lows if lows is not None else [h - 8 for h in highs]
    return {"recent": [{"date": f"d{i}", "high": h, "low": lo}
                       for i, (h, lo) in enumerate(zip(highs, lows))]}


class TestDataInterpretation(unittest.TestCase):
    def test_both_high_and_low_lines_present(self):
        v = _verdict(34.0, 21.0, 21.3, 13.4)                     # London heatwave
        ref = _ref([26, 25, 27, 29, 28, 32, 33], lows=[18, 18, 19, 20, 21, 22, 22])
        lines = "\n".join(run._data_interpretation_lines(v, ref, types.SimpleNamespace(grain="C")))
        self.assertIn("DATA INTERPRETATION", lines)
        self.assertIn("does NOT move the verdict", lines)        # informational disclaimer
        self.assertIn("D18", lines)                              # cites the gate result, not blended
        self.assertIn("HIGH :", lines)                           # both attributes read
        self.assertIn("LOW  :", lines)
        self.assertIn("RISING", lines)
        self.assertIn("RECENT-WARM", lines)
        self.assertIn("ABOVE norm", lines)

    def test_recent_cool_falling_read_in_F(self):
        # SF-shaped: record falling and below norm, settlement grain °F.
        v = _verdict(20.5, 11.0, 21.1, 12.0)
        ref = _ref([21.1, 21.1, 20.6, 20.0, 20.0, 19.4, 16.7])   # 70..62°F, falling
        lines = "\n".join(run._data_interpretation_lines(v, ref, types.SimpleNamespace(grain="F")))
        self.assertIn("FALLING", lines)
        self.assertIn("RECENT-COOL", lines)
        self.assertIn("°F", lines)                               # rendered in the settlement grain
        self.assertNotIn("°C", lines)

    def test_skips_when_record_or_normals_missing(self):
        self.assertEqual(run._data_interpretation_lines(_verdict(30.0, 20.0, 25.0, 15.0), None), [])
        # no normals -> nothing to interpret
        v_nonorm = types.SimpleNamespace(
            high=30.0, low=20.0,
            records=types.SimpleNamespace(normal_high=None, normal_low=None))
        self.assertEqual(run._data_interpretation_lines(v_nonorm, _ref([25, 26, 27, 28])), [])
        # <4 recent days -> skip
        self.assertEqual(run._data_interpretation_lines(_verdict(30.0, 20.0, 25.0, 15.0), _ref([25, 26])), [])

    def test_never_emits_a_pick_or_probability(self):
        v = _verdict(20.5, 11.0, 21.1, 12.0)
        lines = "\n".join(run._data_interpretation_lines(v, _ref([21, 21, 20, 20, 19, 17, 17]),
                                                         types.SimpleNamespace(grain="F")))
        for banned in ("best guess", "%", "pmf", "LOCK", "settles as"):
            self.assertNotIn(banned, lines)


if __name__ == "__main__":
    unittest.main()
