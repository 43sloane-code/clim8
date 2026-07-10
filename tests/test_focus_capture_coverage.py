"""KAT: the daily spine captures a paired price+book snapshot for the WHOLE focus basket.

Executable P&L (paper_pnl.simulate_executable) joins a settled day's price snapshot to the order
book captured at the SAME instant. That pairing only happens on the wired run.py --market path,
which accumulate.py drives. So every book-capture focus city (book_logger.FOCUS_CITIES) must be
driven daily by accumulate — either through the analytical spine (CITIES) or the dedicated focus
loop (FOCUS_BOOK_CITIES). This pins that coverage so a focus city can never be declared but left
un-accrued (the exact gap that left executable P&L Singapore-only). Network-free.

Run with:  PYTHONPATH=. python3 -m unittest tests.test_focus_capture_coverage -v
"""
from __future__ import annotations

import unittest

from tools.accumulate import CITIES, FOCUS_BOOK_CITIES
from tools.book_logger import in_focus, FOCUS_CITIES


def _norm(name: str) -> str:
    return name.strip().lower()


class TestFocusCaptureCoverage(unittest.TestCase):
    def test_every_focus_city_is_captured_daily(self):
        # The union of (spine cities that are in focus) + (dedicated focus loop) must
        # cover every declared book-capture focus city.
        captured = [c for c in CITIES if in_focus(c)] + list(FOCUS_BOOK_CITIES)
        for fc in FOCUS_CITIES:                      # e.g. "karachi", "london", ...
            self.assertTrue(
                any(fc in _norm(c) for c in captured),
                f"focus city {fc!r} is never driven by accumulate — executable P&L "
                f"would never accrue for it")

    def test_focus_loop_is_disjoint_from_the_spine(self):
        # No city captured twice (once via CITIES, once via FOCUS_BOOK_CITIES).
        self.assertEqual(
            {_norm(c) for c in FOCUS_BOOK_CITIES} & {_norm(c) for c in CITIES},
            set())

    def test_focus_loop_cities_are_all_in_focus(self):
        for c in FOCUS_BOOK_CITIES:
            self.assertTrue(in_focus(c), f"{c!r} is in the focus loop but not in FOCUS_CITIES")


if __name__ == "__main__":
    unittest.main()
