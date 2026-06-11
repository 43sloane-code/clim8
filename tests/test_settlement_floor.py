"""Network-free regression test pinning the sub-degree FLOOR settlement rule.

The Polymarket Hong Kong market resolves to "the temperature RANGE that contains
the highest temperature ... 'Absolute Daily Max (deg. C)' ... to one decimal
place." A whole-degree bucket "N°C" is therefore the half-open range [N, N+1),
so settlement is the FLOOR of the 0.1°C reading: 28.6°C lands in the 28°C bucket,
NOT 29 (round-half-up). Mis-floor was the entire 06-10 Hong Kong settlement miss
(verdict 28.x -> 29, market resolved 28).

London (City Airport, EGLC) settles at WHOLE °C, so its native reading is
round-half-up: 17.6°C -> 18. These two cases lock both halves of the convention
through the real `storage.settle_market_snapshots` path so the fix cannot silently
revert.

Run with:  PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import datetime as dt
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from weather_council import storage


class _Place:
    def __init__(self, label, lat, lon):
        self._label, self.latitude, self.longitude = label, lat, lon

    def label(self):
        return self._label


def _bucket(label, lo, hi):
    return types.SimpleNamespace(
        label=label, lo=lo, hi=hi, model_prob=0.0, market_prob=0.0,
        market_yes=None, market_liquidity=None, market_volume=None,
        best_bid=None, best_ask=None, last_trade=None, two_sided=None)


def _ladder(lo, hi):
    """Whole-degree bucket ladder [lo, hi] inclusive, e.g. 25..31°C."""
    return [_bucket(f"{n}°C", n, n) for n in range(lo, hi + 1)]


class _Comparison:
    def __init__(self, title, grain, buckets, sub_degree):
        self.market_title = title
        self.grain = grain
        self.buckets = buckets
        self.settles_sub_degree = sub_degree
        self.market_volume = None
        self.market_liquidity = None


def _verdict(place, target, high, low, truth_source):
    return types.SimpleNamespace(
        place=place, target=target, high=high, low=low,
        confidence="MODERATE", truth_source=truth_source)


class TestFloorSettlement(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patch = mock.patch.object(storage, "DB_PATH", Path(self._tmp.name))
        self._patch.start()
        # well inside the 2-day settlement hold (cutoff = today - 2 days)
        self.target = (dt.date.today() - dt.timedelta(days=4)).isoformat()

    def tearDown(self):
        self._patch.stop()
        Path(self._tmp.name).unlink(missing_ok=True)

    def _settled_label(self, place_label):
        conn = storage._connect()
        row = conn.execute(
            "SELECT realized_label FROM market_snapshots WHERE place=?",
            (place_label,)).fetchone()
        conn.close()
        return row[0] if row else None

    def test_hk_sub_degree_floors_28_6_to_28(self):
        """Hong Kong (HKO Observatory, 0.1°C) — 28.6°C must FLOOR into 28°C,
        the exact 06-10 case that previously mis-settled to 29."""
        hk = _Place("Hong Kong, HK", 22.30, 114.17)
        ts = {"kind": "station",
              "station": {"id": "45005", "name": "Hong Kong Observatory",
                          "icao": None}}
        cmp_ = _Comparison("HK highest temperature", "C",
                           _ladder(25, 31), sub_degree=True)
        storage.log_market_snapshot(_verdict(hk, self.target, 28.9, 25.7, ts), cmp_)

        fake = types.SimpleNamespace(
            fetch_station_daily=lambda st: {self.target: (28.6, 25.0)})
        report = storage.settle_market_snapshots(fake)

        self.assertEqual(len(report), 1)
        self.assertEqual(self._settled_label("Hong Kong, HK"), "28°C")

    def test_hk_sub_degree_persists_flag(self):
        """The sub_degree flag must be persisted from the comparison so settlement
        knows to floor even if the column is read back cold."""
        hk = _Place("Hong Kong, HK", 22.30, 114.17)
        ts = {"kind": "station",
              "station": {"id": "45005", "name": "Hong Kong Observatory",
                          "icao": None}}
        cmp_ = _Comparison("HK highest temperature", "C",
                           _ladder(25, 31), sub_degree=True)
        storage.log_market_snapshot(_verdict(hk, self.target, 28.9, 25.7, ts), cmp_)
        conn = storage._connect()
        sd = conn.execute(
            "SELECT sub_degree FROM market_snapshots WHERE place=?",
            ("Hong Kong, HK",)).fetchone()[0]
        conn.close()
        self.assertEqual(sd, 1)

    def test_london_whole_degree_rounds_17_6_to_18(self):
        """London (City Airport, EGLC) settles at WHOLE °C — round-half-up, NOT
        floor — so 17.6°C -> 18°C. Pins that the floor rule does NOT leak into
        whole-degree markets."""
        london = _Place("London, United Kingdom", 51.5, 0.1167)
        ts = {"kind": "station",
              "station": {"id": "EGLC0", "name": "London / City Airport",
                          "icao": "EGLC"}}
        cmp_ = _Comparison("London highest temperature", "C",
                           _ladder(13, 22), sub_degree=False)
        storage.log_market_snapshot(_verdict(london, self.target, 17.0, 12.0, ts), cmp_)

        fake = types.SimpleNamespace(
            fetch_station_daily=lambda st: {self.target: (17.6, 12.0)})
        report = storage.settle_market_snapshots(fake)

        self.assertEqual(len(report), 1)
        self.assertEqual(self._settled_label("London, United Kingdom"), "18°C")

    def test_london_whole_degree_floor_would_disagree(self):
        """Guard the previous test's strength: at 17.6°C floor and round-half-up
        DISAGREE (17 vs 18). London takes 18 — proving it is NOT floored."""
        from weather_council.market import _native_reading_int
        self.assertEqual(_native_reading_int(17.6, "C", sub_degree=True), 17)
        self.assertEqual(_native_reading_int(17.6, "C", sub_degree=False), 18)
        # And the HK floor case the regression exists for:
        self.assertEqual(_native_reading_int(28.6, "C", sub_degree=True), 28)
        self.assertEqual(_native_reading_int(28.6, "C", sub_degree=False), 29)


if __name__ == "__main__":
    unittest.main()
