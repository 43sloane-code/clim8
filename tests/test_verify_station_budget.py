"""Network-free regression test pinning the per-station request-budget fix in
`storage.verify` and `storage.settle_tracked_forecasts` — the SAME bug
`tests/test_settle_station_budget.py` pins for `settle_market_snapshots`.

THE BUG. Both verify paths built ONE `Sources()` and reused it — hence one shared
SafeHTTPClient request budget (`MAX_REQUESTS_PER_RUN`) — across EVERY unsettled
row's fetch. A heavy early fetch (the HKO Observatory overlay alone costs many
requests) exhausted the budget, after which a LATER city's overlay/WU fetch
silently failed, the fetch exception was swallowed into `series = {}` and CACHED
in `station_cache`, and that city's rows never settled.

THE FIX. On the production path (no injected `Sources`) each station/WU fetch
gets a FRESH `Sources` — its own request budget — exactly as
`settle_market_snapshots` already did. An injected `Sources` (tests / explicit
callers) is honored as-is.

The fake below models the shared budget: the recent-day overlay is present only
while a PER-INSTANCE budget remains. Two distinct stations are settled in one
call: under the old shared-`Sources` code only the first keeps its overlay (1 of
2 settles); under the fix each fetch gets its own instance and both settle.

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


def _verdict(place, target, station_id, name, icao):
    return types.SimpleNamespace(
        place=place, target=target, high=19.0, low=11.0, confidence="MODERATE",
        truth_source={"kind": "station",
                      "station": {"id": station_id, "name": name, "icao": icao}})


# The in-cutoff day both stations must settle (verify cutoff = today - 2 days).
# The modern recent-day overlay carries it; the stale bulk file holds only an OLD
# day, so a station that loses its overlay cannot settle.
_TARGET = (dt.date.today() - dt.timedelta(days=4)).isoformat()
_OVERLAY_HIGH, _OVERLAY_LOW = 19.0, 11.0
_STALE_DAY = "1990-01-01"

_HK = _Place("Hong Kong, HK", 22.30, 114.17)
_LDN = _Place("London, United Kingdom", 51.50, 0.1167)


class _BudgetStarvedSources:
    """Stand-in for `sources.Sources` that mimics the shared per-run request
    budget (same shape as tests/test_settle_station_budget.py). `fetch_station_daily`
    always returns the weeks-stale bulk series, and ADDS the modern recent-day
    overlay (the target date) ONLY while this instance still has overlay budget —
    exactly how the real overlay silently drops once the shared
    `MAX_REQUESTS_PER_RUN` is spent. One fetch exhausts the budget, so a SECOND
    station fetched on the SAME instance loses its overlay; a fresh instance per
    station restores it. Counts instances so the test can show the isolation."""

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

    def wunderground_daily_series(self, icao, start, end, timezone):
        # London (EGLC) settles on the WU oracle, but the fetch still draws from
        # the SAME shared per-run budget — a heavy earlier station can starve it
        # exactly as the overlay did. Present only while this instance has budget.
        if self._overlays_left > 0:
            self._overlays_left -= 1
            return {_TARGET: (_OVERLAY_HIGH, _OVERLAY_LOW)}
        return {}


class _DbTestCase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patch = mock.patch.object(storage, "DB_PATH", Path(self._tmp.name))
        self._patch.start()
        _BudgetStarvedSources.instances = 0

    def tearDown(self):
        self._patch.stop()
        Path(self._tmp.name).unlink(missing_ok=True)

    def _actual_high(self, table, place_label):
        conn = storage._connect()
        row = conn.execute(
            f"SELECT actual_high FROM {table} WHERE place=?",
            (place_label,)).fetchone()
        conn.close()
        return row[0] if row else None


class TestVerifyStationBudget(_DbTestCase):

    def setUp(self):
        super().setUp()
        # Two DISTINCT anchor stations, both with the same in-cutoff target day:
        # Hong Kong (HKO Observatory id 45005, station-fetch path) and London City
        # (EGLC, WU-oracle path).
        storage.log_verdict(_verdict(_HK, _TARGET, "45005", "Hong Kong Observatory", None))
        storage.log_verdict(_verdict(_LDN, _TARGET, "EGLC0", "London / City Airport", "EGLC"))

    def test_second_station_still_settles_when_budget_would_starve(self):
        """With a shared `Sources`, the SECOND station's overlay is starved and it
        never settles (1 of 2). The fix gives each fetch its own `Sources`, so a
        heavy early fetch can't exhaust a later station's budget — BOTH settle."""
        with mock.patch.object(storage, "Sources", _BudgetStarvedSources):
            report = storage.verify()                   # production path, no inject

        self.assertEqual(self._actual_high("verdicts", "Hong Kong, HK"), _OVERLAY_HIGH)
        self.assertEqual(self._actual_high("verdicts", "London, United Kingdom"),
                         _OVERLAY_HIGH,
                         "second station lost its overlay — the shared-budget bug")
        self.assertEqual(len(report), 2)
        # Mechanistic corroboration: each station was fetched on its own Sources
        # instance (≥2 across the two stations), not one shared instance.
        self.assertGreaterEqual(_BudgetStarvedSources.instances, 2)

    def test_injected_sources_is_honored_unchanged(self):
        """An explicitly injected Sources (the duck-typed stub the other tests use)
        must still be used as-is — the per-fetch-Sources path is the PRODUCTION
        path only, so injection keeps full control and stays network-free."""
        seen = []

        def fake_fetch(station):
            seen.append(station.id)
            return {_TARGET: (19.0, 11.0)}

        def fake_wu(icao, start, end, timezone):       # London's WU-oracle verify path
            seen.append(icao)
            return {_TARGET: (19.0, 11.0)}

        stub = types.SimpleNamespace(fetch_station_daily=fake_fetch,
                                     wunderground_daily_series=fake_wu)
        # If verify ignored the injection and built its own Sources, this stub
        # would never be called and `seen` would stay empty.
        with mock.patch.object(storage, "Sources", _BudgetStarvedSources):
            report = storage.verify(stub)

        self.assertEqual(len(report), 2)
        # HK verifies via fetch_station_daily (station id 45005); London via the WU
        # oracle (icao EGLC) — each still routed by its persisted identity.
        self.assertEqual(sorted(seen), ["45005", "EGLC"])
        self.assertEqual(_BudgetStarvedSources.instances, 0)   # no real Sources built
        self.assertEqual(self._actual_high("verdicts", "Hong Kong, HK"), 19.0)
        self.assertEqual(self._actual_high("verdicts", "London, United Kingdom"), 19.0)


class TestSettleTrackedStationBudget(_DbTestCase):

    def setUp(self):
        super().setUp()
        # Two tracked rows on the same two distinct anchors as above.
        for place, sid, name, icao in (
                (_HK, "45005", "Hong Kong Observatory", None),
                (_LDN, "EGLC0", "London / City Airport", "EGLC")):
            storage.log_tracked_forecast(
                "twc", place, _TARGET, 19.0, 11.0, None, None,
                {"kind": "station",
                 "station": {"id": sid, "name": name, "icao": icao}})

    def test_second_station_still_settles_when_budget_would_starve(self):
        """settle_tracked_forecasts had the same shared-budget shape as verify:
        one Sources across every row. The fix gives each station/WU fetch its own
        budget — BOTH tracked rows settle."""
        with mock.patch.object(storage, "Sources", _BudgetStarvedSources):
            report = storage.settle_tracked_forecasts()  # production path, no inject

        self.assertEqual(self._actual_high("tracked_forecasts", "Hong Kong, HK"),
                         _OVERLAY_HIGH)
        self.assertEqual(self._actual_high("tracked_forecasts", "London, United Kingdom"),
                         _OVERLAY_HIGH,
                         "second station lost its overlay — the shared-budget bug")
        self.assertEqual(len(report), 2)
        self.assertGreaterEqual(_BudgetStarvedSources.instances, 2)

    def test_injected_sources_is_honored_unchanged(self):
        seen = []

        def fake_fetch(station):
            seen.append(station.id)
            return {_TARGET: (19.0, 11.0)}

        def fake_wu(icao, start, end, timezone):
            seen.append(icao)
            return {_TARGET: (19.0, 11.0)}

        stub = types.SimpleNamespace(fetch_station_daily=fake_fetch,
                                     wunderground_daily_series=fake_wu)
        with mock.patch.object(storage, "Sources", _BudgetStarvedSources):
            report = storage.settle_tracked_forecasts(stub)

        self.assertEqual(len(report), 2)
        self.assertEqual(sorted(seen), ["45005", "EGLC"])
        self.assertEqual(_BudgetStarvedSources.instances, 0)   # no real Sources built


if __name__ == "__main__":
    unittest.main()
