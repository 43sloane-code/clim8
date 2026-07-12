"""KAT for tools/tape_logger.py — the London-coverage gap-filler (2026-07-12 audit).

Pins the instrument's contract: every configured city is tape-CAPABLE (hourly archive +
v3 register consult, else rows are empty theatre); Manila stays excluded by directive;
a non-sharpened ceiling yields no row and no grade (never fabricated); a live failure in
one city cannot starve the others.
"""
from __future__ import annotations

import datetime as dt
import io
import unittest
from contextlib import redirect_stdout

from tools import tape_logger
from weather_council.intraday_ceiling import _HOURLY_STATION, _LIVE_REGISTER


class TestTapeLogger(unittest.TestCase):

    def test_module_self_test(self):
        tape_logger._self_test()

    def test_every_city_is_tape_capable(self):
        for place in tape_logger.CITIES:
            key = place.name.strip().lower()
            self.assertIn(key, _HOURLY_STATION)   # can build a ceiling
            self.assertIn(key, _LIVE_REGISTER)    # endpoint/cur_f actually fetched

    def test_manila_excluded_by_directive(self):
        # RPLL has no register consult; a Manila row would be an empty no-op, and adding
        # the consult would touch a served number (out of scope by user directive).
        self.assertTrue(all(p.name.lower() != "manila" for p in tape_logger.CITIES))

    def test_failing_city_does_not_starve_the_next(self):
        # A Sources whose every fetch raises: each city reports its error and the loop
        # continues — 0 graded rows, no exception escapes, no tape write attempted.
        class _Boom:
            def __getattr__(self, name):
                def _raise(*a, **k):
                    raise RuntimeError("feed down")
                return _raise
        buf = io.StringIO()
        with redirect_stdout(buf):
            graded = tape_logger.log_once(sources=_Boom())
        out = buf.getvalue()
        self.assertEqual(graded, 0)
        self.assertEqual(out.count("no tape row") + out.count("no grade"),
                         len(tape_logger.CITIES))

    def test_station_coordinates_match_settlement_anchors(self):
        # The sunset gate reads these lat/lons; they must be the STATION, not a centroid.
        sg = next(p for p in tape_logger.CITIES if p.name == "Singapore")
        ldn = next(p for p in tape_logger.CITIES if p.name == "London")
        self.assertAlmostEqual(sg.latitude, 1.35, delta=0.01)      # WSSS Changi
        self.assertAlmostEqual(ldn.longitude, 0.055, delta=0.01)   # EGLC City Airport


if __name__ == "__main__":
    unittest.main()
