"""Tests for the TWC forecast forward-logger (tools/twc_forecast_logger).

The fetch/day-alignment/°F→°C contract now lives in Sources.twc_forecast_daily (KAT
tests/test_sources_twc.py); the logger delegates to it (Plan 4 Phase 1). Here we verify the
logger's OWN glue deterministically (no network, no DB write): the shared method picks the right
day's high through a fake client and hands back a settlement-grade °C bucket, and the tool's
selftest passes."""
import datetime as dt
import unittest

from tools.twc_forecast_logger import _selftest
from weather_council.market import _native_reading_int
from weather_council.sources import Sources


class _FakeHTTP:
    payload = {"validTimeLocal": ["2026-07-01T07:00:00+0800", "2026-07-02T07:00:00+0800"],
               "calendarDayTemperatureMax": [90, 86], "calendarDayTemperatureMin": [78, 79]}

    def get_json(self, url, params):
        return self.payload


class TestTwcForecastLogger(unittest.TestCase):
    def _src(self):
        s = Sources()
        s.http = _FakeHTTP()
        return s

    def test_shared_method_date_alignment_and_bucket(self):
        s = self._src()
        pick = s.twc_forecast_daily(1.35, 103.99, dt.date(2026, 7, 2), "Asia/Singapore", "C")
        self.assertAlmostEqual(pick["fc_high"], 30.0)                         # 86°F
        self.assertEqual(_native_reading_int(pick["fc_high"], "C", False), 30)

    def test_shared_method_bucket_rounds_up(self):
        s = self._src()
        pick = s.twc_forecast_daily(1.35, 103.99, dt.date(2026, 7, 1), "Asia/Singapore", "C")
        self.assertEqual(_native_reading_int(pick["fc_high"], "C", False), 32)  # 90°F=32.2°C→32

    def test_shared_method_absent_date_is_none(self):
        s = self._src()
        self.assertIsNone(s.twc_forecast_daily(1.35, 103.99, dt.date(2026, 7, 9), "Asia/Singapore", "C"))

    def test_selftest_passes(self):
        self.assertEqual(_selftest(), 0)


if __name__ == "__main__":
    unittest.main()
