"""Network-free tests for the tracked-forecaster ledger in storage.py.

These prove the recommend-only, PROSPECTIVE head-to-head record is honest:
  * a forecast logs once per (source, place, target) — re-logging is ignored, so
    the comparison is pinned to one lead and never silently re-based;
  * settlement scores against anchored truth (here the ERA5 path via a fake
    Sources) and only after the lag cutoff;
  * the score is a true apples-to-apples MAE over the SAME settled days, counts
    both high and low error, and ignores rows where a side lacks a forecast.

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
from weather_council.sources import Place


def _place() -> Place:
    return Place(name="London", country="UK", latitude=51.5, longitude=-0.1,
                 timezone="Europe/London")


class _FakeSources:
    """Returns canned ERA5 'truth' for the settlement path; no network."""

    def __init__(self, archive: dict[str, tuple[float, float]]):
        self.archive = archive

    def fetch_archive_series(self, place, start, end):
        return self.archive


class TestTrackedLedger(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patch = mock.patch.object(storage, "DB_PATH", Path(self._tmp.name))
        self._patch.start()
        # A target safely past the >2-day settlement cutoff.
        self.target = (dt.date.today() - dt.timedelta(days=6)).isoformat()

    def tearDown(self):
        self._patch.stop()
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_log_is_idempotent_per_source_place_target(self):
        storage.log_tracked_forecast("weatherbit", _place(), self.target,
                                     20.0, 10.0, 19.0, 11.0, None)
        # A second log for the same key must be IGNORED (keeps the first lead).
        storage.log_tracked_forecast("weatherbit", _place(), self.target,
                                     99.0, 99.0, 99.0, 99.0, None)
        conn = storage._connect()
        rows = conn.execute(
            "SELECT fc_high, fc_low FROM tracked_forecasts").fetchall()
        conn.close()
        self.assertEqual(rows, [(20.0, 10.0)])

    def test_settle_then_score_head_to_head(self):
        # Weatherbit: high 20/low 10. Council: high 19/low 11. Truth: 21/12.
        storage.log_tracked_forecast("weatherbit", _place(), self.target,
                                     20.0, 10.0, 19.0, 11.0, None)
        fake = _FakeSources({self.target: (21.0, 12.0)})
        notes = storage.settle_tracked_forecasts(fake)
        self.assertEqual(len(notes), 1)
        sc = storage.tracked_forecast_scores("weatherbit")
        self.assertEqual(sc["n"], 1)
        # Weatherbit errors: |20-21|=1, |10-12|=2 -> mean 1.5
        self.assertAlmostEqual(sc["source_mae"], 1.5, places=9)
        # Council errors:    |19-21|=2, |11-12|=1 -> mean 1.5
        self.assertAlmostEqual(sc["council_mae"], 1.5, places=9)

    def test_unsettled_day_does_not_score(self):
        # Target inside the cutoff window (today) must not settle yet.
        recent = dt.date.today().isoformat()
        storage.log_tracked_forecast("weatherbit", _place(), recent,
                                     20.0, 10.0, 19.0, 11.0, None)
        fake = _FakeSources({recent: (21.0, 12.0)})
        storage.settle_tracked_forecasts(fake)
        self.assertEqual(storage.tracked_forecast_scores("weatherbit")["n"], 0)

    def test_missing_council_forecast_excluded_from_score(self):
        storage.log_tracked_forecast("weatherbit", _place(), self.target,
                                     20.0, 10.0, None, None, None)
        fake = _FakeSources({self.target: (21.0, 12.0)})
        storage.settle_tracked_forecasts(fake)
        sc = storage.tracked_forecast_scores("weatherbit")
        self.assertEqual(sc["n"], 0)             # no council side -> not comparable
        self.assertIsNone(sc["source_mae"])

    def test_truth_absent_leaves_row_open(self):
        storage.log_tracked_forecast("weatherbit", _place(), self.target,
                                     20.0, 10.0, 19.0, 11.0, None)
        fake = _FakeSources({})                  # truth not in yet
        self.assertEqual(storage.settle_tracked_forecasts(fake), [])
        self.assertEqual(storage.tracked_forecast_scores("weatherbit")["n"], 0)

    def test_station_settlement_reconstructs_station_identity(self):
        """A station-anchored tracked forecast must persist the anchor's icao+name,
        and settlement must rebuild the EXACT Station carrying them — so
        fetch_station_daily's modern truth overlay fires (EGLC by icao). Regression
        guard: a blank Station (icao=None, name="") made settlement read only the
        stale bulk Meteostat file (London EGLC0 ends ~March) and never grade recent
        days. We assert BOTH that the rebuilt Station carries the identity AND that
        the day settles. Mirrors test_edge.py::test_settlement_reconstructs_station_identity."""
        truth_source = {"kind": "station",
                        "station": {"id": "EGLC0", "name": "London / City Airport",
                                    "icao": "EGLC", "wmo": None,
                                    "latitude": 51.5, "longitude": 0.1167}}
        storage.log_tracked_forecast("weatherbit", _place(), self.target,
                                     20.0, 10.0, 19.0, 11.0, truth_source)
        # The anchor identity must actually land in the row.
        conn = storage._connect()
        row = conn.execute(
            "SELECT station_icao, station_name FROM tracked_forecasts").fetchone()
        conn.close()
        self.assertEqual(row, ("EGLC", "London / City Airport"))

        # Fake sources that CAPTURE the Station settlement rebuilds and report a
        # 19/11 day for the target — the value the real EGLC record holds.
        seen = []
        def fake_fetch(st):
            seen.append(st)
            return {self.target: (19.0, 11.0)}
        fake = types.SimpleNamespace(fetch_station_daily=fake_fetch)

        notes = storage.settle_tracked_forecasts(fake)
        self.assertEqual(len(notes), 1)
        # The regression assertion: the rebuilt Station carries the real identity,
        # not a blank one. Pre-fix this was icao=None, name="".
        self.assertEqual((seen[0].icao, seen[0].name),
                         ("EGLC", "London / City Airport"))

        sc = storage.tracked_forecast_scores("weatherbit")
        self.assertEqual(sc["n"], 1)
        # source: |20-19|=1, |10-11|=1 -> 1.0 ; council: |19-19|=0, |11-11|=0 -> 0.0
        self.assertAlmostEqual(sc["source_mae"], 1.0, places=9)
        self.assertAlmostEqual(sc["council_mae"], 0.0, places=9)


if __name__ == "__main__":
    unittest.main()
