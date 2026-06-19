"""Tests for Wunderground settlement-oracle backtest truth (Manila -> RPLL).

Covers the new Sources.wunderground_daily_series fetcher (local-day grouping,
F->C max/min, partial-day drop, unknown-station guard) and the _wu_truth_station
routing helper that anchors Manila's backtest truth on the WU feed."""
import datetime as dt
import unittest
from zoneinfo import ZoneInfo

from weather_council.sources import Sources, Place
from weather_council.council import _wu_truth_station

_MANILA = ZoneInfo("Asia/Manila")


def _obs_for_day(d: dt.date, temps_f: list[float]) -> list[dict]:
    """Hourly WU observations for a Manila-local calendar day, as the API returns
    them: epoch valid_time_gmt + whole-°F temp."""
    out = []
    for hour, tf in enumerate(temps_f):
        local = dt.datetime(d.year, d.month, d.day, hour, 0, tzinfo=_MANILA)
        out.append({"temp": tf, "valid_time_gmt": int(local.timestamp())})
    return out


class _FakeHTTP:
    """Returns a fixed observation set for any get_json call (one chunk in test)."""

    def __init__(self, observations: list[dict]):
        self._obs = observations

    def get_json(self, url, params=None):
        return {"observations": self._obs}


class TestWundergroundTruth(unittest.TestCase):
    def test_series_groups_local_day_max_min_and_drops_partial(self):
        obs = (
            _obs_for_day(dt.date(2026, 6, 10), [80] + [97] + [85] * 22)   # max 97F min 80F, 24 obs
            + _obs_for_day(dt.date(2026, 6, 11), [78] + [90] + [84] * 22)  # max 90F min 78F, 24 obs
            + _obs_for_day(dt.date(2026, 6, 12), [88] * 6)                 # partial (6 obs) -> dropped
        )
        s = Sources(http=_FakeHTTP(obs))
        series = s.wunderground_daily_series(
            "RPLL", dt.date(2026, 6, 10), dt.date(2026, 6, 12), "Asia/Manila")
        self.assertEqual(set(series), {"2026-06-10", "2026-06-11"})  # partial day excluded
        mx, mn = series["2026-06-10"]
        self.assertAlmostEqual(mx, (97 - 32) * 5 / 9, places=6)
        self.assertAlmostEqual(mn, (80 - 32) * 5 / 9, places=6)
        # 97F -> 36.1C -> settlement bucket 36 (round-half-up), the contract value.
        self.assertEqual(round(mx), 36)

    def test_unknown_station_returns_empty(self):
        s = Sources(http=_FakeHTTP([]))
        self.assertEqual(
            s.wunderground_daily_series(
                "ZZZZ", dt.date(2026, 6, 1), dt.date(2026, 6, 5), "Asia/Manila"),
            {})

    def test_wu_truth_station_matches_manila_only(self):
        manila = Place("Manila, Philippines", "PH", 14.6, 121.0, "Asia/Manila")
        london = Place("London, United Kingdom", "GB", 51.5, -0.05, "Europe/London")
        hk = Place("Hong Kong", "HK", 22.3, 114.2, "Asia/Hong_Kong")
        self.assertEqual((_wu_truth_station(manila) or {}).get("icao"), "RPLL")
        self.assertIsNone(_wu_truth_station(london))
        self.assertIsNone(_wu_truth_station(hk))


if __name__ == "__main__":
    unittest.main()
