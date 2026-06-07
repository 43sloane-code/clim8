"""Network-free tests for sources.py live-feed parsing: HKO rhrread (whole-degree), HKO 1-minute 0.1 C feed, and the live EGLC METAR 'now'.

Stdlib unittest only. Run with:
    PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import datetime as dt
import math
import random
import statistics as st
import unittest

from weather_council import scoring
from weather_council.scoring import crps_sample, crps_gaussian, interval_coverage, quantile, pit
from weather_council.compare import residual_calibration, compare_high, MIN_RESIDUALS
from weather_council.market import WeatherMarket, MarketBucket
from weather_council.agents import Vote, MemberSpec, Skill
from weather_council.council import Council


class TestHKORhrread(unittest.TestCase):
    """The live Hong Kong 'current observation' must come from the HKO instrument
    (rhrread), not an Open-Meteo grid cell that sits ~2 °C off the Observatory."""

    def _payload(self, temp_rows, hum_rows=None, rt="2026-06-07T10:00:00+08:00"):
        d = {"temperature": {"recordTime": rt, "data": temp_rows}}
        if hum_rows is not None:
            d["humidity"] = {"recordTime": rt, "data": hum_rows}
        return d

    def test_extracts_observatory_reading(self):
        from weather_council.sources import _parse_hko_rhrread
        out = _parse_hko_rhrread(self._payload(
            [{"place": "King's Park", "value": 30, "unit": "C"},
             {"place": "Hong Kong Observatory", "value": 28.8, "unit": "C"}],
            [{"place": "Hong Kong Observatory", "value": 74, "unit": "percent"}]))
        self.assertEqual(out["temperature_2m"], 28.8)
        self.assertEqual(out["relative_humidity_2m"], 74.0)
        self.assertEqual(out["record_time"], "2026-06-07T10:00:00+08:00")

    def test_humidity_optional(self):
        from weather_council.sources import _parse_hko_rhrread
        out = _parse_hko_rhrread(self._payload(
            [{"place": "Hong Kong Observatory", "value": 29, "unit": "C"}]))
        self.assertEqual(out["temperature_2m"], 29.0)
        self.assertIsNone(out["relative_humidity_2m"])

    def test_missing_observatory_yields_none(self):
        from weather_council.sources import _parse_hko_rhrread
        self.assertIsNone(_parse_hko_rhrread(self._payload(
            [{"place": "King's Park", "value": 30, "unit": "C"}])))

    def test_corrupt_value_yields_none(self):
        from weather_council.sources import _parse_hko_rhrread
        # Out-of-band temperature is dropped by the plausibility screen, so the
        # caller keeps the grid reading rather than ingesting a corrupt one.
        self.assertIsNone(_parse_hko_rhrread(self._payload(
            [{"place": "Hong Kong Observatory", "value": 999, "unit": "C"}])))
        self.assertIsNone(_parse_hko_rhrread({"not": "a feed"}))

class TestHKO1MinTemp(unittest.TestCase):
    """The live 'now' temperature must use HKO's finer 1-minute 0.1 °C feed (the
    same Observatory gauge), not the whole-degree rhrread value — rhrread rounds
    28.4 to 28, which the UI then showed as a coarse 28.0."""

    HEADER = "Date time,Automatic Weather Station,Air Temperature(degree Celsius)"

    def _csv(self, *rows):
        return "\n".join([self.HEADER, *rows])

    def test_extracts_observatory_tenths_and_record_time(self):
        from weather_council.sources import _parse_hko_1min_temp
        out = _parse_hko_1min_temp(self._csv(
            "202606072330,Chek Lap Kok,29.8",
            "202606072330,HK Observatory,28.4"))
        self.assertEqual(out["temperature_2m"], 28.4)   # tenths, not rounded to 28
        self.assertEqual(out["record_time"], "2026-06-07T23:30:00+08:00")

    def test_missing_observatory_yields_none(self):
        from weather_council.sources import _parse_hko_1min_temp
        self.assertIsNone(_parse_hko_1min_temp(self._csv(
            "202606072330,Chek Lap Kok,29.8")))
        self.assertIsNone(_parse_hko_1min_temp(""))

    def test_blank_or_corrupt_value_yields_none(self):
        from weather_council.sources import _parse_hko_1min_temp
        # Blank/non-numeric or out-of-band cell -> None, so the caller falls back
        # to the whole-degree rhrread value rather than fabricating a reading.
        self.assertIsNone(_parse_hko_1min_temp(self._csv(
            "202606072330,HK Observatory,")))
        self.assertIsNone(_parse_hko_1min_temp(self._csv(
            "202606072330,HK Observatory,999")))

    def test_hko_current_prefers_1min_over_rhrread(self):
        # End-to-end: a fake client returns whole-degree 28 from rhrread but 28.4
        # from the 1-minute CSV; hko_current must surface the finer value, keep
        # rhrread humidity, and label the provenance as the 1-minute feed.
        from weather_council.sources import Sources
        rhr = {"temperature": {"recordTime": "2026-06-07T23:00:00+08:00",
                               "data": [{"place": "Hong Kong Observatory",
                                         "value": 28, "unit": "C"}]},
               "humidity": {"recordTime": "2026-06-07T23:00:00+08:00",
                            "data": [{"place": "Hong Kong Observatory",
                                      "value": 91, "unit": "percent"}]}}
        csv = ("Date time,Automatic Weather Station,Air Temperature(degree Celsius)\n"
               "202606072340,HK Observatory,28.4")

        class _FakeHTTP:
            def get_json(self, url, params): return rhr
            def get_text(self, url, params=None): return csv

        s = Sources()
        s.http = _FakeHTTP()
        out = s.hko_current()
        self.assertEqual(out["temperature_2m"], 28.4)
        self.assertEqual(out["relative_humidity_2m"], 91.0)
        self.assertIn("1-minute", out["temperature_source"])
        self.assertEqual(out["record_time"], "2026-06-07T23:40:00+08:00")

    def test_hko_current_falls_back_when_1min_unavailable(self):
        # If the 1-minute CSV fetch fails, hko_current keeps the whole-degree
        # rhrread value (degraded but live) rather than returning nothing.
        from weather_council.sources import Sources
        rhr = {"temperature": {"recordTime": "2026-06-07T23:00:00+08:00",
                               "data": [{"place": "Hong Kong Observatory",
                                         "value": 28, "unit": "C"}]}}

        class _FakeHTTP:
            def get_json(self, url, params): return rhr
            def get_text(self, url, params=None): raise RuntimeError("feed down")

        s = Sources()
        s.http = _FakeHTTP()
        out = s.hko_current()
        self.assertEqual(out["temperature_2m"], 28.0)
        self.assertIn("rhrread", out["temperature_source"])

class TestEGLCCurrent(unittest.TestCase):
    """London's live 'now' must come from the EGLC settlement sensor's most
    recent METAR (the airport the market resolves on), not the Open-Meteo grid."""

    def test_returns_latest_observation_as_iso(self):
        from weather_council.sources import Sources
        s = Sources()
        s.fetch_metar_observations = lambda *a, **k: [
            ("2026-06-07 16:20", 19.0), ("2026-06-07 16:50", 19.0)]
        out = s.eglc_current()
        self.assertEqual(out["temperature_2m"], 19.0)      # latest, not the first
        self.assertEqual(out["record_time"], "2026-06-07T16:50")

    def test_empty_feed_yields_none(self):
        from weather_council.sources import Sources
        s = Sources()
        s.fetch_metar_observations = lambda *a, **k: []
        self.assertIsNone(s.eglc_current())

    def test_fetch_failure_yields_none(self):
        from weather_council.sources import Sources
        def _boom(*a, **k): raise RuntimeError("IEM down")
        s = Sources()
        s.fetch_metar_observations = _boom
        self.assertIsNone(s.eglc_current())   # caller then keeps the grid 'now'

if __name__ == "__main__":
    unittest.main()
