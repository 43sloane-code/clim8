"""KAT for Sources.twc_forecast_daily (Plan 4 Phase 1) — the TWC cross-reference fetch surface.

Network-free: a fake http client replays the exact Phase-0 probe schema. Pins, in order of what
would hurt most if it broke:
  * the fetch rides units='e' (whole-°F, the WU settlement grain) at the SETTLEMENT-ANCHOR geocode
    passed in — NOT a city centroid — and converts once at the edge (°C basket, °F where applicable);
  * the target day is matched UTC-independently on validTimeLocal[:10], so an Asian-tz city (Manila)
    and a European one (London) each resolve the correct index regardless of the embedded offset;
  * a transport error OR a structurally malformed response records the 'twc_forecast' soft failure
    (DISTINCT from the truth path's tag) and returns None — never a guessed temperature;
  * a well-formed response that simply does not yet cover the target returns None WITHOUT a soft
    failure (not-yet-published is normal, not a defect); a null max for the matched day → None.

Run with:  PYTHONPATH=. python3 -m unittest tests.test_sources_twc -v
"""
from __future__ import annotations

import datetime as dt
import unittest
from unittest import mock

from weather_council.sources import Sources, TWC_FORECAST_URL, WU_API_KEY

# The Phase-0 probe shape (parallel arrays index-aligned to validTimeLocal). Highs/lows are °F ints.
_VALID = ["2026-07-11T07:00:00+0800", "2026-07-12T07:00:00+0800", "2026-07-13T07:00:00+0800"]
_HIGHS_F = [89, 87, 90]
_LOWS_F = [81, 79, 80]
_FIXTURE = {"validTimeLocal": _VALID,
            "calendarDayTemperatureMax": _HIGHS_F,
            "calendarDayTemperatureMin": _LOWS_F}


class _FakeHTTP:
    def __init__(self, payload=None, raises=False):
        self.payload = payload
        self.raises = raises
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, params))
        if self.raises:
            raise ConnectionError("boom")
        return self.payload


def _src(payload=None, raises=False):
    s = Sources()
    s.http = _FakeHTTP(payload, raises)
    return s


class TestTwcForecastDaily(unittest.TestCase):
    # ── fetch shape: units='e', anchor geocode, host ─────────────────────────────────────────────
    def test_fetch_uses_units_e_at_anchor_geocode(self):
        s = _src(_FIXTURE)
        s.twc_forecast_daily(1.3502, 103.994, dt.date(2026, 7, 12), "Asia/Singapore", "C")
        url, params = s.http.calls[0]
        self.assertEqual(url, TWC_FORECAST_URL)
        self.assertEqual(params["units"], "e")                 # whole-°F = WU settlement grain
        self.assertEqual(params["geocode"], "1.3502,103.994")  # settlement anchor, not centroid
        self.assertEqual(params["apiKey"], WU_API_KEY)         # same public web key as the truth path

    # ── conversion at the edge ───────────────────────────────────────────────────────────────────
    def test_celsius_conversion_for_basket_city(self):
        s = _src(_FIXTURE)
        out = s.twc_forecast_daily(1.3502, 103.994, dt.date(2026, 7, 12), "Asia/Singapore", "C")
        self.assertAlmostEqual(out["fc_high"], (87 - 32) * 5 / 9)   # 30.5556°C
        self.assertAlmostEqual(out["fc_low"], (79 - 32) * 5 / 9)    # 26.1111°C
        self.assertEqual(out["grain"], "C")
        self.assertEqual(out["raw_day_label"], "2026-07-12T07:00:00+0800")

    def test_fahrenheit_grain_passthrough(self):
        s = _src(_FIXTURE)
        out = s.twc_forecast_daily(37.6189, -122.375, dt.date(2026, 7, 12), "America/Los_Angeles", "F")
        self.assertEqual(out["fc_high"], 87.0)                 # °F kept as-is where the market settles °F
        self.assertEqual(out["fc_low"], 79.0)

    # ── day-mapping is UTC-independent (validTimeLocal[:10]) ──────────────────────────────────────
    def test_day_mapping_manila_asian_offset(self):
        fx = {**_FIXTURE, "validTimeLocal": [v.replace("+0800", "+0800") for v in _VALID]}
        s = _src(fx)
        out = s.twc_forecast_daily(14.5086, 121.0198, dt.date(2026, 7, 13), "Asia/Manila", "C")
        self.assertAlmostEqual(out["fc_high"], (90 - 32) * 5 / 9)   # index 2, the third day
        self.assertEqual(out["raw_day_label"][:10], "2026-07-13")

    def test_day_mapping_london_european_offset(self):
        fx = {**_FIXTURE, "validTimeLocal": [v.replace("+0800", "+0100") for v in _VALID]}
        s = _src(fx)
        out = s.twc_forecast_daily(51.5053, 0.0553, dt.date(2026, 7, 11), "Europe/London", "C")
        self.assertAlmostEqual(out["fc_high"], (89 - 32) * 5 / 9)   # index 0, still matched on date
        self.assertEqual(out["raw_day_label"][-5:], "+0100")

    # ── None paths ───────────────────────────────────────────────────────────────────────────────
    def test_target_not_in_horizon_returns_none_no_soft_failure(self):
        s = _src(_FIXTURE)
        with mock.patch("weather_council.failures.record_soft_failure") as rec:
            out = s.twc_forecast_daily(1.3502, 103.994, dt.date(2026, 7, 30), "Asia/Singapore", "C")
        self.assertIsNone(out)
        rec.assert_not_called()                                # not-yet-published is normal, not a defect

    def test_null_max_for_matched_day_returns_none(self):
        fx = {**_FIXTURE, "calendarDayTemperatureMax": [89, None, 90]}
        s = _src(fx)
        with mock.patch("weather_council.failures.record_soft_failure") as rec:
            out = s.twc_forecast_daily(1.3502, 103.994, dt.date(2026, 7, 12), "Asia/Singapore", "C")
        self.assertIsNone(out)
        rec.assert_not_called()                                # a present-but-unset max is not a guess

    def test_malformed_response_records_soft_failure(self):
        s = _src({"validTimeLocal": _VALID})                   # calendar-day arrays missing
        with mock.patch("weather_council.failures.record_soft_failure") as rec:
            out = s.twc_forecast_daily(1.3502, 103.994, dt.date(2026, 7, 12), "Asia/Singapore", "C")
        self.assertIsNone(out)
        rec.assert_called_once()
        self.assertEqual(rec.call_args[0][0], "twc_forecast")  # DISTINCT tag from the truth path

    def test_transport_error_records_soft_failure(self):
        s = _src(raises=True)
        with mock.patch("weather_council.failures.record_soft_failure") as rec:
            out = s.twc_forecast_daily(1.3502, 103.994, dt.date(2026, 7, 12), "Asia/Singapore", "C")
        self.assertIsNone(out)
        rec.assert_called_once()
        self.assertEqual(rec.call_args[0][0], "twc_forecast")

    def test_missing_low_still_returns_high(self):
        fx = {**_FIXTURE, "calendarDayTemperatureMin": [81, None, 80]}
        s = _src(fx)
        out = s.twc_forecast_daily(1.3502, 103.994, dt.date(2026, 7, 12), "Asia/Singapore", "C")
        self.assertAlmostEqual(out["fc_high"], (87 - 32) * 5 / 9)
        self.assertIsNone(out["fc_low"])                       # never guesses the missing low


if __name__ == "__main__":
    unittest.main()
