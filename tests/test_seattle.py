"""KATs pinning Seattle (Seattle-Tacoma Intl / KSEA) as an IEM-ASOS-METAR-anchored
settlement city for the Kalshi Seattle high-temperature market. The contract settles
on the NWS Climatological Report (Daily) for KSEA, built from the same ASOS/METAR feed
the IEM archive ingests — so the council's backtest and the market's settlement share
one source. Seattle is deliberately NOT a WU-truth city and NOT a WU-settled whole-°C
city.

Run with:  PYTHONPATH=. python3 -m unittest tests.test_seattle -v
"""
from __future__ import annotations

import unittest


class TestSeattleWiring(unittest.TestCase):
    def test_ksea_oracle_and_overlay(self):
        from weather_council.sources import WU_GEO, WU_LOCATION, _IEM_OVERLAY_TZ
        self.assertIn("KSEA", WU_GEO)
        self.assertEqual(WU_LOCATION.get("KSEA"), "KSEA:9:US")
        self.assertEqual(_IEM_OVERLAY_TZ.get("KSEA"), "America/Los_Angeles")

    def test_ksea_pinned_strict_anchor(self):
        from weather_council.council import (PINNED_ANCHOR_ICAO, STRICT_ANCHOR_ICAO,
                                             _WU_SETTLE_C_ICAOS, _WU_TRUTH_STATIONS)
        from weather_council.storage import _WU_SETTLE_TZ
        self.assertEqual(PINNED_ANCHOR_ICAO.get("seattle"), "KSEA")
        self.assertIn("seattle", STRICT_ANCHOR_ICAO)
        # Seattle settles on NWS CLI / IEM ASOS, not WU:
        self.assertNotIn("KSEA", _WU_SETTLE_C_ICAOS)
        self.assertNotIn("KSEA", {s["icao"] for s in _WU_TRUTH_STATIONS.values()})
        self.assertNotIn("KSEA", _WU_SETTLE_TZ)

    def test_station_provenance_is_iem_for_ksea(self):
        from weather_council.council import Council
        from weather_council.sources import Sources, Station
        # Minimal synthetic KSEA station; council just needs the ICAO for provenance.
        st = Station(id="72793", name="Seattle / Sea-Tac", wmo="72793",
                     icao="KSEA", latitude=47.4502, longitude=-122.3088,
                     elevation=132.0, distance_km=0.1)
        prov, _label = Council(Sources())._station_provenance(st)
        self.assertEqual(prov, "iem_metar")

    def test_intraday_station_and_grain(self):
        from weather_council.intraday_ceiling import _HOURLY_STATION, _SETTLE_GRAIN
        self.assertIn("seattle", _HOURLY_STATION)
        self.assertEqual(_HOURLY_STATION["seattle"][0], "KSEA")
        self.assertEqual(_HOURLY_STATION["seattle"][1], "America/Los_Angeles")
        self.assertEqual(_SETTLE_GRAIN.get("seattle"), "F")

    def test_intraday_floor_config(self):
        from weather_council.intraday import _CITY_CONFIG, _cfg_for
        from weather_council.sources import Place
        cfg = _cfg_for(Place(name="Seattle", latitude=47.4502, longitude=-122.3088,
                             country="US", timezone="America/Los_Angeles"))
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.icao, "KSEA")
        self.assertEqual(cfg.grain, "F")
        self.assertEqual(cfg.fetch, "nws")
        self.assertFalse(cfg.sub_degree)

    def test_nws_current_parse(self):
        from weather_council.sources import Sources
        from unittest.mock import patch, MagicMock
        sources = Sources()
        mock_resp = {
            "properties": {
                "timestamp": "2026-07-20T23:10:00+00:00",
                "temperature": {"value": 27.0, "unitCode": "wmoUnit:degC"},
            }
        }
        with patch.object(sources.http, "get_json", return_value=mock_resp):
            cur = sources.nws_current("KSEA")
        self.assertIsNotNone(cur)
        self.assertEqual(cur["temperature_2m"], 27.0)
        self.assertEqual(cur["record_time"], "2026-07-20T23:10:00+00:00")


if __name__ == "__main__":
    unittest.main()
