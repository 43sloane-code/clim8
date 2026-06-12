"""Network-free tests for the intraday running-max dead-bucket eliminator
(ledger candidate 48).

The contract under test: every observation recorded so far today is a HARD lower
bound on the day's final max, and the settlement quantizer is monotone, so
buckets strictly below the running max's bucket are mechanically impossible. The
annotation is READ-ONLY and must produce ZERO false eliminations — the bucket the
running max itself lands in is always still alive, and a feed failure eliminates
nothing (and says so).

Run with:  PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import datetime as dt
import unittest

from weather_council.sources import Place
from weather_council.intraday import intraday_floor, IntradayFloor


HK = Place(name="Hong Kong", country="HK", latitude=22.30, longitude=114.17,
           timezone="Asia/Hong_Kong")
LDN = Place(name="London", country="GB", latitude=51.51, longitude=-0.13,
            timezone="Europe/London")
ELSEWHERE = Place(name="Tokyo", country="JP", latitude=35.68, longitude=139.69,
                  timezone="Asia/Tokyo")

TODAY = dt.date(2026, 6, 12)


class FakeSources:
    """Minimal stand-in: serves a canned EGLC METAR series and an HKO reading."""
    def __init__(self, metar=None, hko=None, raise_metar=False, raise_hko=False):
        self._metar = metar or []
        self._hko = hko
        self._raise_metar = raise_metar
        self._raise_hko = raise_hko
        self.metar_calls = []

    def fetch_metar_observations(self, icao, start, end, timezone):
        self.metar_calls.append((icao, start, end, timezone))
        if self._raise_metar:
            raise RuntimeError("IEM archive unreachable")
        return list(self._metar)

    def hko_current(self):
        if self._raise_hko:
            raise RuntimeError("HKO feed unreachable")
        return self._hko


class TestApplicabilityGates(unittest.TestCase):
    def test_non_basket_city_is_noop(self):
        f = intraday_floor(ELSEWHERE, TODAY, sources=FakeSources(), today=TODAY)
        self.assertEqual(f.kind, "not_basket")
        self.assertIsNone(f.floor_bucket)
        self.assertFalse(f.is_dead(0))

    def test_future_target_eliminates_nothing(self):
        src = FakeSources(metar=[("2026-06-13 14:00", 25.0)])
        f = intraday_floor(LDN, TODAY + dt.timedelta(days=1), sources=src, today=TODAY)
        self.assertEqual(f.kind, "not_today")
        self.assertIsNone(f.floor_bucket)
        # Must not have hit the network for a day with no observations yet.
        self.assertEqual(src.metar_calls, [])

    def test_past_target_is_not_today(self):
        f = intraday_floor(LDN, TODAY - dt.timedelta(days=1),
                           sources=FakeSources(), today=TODAY)
        self.assertEqual(f.kind, "not_today")
        self.assertIn("already settled", f.note)


class TestFeedSafety(unittest.TestCase):
    def test_eglc_feed_error_is_unverified_not_clear(self):
        src = FakeSources(raise_metar=True)
        f = intraday_floor(LDN, TODAY, sources=src, today=TODAY)
        self.assertEqual(f.kind, "unverified")
        self.assertFalse(f.is_dead(99))          # eliminates nothing
        self.assertIsNotNone(f.note)

    def test_empty_obs_is_unverified(self):
        src = FakeSources(metar=[])              # archive returned nothing yet
        f = intraday_floor(LDN, TODAY, sources=src, today=TODAY)
        self.assertEqual(f.kind, "unverified")
        self.assertIsNone(f.floor_bucket)

    def test_hko_feed_down_is_unverified(self):
        f = intraday_floor(HK, TODAY, sources=FakeSources(hko=None), today=TODAY)
        self.assertEqual(f.kind, "unverified")
        self.assertFalse(f.is_dead(10))

    def test_no_sources_is_unverified(self):
        f = intraday_floor(LDN, TODAY, sources=None, today=TODAY)
        self.assertEqual(f.kind, "unverified")


class TestLondonRoundHalfUp(unittest.TestCase):
    def test_running_max_picks_day_peak_and_buckets_round_half_up(self):
        # Peak 21.6 on the target day -> round-half-up -> bucket 22 guaranteed.
        src = FakeSources(metar=[
            ("2026-06-12 08:00", 17.0),
            ("2026-06-12 13:00", 21.6),   # the running max
            ("2026-06-12 15:00", 20.4),
            ("2026-06-11 14:00", 28.0),   # yesterday — MUST be excluded
        ])
        f = intraday_floor(LDN, TODAY, sources=src, today=TODAY)
        self.assertEqual(f.kind, "floor")
        self.assertAlmostEqual(f.running_max_c, 21.6)
        self.assertEqual(f.floor_bucket, 22)
        self.assertEqual(f.n_obs, 3)               # yesterday's obs dropped
        self.assertEqual(f.record_time, "2026-06-12T13:00")

    def test_dead_buckets_strictly_below_floor(self):
        src = FakeSources(metar=[("2026-06-12 13:00", 21.6)])
        f = intraday_floor(LDN, TODAY, sources=src, today=TODAY)
        self.assertTrue(f.is_dead(21))             # below floor -> impossible
        self.assertTrue(f.is_dead(20))
        self.assertFalse(f.is_dead(22))            # the floor bucket is STILL ALIVE
        self.assertFalse(f.is_dead(23))            # above floor -> still possible

    def test_half_rounds_up_at_boundary(self):
        # 21.5 -> round-half-up -> 22 (NOT 21).
        src = FakeSources(metar=[("2026-06-12 12:00", 21.5)])
        f = intraday_floor(LDN, TODAY, sources=src, today=TODAY)
        self.assertEqual(f.floor_bucket, 22)


class TestHongKongFloor(unittest.TestCase):
    def test_current_reading_is_lower_bound_floor_rule(self):
        # HK floor: 28.6 -> bucket 28 (NOT 29). 27 and below are dead; 28 alive.
        src = FakeSources(hko={"temperature_2m": 28.6,
                               "record_time": "2026-06-12T14:30"})
        f = intraday_floor(HK, TODAY, sources=src, today=TODAY)
        self.assertEqual(f.kind, "floor")
        self.assertAlmostEqual(f.running_max_c, 28.6)
        self.assertEqual(f.floor_bucket, 28)
        self.assertTrue(f.is_dead(27))
        self.assertFalse(f.is_dead(28))            # zero false elimination
        self.assertFalse(f.is_dead(29))
        self.assertEqual(f.n_obs, 1)

    def test_integer_reading_floor(self):
        # 30.0 -> floor 30. Bucket 29 dead, 30 alive.
        src = FakeSources(hko={"temperature_2m": 30.0, "record_time": None})
        f = intraday_floor(HK, TODAY, sources=src, today=TODAY)
        self.assertEqual(f.floor_bucket, 30)
        self.assertTrue(f.is_dead(29))
        self.assertFalse(f.is_dead(30))


class TestReadOnlyDataclass(unittest.TestCase):
    def test_frozen(self):
        f = IntradayFloor(kind="floor", city="London", target="2026-06-12",
                          sub_degree=False, floor_bucket=22)
        with self.assertRaises(Exception):
            f.floor_bucket = 5  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
