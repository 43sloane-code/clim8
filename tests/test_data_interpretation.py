"""KAT for the informational DATA INTERPRETATION line (run.py `_data_interpretation_lines`).

It reads the readily-available record (settlement_ref recent highs + v.records climatology
normal) and surfaces recent-trend / vs-climatology / recent-cool-or-warm regime as an
INFORMATIONAL lens. Descriptive only — it must never move the verdict/pmf/pick (the regime
signal is real but sub-bucket-width, D18). Network-free.

Run with:  PYTHONPATH=. python3 -m unittest tests.test_data_interpretation -v
"""
from __future__ import annotations

import types
import unittest

import run


def _verdict(high, normal_high):
    return types.SimpleNamespace(
        high=high, records=types.SimpleNamespace(normal_high=normal_high))


def _ref(highs):   # oldest -> newest °C
    return {"recent": [{"date": f"d{i}", "high": h, "low": h - 8} for i, h in enumerate(highs)]}


class TestDataInterpretation(unittest.TestCase):
    def test_recent_cool_falling_flags_downside(self):
        # SF-shaped: forecast near norm, record falling hard and sitting below norm.
        v = _verdict(20.5, 21.1)     # ~69°F vs ~70°F norm
        ref = _ref([21.1, 21.1, 20.6, 20.0, 20.0, 19.4, 16.7])   # 70..62°F, falling
        comparison = types.SimpleNamespace(grain="F")            # SF settles °F
        lines = "\n".join(run._data_interpretation_lines(v, ref, comparison))
        self.assertIn("DATA INTERPRETATION", lines)
        self.assertIn("does NOT move the verdict", lines)        # informational disclaimer present
        self.assertIn("FALLING", lines)
        self.assertIn("RECENT-COOL", lines)
        self.assertIn("°F", lines)                               # rendered in the settlement grain
        self.assertIn("D18", lines)                              # cites the gate result, not blended

    def test_recent_warm_rising_flags_upper(self):
        v = _verdict(34.0, 21.3)                                 # London heatwave, °C
        ref = _ref([26, 25, 27, 29, 28, 32, 32])                 # rising
        lines = "\n".join(run._data_interpretation_lines(v, ref, types.SimpleNamespace(grain="C")))
        self.assertIn("RISING", lines)
        self.assertIn("RECENT-WARM", lines)
        self.assertIn("ABOVE the seasonal norm", lines)
        self.assertNotIn("°F", lines)                            # °C city

    def test_skips_when_record_or_normal_missing(self):
        self.assertEqual(run._data_interpretation_lines(_verdict(30.0, 25.0), None), [])
        self.assertEqual(run._data_interpretation_lines(_verdict(30.0, None), _ref([25, 26, 27, 28])), [])
        self.assertEqual(run._data_interpretation_lines(_verdict(30.0, 25.0), _ref([25, 26])), [])  # <4 days

    def test_never_returns_a_bucket_or_probability(self):
        # The line is descriptive: it must not emit a served pick/probability token.
        v = _verdict(20.5, 21.1)
        lines = "\n".join(run._data_interpretation_lines(v, _ref([21, 21, 20, 20, 19, 17, 17]),
                                                         types.SimpleNamespace(grain="F")))
        for banned in ("best guess", "%", "pmf", "LOCK", "settles as"):
            self.assertNotIn(banned, lines)


if __name__ == "__main__":
    unittest.main()
