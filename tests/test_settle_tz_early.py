"""Network-free KAT pinning the tz-aware early-settle in
`storage.settle_market_snapshots`.

THE SEAM (2026-07-08 audit). `realized_label` (the anchor-station PROXY the
proxy-vs-contract alignment alarm cross-checks) is written by this function; the
contract's own `pm_resolved_label` is backfilled at T-1. Settlement used ONE
blanket host cutoff of `today - 2 days`, so a WU-oracle day that had already
finished in its CITY-LOCAL timezone (and had already resolved on the contract at
T-1) still sat unsettled for an extra ~24h. That one-day skew left the alarm blind
on the freshest resolved day — London 07-07 showed `pm_resolved_label=32°C` with
`realized_label=None`, so the divergence could not even be evaluated.

THE FIX (ported from the already-audited sibling `settle_tracked_forecasts`). The
host cutoff is a broad prefilter only; per row, a WU-oracle station settles once
its CITY-LOCAL day is over (T-1), while lagged-truth stations (Meteostat bulk
file) keep the conservative 2-day buffer. The realized bucket is unchanged — WU
dailies do not revise — only the day it lands is earlier.

Four cases, all deterministic (targets are relative to the SAME city-local clock
the code reads, so no wall-clock injection is needed) and network-free (an
injected stub Sources supplies every reading):
  1. WU city (EGLC), city-local YESTERDAY  -> SETTLES  (the early-settle)
  2. WU city (EGLC), city-local TODAY       -> SKIPPED  (in-progress; no leak)
  3. WU city (WSSS), city-local YESTERDAY   -> SETTLES  (per-city tz, not just London)
  4. lagged station (HKO id, no icao), T-1  -> SKIPPED  (2-day buffer preserved)
     lagged station (HKO id, no icao), T-4  -> SETTLES  (buffer clears)

Run with:  PYTHONPATH=. python3 -m unittest tests.test_settle_tz_early -v
"""
from __future__ import annotations

import datetime as dt
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from weather_council import storage


def _city_local_today(tz: str) -> dt.date:
    """The same city-local calendar date settle_market_snapshots reads."""
    return dt.datetime.now(ZoneInfo(tz)).date()


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
    return [_bucket(f"{n}°C", n, n) for n in range(lo, hi + 1)]


class _Comparison:
    def __init__(self, title, grain, buckets):
        self.market_title = title
        self.grain = grain
        self.buckets = buckets
        self.settles_sub_degree = False
        self.market_volume = None
        self.market_liquidity = None


def _verdict(place, target, station_id, name, icao):
    return types.SimpleNamespace(
        place=place, target=target, high=19.0, low=11.0, confidence="MODERATE",
        truth_source={"kind": "station",
                      "station": {"id": station_id, "name": name, "icao": icao}})


_SETTLE_HIGH, _SETTLE_LOW = 19.0, 11.0     # -> bucket "19°C" in a 15..23 ladder


class _StubSources:
    """Duck-typed stand-in that HAS a reading for every seeded target on every
    path, so anything the timing guard lets through settles — and anything it
    skips stays NULL because the fetch is never reached. Network-free."""

    def __init__(self, days: set[str]):
        self._days = days

    def wunderground_daily_series(self, icao, start, end, timezone):
        s, e = start.isoformat(), end.isoformat()
        return {d: (_SETTLE_HIGH, _SETTLE_LOW) for d in self._days if s <= d <= e}

    def fetch_station_daily(self, station):
        return {d: (_SETTLE_HIGH, _SETTLE_LOW) for d in self._days}


class TestSettleTzEarly(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patch = mock.patch.object(storage, "DB_PATH", Path(self._tmp.name))
        self._patch.start()

        ldn = _Place("London, United Kingdom", 51.50, 0.1167)
        sg = _Place("Singapore, Singapore", 1.35, 103.99)
        hk = _Place("Hong Kong, HK", 22.30, 114.17)
        ladder = _ladder(15, 23)

        # City-local reference dates (the exact clock the code consults).
        ldn_today = _city_local_today("Europe/London")
        sg_today = _city_local_today("Asia/Singapore")
        host_today = dt.date.today()

        self.t_ldn_yesterday = (ldn_today - dt.timedelta(days=1)).isoformat()
        self.t_ldn_today = ldn_today.isoformat()
        self.t_sg_yesterday = (sg_today - dt.timedelta(days=1)).isoformat()
        self.t_hk_t1 = (host_today - dt.timedelta(days=1)).isoformat()
        self.t_hk_t4 = (host_today - dt.timedelta(days=4)).isoformat()

        seeds = [
            (_verdict(ldn, self.t_ldn_yesterday, "EGLC0", "London / City Airport", "EGLC"),
             "London highest temperature (settles)"),
            (_verdict(ldn, self.t_ldn_today, "EGLC0", "London / City Airport", "EGLC"),
             "London highest temperature (in progress)"),
            (_verdict(sg, self.t_sg_yesterday, "WSSS0", "Singapore Changi", "WSSS"),
             "Singapore highest temperature (settles)"),
            (_verdict(hk, self.t_hk_t1, "45005", "Hong Kong Observatory", None),
             "HK highest temperature (T-1, buffered)"),
            (_verdict(hk, self.t_hk_t4, "45005", "Hong Kong Observatory", None),
             "HK highest temperature (T-4, clears)"),
        ]
        for v, title in seeds:
            storage.log_market_snapshot(v, _Comparison(title, "C", ladder))

        self._all_days = {
            self.t_ldn_yesterday, self.t_ldn_today, self.t_sg_yesterday,
            self.t_hk_t1, self.t_hk_t4,
        }

    def tearDown(self):
        self._patch.stop()
        Path(self._tmp.name).unlink(missing_ok=True)

    def _label(self, place_label, target):
        conn = storage._connect()
        row = conn.execute(
            "SELECT realized_label FROM market_snapshots WHERE place=? AND target_date=?",
            (place_label, target)).fetchone()
        conn.close()
        return row[0] if row else None

    def test_tz_aware_early_settle(self):
        storage.settle_market_snapshots(_StubSources(self._all_days))

        # 1. WU city, city-local yesterday -> the early-settle fires (was quarantined
        #    an extra day under the old blanket today-2 cutoff).
        self.assertEqual(
            self._label("London, United Kingdom", self.t_ldn_yesterday), "19°C",
            "EGLC's completed city-local day must settle at T-1, not wait for T-2")

        # 2. WU city, city-local TODAY -> must NOT settle (peak still forming; a leak).
        self.assertIsNone(
            self._label("London, United Kingdom", self.t_ldn_today),
            "EGLC's in-progress city-local day must stay unsettled")

        # 3. A DIFFERENT WU city resolves on its OWN tz, proving it is per-city.
        self.assertEqual(
            self._label("Singapore, Singapore", self.t_sg_yesterday), "19°C",
            "WSSS's completed city-local day must settle at T-1 on Singapore time")

        # 4. Lagged-truth station keeps the conservative 2-day buffer: T-1 waits,
        #    T-4 clears. (Guards against a naive 'settle everything at T-1' rewrite.)
        self.assertIsNone(
            self._label("Hong Kong, HK", self.t_hk_t1),
            "lagged-truth station must keep the 2-day buffer (T-1 not yet due)")
        self.assertEqual(
            self._label("Hong Kong, HK", self.t_hk_t4), "19°C",
            "lagged-truth station settles once the 2-day buffer clears")


if __name__ == "__main__":
    unittest.main()
