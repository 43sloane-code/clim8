"""KATs pinning Jeddah (King Abdulaziz / OEJN) as a WU-anchored whole-°C settlement
city — same criteria/precision as London/Singapore/Karachi (the London pattern:
WU-settled whole-°C, IEM-backtested + overlay, IEM hourly intraday + WU register).
Unlike Karachi, the contract settles on the airport itself (OEJN), no proxy station.

Run with:  PYTHONPATH=. python3 -m unittest tests.test_jeddah -v
"""
from __future__ import annotations

import unittest


class TestJeddahWiring(unittest.TestCase):
    def test_wu_oracle_and_overlay(self):
        from weather_council.sources import WU_GEO, WU_LOCATION, _IEM_OVERLAY_TZ
        self.assertIn("OEJN", WU_GEO)
        self.assertEqual(WU_LOCATION.get("OEJN"), "OEJN:9:SA")
        self.assertEqual(_IEM_OVERLAY_TZ.get("OEJN"), "Asia/Riyadh")

    def test_wu_settle_whole_c(self):
        from weather_council.council import (PINNED_ANCHOR_ICAO, STRICT_ANCHOR_ICAO,
                                             _WU_SETTLE_C_ICAOS)
        from weather_council.storage import _WU_SETTLE_TZ
        self.assertEqual(PINNED_ANCHOR_ICAO.get("jeddah"), "OEJN")
        self.assertIn("jeddah", STRICT_ANCHOR_ICAO)
        self.assertIn("OEJN", _WU_SETTLE_C_ICAOS)
        self.assertEqual(_WU_SETTLE_TZ.get("OEJN"), "Asia/Riyadh")

    def test_settlement_reference_and_intraday(self):
        from run import SETTLEMENT_REFERENCE
        from weather_council.intraday_ceiling import (_HOURLY_STATION, _LIVE_REGISTER,
                                                      _WU_INTRADAY, _SETTLE_GRAIN)
        from weather_council.intraday import _CITY_CONFIG
        self.assertEqual(SETTLEMENT_REFERENCE["jeddah"]["icao"], "OEJN")
        self.assertEqual(_HOURLY_STATION["jeddah"][0], "OEJN")
        self.assertIn("jeddah", _LIVE_REGISTER)
        self.assertNotIn("jeddah", _WU_INTRADAY)      # IEM hourly backbone (London pattern)
        self.assertNotIn("jeddah", _SETTLE_GRAIN)     # whole-°C
        jed = next((c for c in _CITY_CONFIG if c.key == "jeddah"), None)
        self.assertIsNotNone(jed)
        self.assertEqual((jed.icao, jed.grain, jed.sub_degree), ("OEJN", "C", False))


if __name__ == "__main__":
    unittest.main()
