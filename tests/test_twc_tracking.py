"""KAT for the TWC prospective-tracking accrual contract (Plan 4 Phase 2).

The clock is already live: twc_forecast_logger runs daily in accumulate, logging source='twc' rows
into tracked_forecasts and settling them against the identical anchored WU oracle. This test PINS
that contract so a future refactor can't silently break the precious, unbackfillable accrual:
  * the PK (source, place, target_date) is idempotent — INSERT OR IGNORE keeps the FIRST forecast
    seen for a day (pinned lead), never silently re-based by a later same-day run;
  * the council's own forecast is paired at capture time (point-in-time, not joined after the fact);
  * the °F→°C value is stored UNROUNDED — the offset estimator (Phase 3) handles rounding
    statistically; the ledger never un-rounds by guessing;
  * settlement grades TWC against the SAME anchored truth the verdict uses, via the existing
    settle_tracked_forecasts() — zero TWC-specific settlement code.
Network-free; isolated temp DB.

Run with:  PYTHONPATH=. python3 -m unittest tests.test_twc_tracking -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weather_council import storage
from weather_council.sources import Place

_TS = {"kind": "station", "station": {"icao": "WSSS", "name": "Changi", "id": None}}


def _place():
    return Place(name="Singapore", country="Singapore",
                 latitude=1.3502, longitude=103.994, timezone="Asia/Singapore")


class TestTwcTracking(unittest.TestCase):
    def setUp(self):
        self._dir = Path(tempfile.mkdtemp())
        self._orig = storage.DB_PATH
        storage.DB_PATH = self._dir / "t.db"
        storage._connect().close()                       # run the migration

    def tearDown(self):
        storage.DB_PATH = self._orig

    def _rows(self):
        conn = storage._connect()
        try:
            return conn.execute(
                "SELECT fc_high, fc_low, council_high, actual_high FROM tracked_forecasts "
                "WHERE source='twc' ORDER BY target_date").fetchall()
        finally:
            conn.close()

    def test_idempotent_pins_first_forecast(self):
        storage.log_tracked_forecast("twc", _place(), "2026-07-12", 30.5, 26.0, 30.4, 25.9, _TS)
        # a later same-day run must NOT overwrite the pinned lead
        storage.log_tracked_forecast("twc", _place(), "2026-07-12", 31.9, 27.0, 31.8, 26.9, _TS)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0][0], 30.5)         # the FIRST forecast is kept

    def test_council_paired_at_capture(self):
        storage.log_tracked_forecast("twc", _place(), "2026-07-12", 30.5, 26.0, 30.4, 25.9, _TS)
        self.assertAlmostEqual(self._rows()[0][2], 30.4)  # council's own forecast stored beside TWC

    def test_stores_unrounded_celsius(self):
        # 87°F -> 30.5556°C; the ledger keeps the unrounded value (estimator handles rounding).
        c = (87 - 32) * 5 / 9
        storage.log_tracked_forecast("twc", _place(), "2026-07-12", c, 26.0, None, None, _TS)
        self.assertAlmostEqual(self._rows()[0][0], c, places=6)

    def test_null_low_is_dropped_by_not_null_constraint(self):
        # tracked_forecasts.fc_low is NOT NULL; a null low is silently ignored by INSERT OR IGNORE.
        # This pins WHY the logger must skip a missing-low day explicitly (never a silent gap).
        storage.log_tracked_forecast("twc", _place(), "2026-07-12", 30.5, None, None, None, _TS)
        self.assertEqual(len(self._rows()), 0)

    def test_settles_against_anchored_truth(self):
        storage.log_tracked_forecast("twc", _place(), "2026-01-01", 30.5, 26.0, 30.4, 25.9, _TS)
        # settle grades via the SAME _anchored_actual the council verdict uses — fake the record.
        with mock.patch.object(storage, "_anchored_actual", return_value=(33.0, 25.0)):
            report = storage.settle_tracked_forecasts(sources=object())
        rows = self._rows()
        self.assertAlmostEqual(rows[0][3], 33.0)          # actual_high filled from anchored truth
        self.assertTrue(any("twc/Singapore" in r for r in report))

    def test_unsettled_when_truth_absent(self):
        storage.log_tracked_forecast("twc", _place(), "2026-01-01", 30.5, 26.0, None, None, _TS)
        with mock.patch.object(storage, "_anchored_actual", return_value=None):
            storage.settle_tracked_forecasts(sources=object())
        self.assertIsNone(self._rows()[0][3])             # no truth yet -> stays ungraded (no guess)


if __name__ == "__main__":
    unittest.main()
