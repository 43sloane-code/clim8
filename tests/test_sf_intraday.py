"""KATs for the KSFO / San Francisco intraday lever (whole-°F settlement).

SF was previously skipped by both intraday modules ("not a configured settlement
city"). It is now wired into the dead-bucket floor and the ceiling sharpening, in
its native whole-°F grain. Pins: the config wiring in both modules, and that the
shared settlement quantizer produces °F buckets from a °C running max (the running
max is always carried in °C; _native_reading_int converts to the settlement grain).

Run with:  PYTHONPATH=. python3 -m unittest tests.test_sf_intraday -v
"""
from __future__ import annotations

import unittest

from weather_council.intraday_ceiling import (
    sharpen_pmf, _HOURLY_STATION, _WU_INTRADAY, _LIVE_REGISTER, _SETTLE_GRAIN)
from weather_council.intraday import _CITY_CONFIG


class TestSFConfigWiring(unittest.TestCase):
    def test_ceiling_maps_include_sf(self):
        self.assertIn("san francisco", _HOURLY_STATION)
        icao, tz, sub, name = _HOURLY_STATION["san francisco"]
        self.assertEqual(icao, "KSFO")
        self.assertFalse(sub)                              # whole-°F round-half-up, not floor
        self.assertIn("san francisco", _WU_INTRADAY)       # reads the WU hourly settlement feed
        self.assertIn("san francisco", _LIVE_REGISTER)     # consults the v3 current/register
        self.assertEqual(_SETTLE_GRAIN.get("san francisco"), "F")

    def test_deadbucket_config_includes_sf(self):
        sf = next((c for c in _CITY_CONFIG if c.key == "san francisco"), None)
        self.assertIsNotNone(sf)
        self.assertEqual(sf.icao, "KSFO")
        self.assertEqual(sf.grain, "F")
        self.assertFalse(sf.sub_degree)


class TestGrainAwareQuantizer(unittest.TestCase):
    def test_F_grain_produces_fahrenheit_buckets(self):
        # running max 18.3°C = 64.9°F; a tight rise cloud -> the 65°F bucket dominates
        rises = [0.0, 0.1, 0.2, -0.1, 0.15, 0.05, -0.05, 0.1, 0.0, 0.2]
        pmf_f = dict(sharpen_pmf(18.3, rises, sub_degree=False, grain="F"))
        self.assertIn(65, pmf_f)                           # °F bucket …
        self.assertNotIn(18, pmf_f)                        # … NOT the °C reading
        self.assertEqual(max(pmf_f, key=pmf_f.get), 65)

    def test_same_runmax_C_grain_buckets_in_celsius(self):
        # the identical running max under °C grain lands at 18 — proves the grain switch
        rises = [0.0, 0.1, 0.2, -0.1, 0.15, 0.05, -0.05, 0.1, 0.0, 0.2]
        pmf_c = dict(sharpen_pmf(18.3, rises, sub_degree=False, grain="C"))
        self.assertIn(18, pmf_c)
        self.assertNotIn(65, pmf_c)

    def test_default_grain_is_celsius(self):
        pmf = dict(sharpen_pmf(30.3, [0.0, 0.1, 0.2], sub_degree=False))  # no grain -> C
        self.assertIn(30, pmf)
        self.assertNotIn(86, pmf)                          # would be the °F reading


if __name__ == "__main__":
    unittest.main()
