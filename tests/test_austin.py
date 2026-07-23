"""KATs pinning Austin (Austin Bergstrom / KAUS) as an IEM-ASOS-METAR-anchored
settlement city for Kalshi KXHIGHAUS. The contract settles on the NWS Climatological
Report (Daily) for KAUS, which is built from the same ASOS/METAR feed the IEM archive
ingests — so the council's backtest and the market's settlement share one source.
Austin is deliberately NOT a WU-truth city (unlike Manila/Singapore/SF) and NOT a
WU-settled whole-°C city (unlike London/Karachi/Jeddah).

Run with:  PYTHONPATH=. python3 -m unittest tests.test_austin -v
"""
from __future__ import annotations

import unittest


class TestAustinWiring(unittest.TestCase):
    def test_kaus_oracle_and_overlay(self):
        from weather_council.sources import WU_GEO, WU_LOCATION, _IEM_OVERLAY_TZ
        self.assertIn("KAUS", WU_GEO)
        self.assertEqual(WU_LOCATION.get("KAUS"), "KAUS:9:US")
        self.assertEqual(_IEM_OVERLAY_TZ.get("KAUS"), "America/Chicago")

    def test_kaus_pinned_strict_anchor(self):
        from weather_council.council import (PINNED_ANCHOR_ICAO, STRICT_ANCHOR_ICAO,
                                             _WU_SETTLE_C_ICAOS, _WU_TRUTH_STATIONS)
        from weather_council.storage import _WU_SETTLE_TZ
        self.assertEqual(PINNED_ANCHOR_ICAO.get("austin"), "KAUS")
        self.assertIn("austin", STRICT_ANCHOR_ICAO)
        # Austin settles on NWS CLI / IEM ASOS, not WU:
        self.assertNotIn("KAUS", _WU_SETTLE_C_ICAOS)
        self.assertNotIn("KAUS", {s["icao"] for s in _WU_TRUTH_STATIONS.values()})
        self.assertNotIn("KAUS", _WU_SETTLE_TZ)

    def test_station_provenance_is_iem_for_kaus(self):
        from weather_council.council import Council
        from weather_council.sources import Sources, Station
        # Minimal synthetic KAUS station; council just needs the ICAO for provenance.
        st = Station(id="74745", name="Austin / Del Valle", wmo="74745",
                     icao="KAUS", latitude=30.1945, longitude=-97.6699,
                     elevation=165.0, distance_km=0.1)
        prov, _label = Council(Sources())._station_provenance(st)
        self.assertEqual(prov, "iem_metar")


if __name__ == "__main__":
    unittest.main()
