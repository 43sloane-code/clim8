"""KAT: the day-ahead CROSS-CHECK panel compares signals in ONE unit (run._cross_check_lines).

SF settles whole-°F, so its market modal is a °F bucket label ("66-67°F") while the council/TWC/
regime signals are °C buckets. Before the fix the panel printed "council 19°C · market 66°C" and
flagged a bogus "COUNCIL is the OUTLIER — 19/66 boundary is live" — mixing 19°C against a 66°F
number. The fix converts a °F market modal to its °C bucket so the comparison is like-for-like.
This pins that: °F markets are converted (66-67°F → 19°C == council → agree), °C cities are
untouched, and no raw °F number ever appears labeled °C. Network-free (DB touches mocked out).

Run with:  PYTHONPATH=. python3 -m unittest tests.test_crosscheck_grain -v
"""
from __future__ import annotations

import types
import unittest
from unittest import mock

import run
from weather_council import storage


def _verdict(label, high):
    return types.SimpleNamespace(
        place=types.SimpleNamespace(label=lambda label=label: label),
        target="2026-07-10", high=high)


class TestCrossCheckGrain(unittest.TestCase):
    def _lines(self, v, c, comparison):
        # No regime signal, and no TWC row — keep the panel to council + market so the unit
        # bug is isolated. Mock the two DB-touching calls to stay hermetic.
        with mock.patch.object(run, "live_bucket_scorecard", lambda *a, **k: {"recent": []}), \
             mock.patch.object(storage, "_connect", side_effect=RuntimeError("no db in test")):
            return "\n".join(run._cross_check_lines(v, c, comparison))

    def test_sf_fahrenheit_market_is_converted_to_celsius(self):
        v = _verdict("San Francisco, United States", 19.2)
        comp = types.SimpleNamespace(market_modal="66-67°F", grain="F")
        out = self._lines(v, {"bucket": 19}, comp)
        self.assertIn("market 19°C", out)          # 66-67°F converted to its °C bucket
        self.assertNotIn("66°C", out)              # the exact bug — a °F number labeled °C
        self.assertNotIn("OUTLIER", out)           # council 19 == market 19 → agreement
        self.assertNotIn("19/66", out)             # no cross-unit "boundary"
        self.assertIn("cross-validated", out)

    def test_sf_disagreement_stays_in_celsius(self):
        # 68-69°F → 20°C vs council 19°C: a real one-bucket split, expressed wholly in °C.
        v = _verdict("San Francisco, United States", 19.2)
        comp = types.SimpleNamespace(market_modal="68-69°F", grain="F")
        out = self._lines(v, {"bucket": 19}, comp)
        self.assertIn("market 20°C", out)
        self.assertNotIn("68°C", out)
        self.assertIn("19/20 boundary", out)       # both edges °C, sensible

    def test_celsius_city_unchanged(self):
        v = _verdict("London, United Kingdom", 28.8)
        comp = types.SimpleNamespace(market_modal="29°C", grain="C")
        out = self._lines(v, {"bucket": 29}, comp)
        self.assertIn("market 29°C", out)          # untouched
        self.assertIn("cross-validated", out)

    def test_celsius_city_outlier_unchanged(self):
        v = _verdict("Singapore, Singapore", 31.4)
        comp = types.SimpleNamespace(market_modal="32°C", grain="C")
        out = self._lines(v, {"bucket": 31}, comp)
        self.assertIn("market 32°C", out)
        self.assertIn("31/32 boundary", out)       # genuine °C disagreement, as before


class TestRegimeGrain(unittest.TestCase):
    """The recent-regime signal comes from live_bucket_scorecard, whose buckets are
    in the market's SETTLEMENT grain (°F for SF) while the panel prints °C — the
    same unit artifact the market-modal fix addressed. Pins the conversion."""

    def _lines(self, v, c, comparison, recent):
        with mock.patch.object(run, "live_bucket_scorecard",
                               lambda *a, **k: {"recent": recent}), \
             mock.patch.object(storage, "_connect", side_effect=RuntimeError("no db in test")):
            return "\n".join(run._cross_check_lines(v, c, comparison))

    @staticmethod
    def _recent(served, n=9):                       # n >= 8 clears the M3 thin guard
        return [(f"2026-07-0{i}", served, served, True) for i in range(1, n + 1)]

    def test_f_grain_scorecard_is_converted_to_celsius(self):
        # SF: nine settled days served 66 (whole-°F buckets) == 19°C == council.
        v = _verdict("San Francisco, United States", 19.2)
        comp = types.SimpleNamespace(market_modal="66-67°F", grain="F")
        out = self._lines(v, {"bucket": 19}, comp, self._recent(66))
        self.assertIn("recent-regime 19°C", out)   # 66°F converted to its °C bucket
        self.assertNotIn("66°C", out)              # the bug — a °F regime labeled °C
        self.assertNotIn("OUTLIER", out)           # council 19 == regime 19 → agreement
        self.assertIn("cross-validated", out)

    def test_f_grain_disagreement_stays_in_celsius(self):
        # Regime mode 68 (°F) → 20°C vs council/market 19°C: a real split, in °C.
        v = _verdict("San Francisco, United States", 19.2)
        comp = types.SimpleNamespace(market_modal="66-67°F", grain="F")
        out = self._lines(v, {"bucket": 19}, comp, self._recent(68))
        self.assertIn("recent-regime 20°C", out)
        self.assertNotIn("68°C", out)
        self.assertIn("the signals split", out)

    def test_c_grain_scorecard_unchanged(self):
        v = _verdict("London, United Kingdom", 28.8)
        comp = types.SimpleNamespace(market_modal="29°C", grain="C")
        out = self._lines(v, {"bucket": 29}, comp, self._recent(29))
        self.assertIn("recent-regime 29°C", out)   # untouched
        self.assertIn("cross-validated", out)


if __name__ == "__main__":
    unittest.main()
