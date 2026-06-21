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

    def test_wu_truth_station_routing(self):
        manila = Place("Manila, Philippines", "PH", 14.6, 121.0, "Asia/Manila")
        singapore = Place("Singapore", "SG", 1.29, 103.85, "Asia/Singapore")
        london = Place("London, United Kingdom", "GB", 51.5, -0.05, "Europe/London")
        hk = Place("Hong Kong", "HK", 22.3, 114.2, "Asia/Hong_Kong")
        self.assertEqual((_wu_truth_station(manila) or {}).get("icao"), "RPLL")
        self.assertEqual((_wu_truth_station(singapore) or {}).get("icao"), "WSSS")
        self.assertIsNone(_wu_truth_station(london))   # London stays on IEM-EGLC truth
        self.assertIsNone(_wu_truth_station(hk))

    def test_hourly_observations_shape_fc_sorted(self):
        # 4 obs on one local day; whole-°F -> °C, drop-in (ts, temp_c) shape.
        obs = _obs_for_day(dt.date(2026, 6, 10), [80, 82, 88, 90])
        s = Sources(http=_FakeHTTP(obs))
        series = s.wunderground_hourly_observations(
            "WSSS", dt.date(2026, 6, 10), dt.date(2026, 6, 10), "Asia/Singapore")
        self.assertEqual(len(series), 4)
        self.assertEqual(series, sorted(series))                     # time-sorted
        for ts, c in series:                                        # 'YYYY-MM-DD HH:MM'
            self.assertEqual(len(ts), 16)
            self.assertEqual(ts[10], " ")
            self.assertIsInstance(c, float)
        self.assertAlmostEqual(max(c for _, c in series), (90 - 32) * 5 / 9, places=6)

    def test_hourly_observations_unknown_station_empty(self):
        s = Sources(http=_FakeHTTP([]))
        self.assertEqual(
            s.wunderground_hourly_observations(
                "ZZZZ", dt.date(2026, 6, 1), dt.date(2026, 6, 2), "Asia/Singapore"),
            [])


class _SpySources:
    """Serves one synthetic obs set (the shared (ts, temp_c) shape) from BOTH
    hourly feeds and records which one the lever actually read."""

    def __init__(self, obs):
        self._obs = obs
        self.wu_called = False
        self.iem_called = False

    def wunderground_hourly_observations(self, icao, start, end, tz):
        self.wu_called = True
        return self._obs

    def fetch_metar_observations(self, icao, start, end, tz):
        self.iem_called = True
        return self._obs


def _synth_hourly(target: dt.date, n_days: int = 45) -> list[tuple[str, float]]:
    """n_days+1 days ending at target, each a clean diurnal peak at 14:00 so the
    post-peak (15:00) running max settles the bucket; (ts, temp_c) shape."""
    out = []
    for d in range(n_days, -1, -1):
        day = target - dt.timedelta(days=d)
        base = 26.0 + (d % 3)
        for hh in range(6, 19):
            c = base + max(0.0, 6.0 - abs(hh - 14) * 1.2)
            out.append((f"{day.isoformat()} {hh:02d}:00", round(c, 1)))
    return out


class TestIntradayFeedSelection(unittest.TestCase):
    """The WU-native switch: Singapore reads Wunderground, others stay on IEM."""

    def test_singapore_reads_wu_feed(self):
        from weather_council.intraday_ceiling import intraday_ceiling
        target = dt.date(2026, 6, 21)
        spy = _SpySources(_synth_hourly(target))
        sg = Place("Singapore", "SG", 1.29, 103.85, "Asia/Singapore")
        r = intraday_ceiling(sg, target, sources=spy, today=target, now_hour=15)
        self.assertTrue(spy.wu_called)
        self.assertFalse(spy.iem_called)
        self.assertEqual(r.kind, "sharpened")
        self.assertIn("Wunderground", r.source or "")

    def test_manila_stays_on_iem_feed(self):
        from weather_council.intraday_ceiling import intraday_ceiling
        target = dt.date(2026, 6, 21)
        spy = _SpySources(_synth_hourly(target))
        mnl = Place("Manila, Philippines", "PH", 14.6, 121.0, "Asia/Manila")
        r = intraday_ceiling(mnl, target, sources=spy, today=target, now_hour=15)
        self.assertTrue(spy.iem_called)
        self.assertFalse(spy.wu_called)
        self.assertIn("IEM", r.source or "")


if __name__ == "__main__":
    unittest.main()
