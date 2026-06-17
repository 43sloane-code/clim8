"""Network-free regression test pinning the per-station request-budget fix in
`storage.settle_market_snapshots`.

THE BUG. `fetch_station_daily` returns the weeks-stale Meteostat bulk file PLUS a
modern recent-day overlay (HKO open-data for the Observatory, IEM ASOS METAR for
EGLC). The overlay alone costs many requests, all drawn from the SafeHTTPClient's
shared per-run budget (`MAX_REQUESTS_PER_RUN`). Settlement used to build ONE
`Sources()` and reuse it — hence one shared budget — across every station's fetch.
A heavy early fetch exhausted the budget, after which a LATER station's overlay
silently failed and the series fell back to the bulk file (lagging ~weeks). The
target day was then absent, `series.get(target)` was None, and that station's
recent rows never settled — leaving `realized_label` NULL and starving both the
proxy-vs-contract alignment alarm and the C7 edge scorer.

THE FIX. On the production path (no injected `Sources`), settlement builds a FRESH
`Sources` per unique station, so each station fetch gets its own request budget and
a heavy early fetch can no longer starve a later station's overlay.

This test models the shared budget with a fake whose recent-day overlay is present
only while a PER-INSTANCE budget remains. Two stations are settled in one call:
under the old shared-`Sources` code only the first keeps its overlay (1 of 2
settles); under the fix each station gets its own instance and both settle.

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


# The in-cutoff day both stations must settle (cutoff = today - 2 days). The
# modern recent-day overlay carries it; the stale bulk file holds only an OLD day,
# so a station that loses its overlay cannot settle.
_TARGET = (dt.date.today() - dt.timedelta(days=4)).isoformat()
_OVERLAY_HIGH, _OVERLAY_LOW = 19.0, 11.0
_STALE_DAY = "1990-01-01"


class _BudgetStarvedSources:
    """Stand-in for `sources.Sources` that mimics the shared per-run request
    budget. `fetch_station_daily` always returns the weeks-stale bulk series, and
    ADDS the modern recent-day overlay (the target date) ONLY while this instance
    still has overlay budget — exactly how the real overlay silently drops once
    the shared `MAX_REQUESTS_PER_RUN` is spent. One fetch exhausts the budget, so a
    SECOND station fetched on the SAME instance loses its overlay; a fresh instance
    per station restores it. Counts instances so the test can show the isolation."""

    instances = 0
    OVERLAY_BUDGET = 1

    def __init__(self, *args, **kwargs):
        type(self).instances += 1
        self._overlays_left = self.OVERLAY_BUDGET

    def fetch_station_daily(self, station):
        series = {_STALE_DAY: (10.0, 2.0)}          # bulk file: no recent days
        if self._overlays_left > 0:
            self._overlays_left -= 1
            series[_TARGET] = (_OVERLAY_HIGH, _OVERLAY_LOW)
        return series


class TestSettleStationBudget(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patch = mock.patch.object(storage, "DB_PATH", Path(self._tmp.name))
        self._patch.start()
        _BudgetStarvedSources.instances = 0
        # Two DISTINCT settlement stations, both with the same in-cutoff target
        # day: Hong Kong (HKO Observatory id 45005) and London City (EGLC).
        hk = _Place("Hong Kong, HK", 22.30, 114.17)
        ldn = _Place("London, United Kingdom", 51.50, 0.1167)
        ladder = _ladder(15, 23)
        storage.log_market_snapshot(
            _verdict(hk, _TARGET, "45005", "Hong Kong Observatory", None),
            _Comparison("HK highest temperature", "C", ladder))
        storage.log_market_snapshot(
            _verdict(ldn, _TARGET, "EGLC0", "London / City Airport", "EGLC"),
            _Comparison("London highest temperature", "C", ladder))

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

    def test_second_station_still_settles_when_budget_would_starve(self):
        """With a shared `Sources`, the SECOND station's overlay is starved and it
        never settles (1 of 2). The fix gives each station its own `Sources`, so a
        heavy early fetch can't exhaust a later station's budget — BOTH settle."""
        with mock.patch.object(storage, "Sources", _BudgetStarvedSources):
            report = storage.settle_market_snapshots()   # production path, no inject

        self.assertEqual(self._settled_label("Hong Kong, HK"), "19°C")
        self.assertEqual(self._settled_label("London, United Kingdom"), "19°C",
                         "second station lost its overlay — the shared-budget bug")
        self.assertEqual(len(report), 2)
        # Mechanistic corroboration: each unique station was fetched on its own
        # Sources instance (≥2 across the two stations), not one shared instance.
        self.assertGreaterEqual(_BudgetStarvedSources.instances, 2)

    def test_injected_sources_is_honored_unchanged(self):
        """An explicitly injected Sources (the duck-typed stub the other tests use)
        must still be used as-is — the per-station-Sources path is the PRODUCTION
        path only, so injection keeps full control and stays network-free."""
        seen = []

        def fake_fetch(station):
            seen.append(station.id)
            return {_TARGET: (19.0, 11.0)}

        stub = types.SimpleNamespace(fetch_station_daily=fake_fetch)
        # If settlement ignored the injection and built its own Sources, this stub
        # would never be called and `seen` would stay empty.
        with mock.patch.object(storage, "Sources", _BudgetStarvedSources):
            report = storage.settle_market_snapshots(stub)

        self.assertEqual(len(report), 2)
        self.assertEqual(sorted(seen), ["45005", "EGLC0"])
        self.assertEqual(_BudgetStarvedSources.instances, 0)   # no real Sources built
        self.assertEqual(self._settled_label("Hong Kong, HK"), "19°C")
        self.assertEqual(self._settled_label("London, United Kingdom"), "19°C")


if __name__ == "__main__":
    unittest.main()
