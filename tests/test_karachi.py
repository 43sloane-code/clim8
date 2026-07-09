"""KATs pinning Karachi (Jinnah / OPKC) as a WU-anchored, whole-°C settlement city —
the same criteria/precision as London and Singapore.

Karachi mirrors LONDON: settlement on the live WU oracle (whole-°C round-half-up),
backtest on IEM, IEM hourly intraday backbone + WU v3 register consult. The critical
extra is the IEM overlay: Karachi's Meteostat bulk file lags ~110 days, so without it
the backtest scores on the wrong season (March data in July).

Run with:  PYTHONPATH=. python3 -m unittest tests.test_karachi -v
"""
from __future__ import annotations

import unittest


class TestKarachiSettlementWiring(unittest.TestCase):
    def test_wu_oracle_maps(self):
        from weather_council.sources import WU_GEO, WU_LOCATION, _IEM_OVERLAY_TZ
        self.assertIn("OPKC", WU_GEO)
        self.assertEqual(WU_LOCATION.get("OPKC"), "OPKC:9:PK")
        # the currency fix: live IEM overlay closes the ~110-day Meteostat lag
        self.assertEqual(_IEM_OVERLAY_TZ.get("OPKC"), "Asia/Karachi")

    def test_wu_settle_whole_c(self):
        from weather_council.council import (PINNED_ANCHOR_ICAO, STRICT_ANCHOR_ICAO,
                                             _WU_SETTLE_C_ICAOS)
        from weather_council.storage import _WU_SETTLE_TZ
        self.assertEqual(PINNED_ANCHOR_ICAO.get("karachi"), "OPKC")
        self.assertIn("karachi", STRICT_ANCHOR_ICAO)       # no silent fall-through to another station
        self.assertIn("OPKC", _WU_SETTLE_C_ICAOS)          # settles whole-°C on WU (like EGLC/WSSS/RPLL)
        self.assertEqual(_WU_SETTLE_TZ.get("OPKC"), "Asia/Karachi")

    def test_settlement_reference(self):
        from run import SETTLEMENT_REFERENCE
        khi = SETTLEMENT_REFERENCE.get("karachi")
        self.assertIsNotNone(khi)
        self.assertEqual(khi["icao"], "OPKC")
        self.assertIn("wunderground.com", khi["url"])


class TestKarachiIntradayWiring(unittest.TestCase):
    def test_ceiling_maps_london_pattern(self):
        from weather_council.intraday_ceiling import (
            _HOURLY_STATION, _WU_INTRADAY, _LIVE_REGISTER, _SETTLE_GRAIN)
        self.assertIn("karachi", _HOURLY_STATION)
        icao, tz, sub, name = _HOURLY_STATION["karachi"]
        self.assertEqual(icao, "OPKC")
        self.assertFalse(sub)
        self.assertIn("karachi", _LIVE_REGISTER)           # consults the WU v3 register (like London)
        self.assertNotIn("karachi", _WU_INTRADAY)          # IEM hourly backbone (like London), not WU-native
        self.assertNotIn("karachi", _SETTLE_GRAIN)         # whole-°C (default), not °F

    def test_deadbucket_config(self):
        from weather_council.intraday import _CITY_CONFIG
        khi = next((c for c in _CITY_CONFIG if c.key == "karachi"), None)
        self.assertIsNotNone(khi)
        self.assertEqual(khi.icao, "OPKC")
        self.assertEqual(khi.grain, "C")
        self.assertFalse(khi.sub_degree)


if __name__ == "__main__":
    unittest.main()
