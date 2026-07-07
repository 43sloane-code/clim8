"""Network-free tests for candidate 46 — settlement-anchored verification.

The `verdicts` table was the last of three settlement paths still building a
BLANK, id-only Station inside `verify()`. That silently skipped the modern
settlement overlays in `fetch_station_daily` (the HKO Observatory by a name token
+ geography, London City by the EGLC ICAO), so every station-anchored basket-city
verdict scored against the stale bulk Meteostat file — or, for the HKO station
whose Meteostat file ends in 1992, never settled at all. Live evidence: 0 of ~115
station-anchored Hong Kong / London rows had settled, while ERA5-anchored rows did.

These prove the fix WITHOUT touching the network:
  * log_verdict persists the anchor's icao + name;
  * verify() rebuilds the EXACT Station from the persisted identity and settles;
  * a row logged BEFORE identity was persisted is recovered from the station
    inventory by id (station_by_id) so the overlay still fires — the existing
    Hong Kong / London backlog can finally settle on its settlement record;
  * the ERA5 (non-station) path is unchanged.

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
from weather_council.sources import Station


class _Place:
    def __init__(self, label, lat, lon):
        self._label, self.latitude, self.longitude = label, lat, lon

    def label(self):
        return self._label


def _verdict(place, target, high, low, truth_source):
    return types.SimpleNamespace(
        place=place, target=target, high=high, low=low,
        confidence="MODERATE", truth_source=truth_source)


class TestSettlementVerify(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patch = mock.patch.object(storage, "DB_PATH", Path(self._tmp.name))
        self._patch.start()
        self.target = (dt.date.today() - dt.timedelta(days=6)).isoformat()
        self.hk = _Place("Hong Kong, HK", 22.30, 114.17)

    def tearDown(self):
        self._patch.stop()
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_log_verdict_persists_station_identity(self):
        ts = {"kind": "station",
              "station": {"id": "45005", "name": "Royal Observatory", "icao": None}}
        storage.log_verdict(_verdict(self.hk, self.target, 29.5, 25.7, ts))
        conn = storage._connect()
        row = conn.execute(
            "SELECT station_id, station_icao, station_name FROM verdicts").fetchone()
        conn.close()
        self.assertEqual(row, ("45005", None, "Royal Observatory"))

    def test_verify_rebuilds_persisted_identity_and_settles_on_hko(self):
        # Hong Kong verdict anchored on the HKO Observatory; the settlement record
        # for the day is 28.0 (the value the user supplied / the audit kv carries).
        ts = {"kind": "station",
              "station": {"id": "45005", "name": "Royal Observatory", "icao": None}}
        storage.log_verdict(_verdict(self.hk, self.target, 29.5, 25.7, ts))

        seen = []
        def fake_station_daily(st):
            seen.append(st)
            return {self.target: (28.0, 25.0)}
        fake = types.SimpleNamespace(fetch_station_daily=fake_station_daily)

        notes = storage.verify(fake)
        self.assertEqual(len(notes), 1)
        # The rebuilt Station carries the real identity — NOT a blank one. Pre-fix
        # this was name="" so is_hko_observatory() never fired.
        self.assertEqual(seen[0].name, "Royal Observatory")
        self.assertEqual(seen[0].id, "45005")

        conn = storage._connect()
        ah, al, eh, el = conn.execute(
            "SELECT actual_high, actual_low, err_high, err_low FROM verdicts").fetchone()
        conn.close()
        self.assertEqual((ah, al), (28.0, 25.0))
        self.assertAlmostEqual(eh, 1.5, places=9)     # |29.5 - 28.0|
        self.assertAlmostEqual(el, 0.7, places=9)     # |25.7 - 25.0|

    def test_verify_recovers_legacy_identity_via_inventory(self):
        """A row logged BEFORE identity was persisted (icao/name NULL) must recover
        the anchor from the inventory by id, so the overlay still fires and the
        existing backlog settles."""
        conn = storage._connect()        # build the table, then insert a legacy row
        with conn:
            conn.execute(
                "INSERT INTO verdicts (issued_at, place, target_date, high, low, "
                " confidence, truth_kind, station_id, station_icao, station_name, "
                " fc_lat, fc_lon) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("2026-01-01T00:00:00", "Hong Kong, HK", self.target, 29.5, 25.7,
                 "MODERATE", "station", "45005", None, None, 22.30, 114.17))
        conn.close()

        recovered = Station(id="45005", name="Royal Observatory", wmo=None, icao=None,
                            latitude=22.302, longitude=114.174, elevation=None,
                            distance_km=0.0)
        looked_up, fetched = [], []
        def fake_by_id(sid):
            looked_up.append(sid)
            return recovered
        def fake_station_daily(st):
            fetched.append(st)
            return {self.target: (28.0, 25.0)}
        fake = types.SimpleNamespace(station_by_id=fake_by_id,
                                     fetch_station_daily=fake_station_daily)

        notes = storage.verify(fake)
        self.assertEqual(len(notes), 1)
        self.assertEqual(looked_up, ["45005"])               # inventory fallback fired
        self.assertEqual(fetched[0].name, "Royal Observatory")  # recovered identity used

        conn = storage._connect()
        ah = conn.execute("SELECT actual_high FROM verdicts").fetchone()[0]
        conn.close()
        self.assertEqual(ah, 28.0)

    def test_verify_does_not_use_inventory_when_identity_present(self):
        """When icao/name are already persisted, verify must NOT consult the
        inventory — the frozen-at-log identity wins (robust to inventory drift).
        London settles on the WU oracle (icao in storage._WU_SETTLE_TZ), so verify
        reads wunderground_daily_series; the no-inventory guarantee is unchanged."""
        ts = {"kind": "station",
              "station": {"id": "EGLC0", "name": "London / City Airport",
                          "icao": "EGLC"}}
        london = _Place("London, United Kingdom", 51.5, 0.1167)
        storage.log_verdict(_verdict(london, self.target, 22.0, 13.0, ts))

        def boom(sid):                       # must never be called
            raise AssertionError("station_by_id used despite persisted identity")
        fake = types.SimpleNamespace(
            station_by_id=boom,
            wunderground_daily_series=lambda ic, s, e, tz: {self.target: (21.0, 12.0)},
            fetch_station_daily=lambda st: {self.target: (21.0, 12.0)})
        notes = storage.verify(fake)
        self.assertEqual(len(notes), 1)

    def test_verify_era5_path_unchanged(self):
        """A non-station (ERA5-anchored) verdict still settles via the archive
        path — the fix only touched the station branch."""
        ts = {"kind": "grid", "station": {}}      # not station-anchored
        nyc = _Place("New York, United States", 40.7, -74.0)
        storage.log_verdict(_verdict(nyc, self.target, 30.0, 20.0, ts))
        fake = types.SimpleNamespace(
            fetch_archive_series=lambda place, s, e: {self.target: (31.0, 19.0)})
        notes = storage.verify(fake)
        self.assertEqual(len(notes), 1)
        conn = storage._connect()
        eh = conn.execute("SELECT err_high FROM verdicts").fetchone()[0]
        conn.close()
        self.assertAlmostEqual(eh, 1.0, places=9)


if __name__ == "__main__":
    unittest.main()
