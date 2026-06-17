"""Network-free tests for the authoritative settlement layer (ledger cand 53).

Covers: the per-day event slug builder, parsing a SETTLED Gamma event into a
Resolution (winner detection by Yes==1, °C/°F grain, range/tail edges,
unresolved/empty handling), Resolution.contains, and the idempotent DB backfill
that fills pm_resolved_label from a stubbed MarketData.

Run with:  PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path

from weather_council.market import (MarketData, Resolution, resolved_event_slug)
from weather_council import storage


def _market(label: str, yes: str, *, unit: str = "Celsius", station: str = "London City Airport"):
    """A single Gamma 'market' (one bucket) of a settled event."""
    return {
        "groupItemTitle": label,
        "question": f"Will the highest temperature be {label}?",
        "description": (f"This market will resolve to the range that contains the "
                        f"highest temperature recorded at the {station} in degrees "
                        f"{unit} on 12 Jun '26."),
        "outcomes": '["Yes", "No"]',
        "outcomePrices": f'["{yes}", "{1 - float(yes):.0f}"]',
        "clobTokenIds": '["tok1", "tok2"]',
    }


def _event(slug: str, buckets: list[tuple[str, str]], *, closed: bool = True,
           unit: str = "Celsius", station: str = "London City Airport") -> dict:
    return {
        "id": "evt1",
        "title": "Highest temperature in London on June 12?",
        "slug": slug,
        "endDate": "2026-06-12T12:00:00Z",
        "closed": closed,
        "markets": [_market(lbl, yes, unit=unit, station=station) for lbl, yes in buckets],
    }


class FakeHTTP:
    """Returns one canned events array regardless of params; records calls."""
    def __init__(self, array):
        self._array = array
        self.calls = []

    def get_json_array(self, url, params=None):
        self.calls.append((url, params))
        return self._array


class TestSlug(unittest.TestCase):
    def test_basket_city_slugs_with_year_suffix(self):
        self.assertEqual(
            resolved_event_slug("Hong Kong, HK", dt.date(2026, 6, 12)),
            "highest-temperature-in-hong-kong-on-june-12-2026")
        self.assertEqual(
            resolved_event_slug("London, United Kingdom", dt.date(2026, 6, 3)),
            "highest-temperature-in-london-on-june-3-2026")  # no leading zero

    def test_strips_to_city_before_comma(self):
        self.assertEqual(
            resolved_event_slug("London", dt.date(2025, 12, 31)),
            "highest-temperature-in-london-on-december-31-2025")


class TestFetchResolution(unittest.TestCase):
    def _md(self, array):
        return MarketData(http=FakeHTTP(array))

    def test_winner_is_the_yes_priced_bucket(self):
        ev = _event("s", [("22°C", "0"), ("23°C", "1"), ("24°C", "0")])
        r = self._md([ev]).fetch_resolution("s")
        self.assertIsNotNone(r)
        self.assertTrue(r.resolved)
        self.assertEqual(r.winning_label, "23°C")
        self.assertEqual((r.winning_lo, r.winning_hi), (23, 23))
        self.assertEqual(r.grain, "C")
        self.assertTrue(r.contains(23))
        self.assertFalse(r.contains(22))
        self.assertFalse(r.contains(24))

    def test_open_tail_winner_has_one_sided_edge(self):
        ev = _event("s", [("31°C", "0"), ("33°C or higher", "1")])
        r = self._md([ev]).fetch_resolution("s")
        self.assertEqual((r.winning_lo, r.winning_hi), (33, None))
        self.assertTrue(r.contains(40))      # 40 >= 33 -> in the open tail
        self.assertFalse(r.contains(32))

    def test_fahrenheit_grain_detected(self):
        ev = _event("s", [("72°F or below", "0"), ("79-80°F", "1")], unit="Fahrenheit")
        r = self._md([ev]).fetch_resolution("s")
        self.assertEqual(r.grain, "F")
        self.assertEqual((r.winning_lo, r.winning_hi), (79, 80))

    def test_unresolved_when_no_yes_winner(self):
        ev = _event("s", [("22°C", "0"), ("23°C", "0")], closed=False)
        r = self._md([ev]).fetch_resolution("s")
        self.assertFalse(r.resolved)
        self.assertIsNone(r.winning_label)
        self.assertFalse(r.contains(23))     # an unresolved day eliminates nothing

    def test_no_event_returns_none(self):
        self.assertIsNone(self._md([]).fetch_resolution("s"))

    def test_picks_event_matching_the_requested_slug(self):
        wrong = _event("highest-temperature-in-london-on-june-12", [("23°C", "1")])
        right = _event("highest-temperature-in-london-on-june-12-2026", [("22°C", "1")])
        r = self._md([wrong, right]).fetch_resolution(
            "highest-temperature-in-london-on-june-12-2026")
        self.assertEqual(r.winning_label, "22°C")   # year-suffixed event, not the 2025 one


class _StubMarket:
    """MarketData stand-in: maps slug -> Resolution (or None)."""
    def __init__(self, table):
        self.table = table
        self.fetches = []

    def fetch_resolution(self, slug):
        self.fetches.append(slug)
        return self.table.get(slug)


class TestBackfill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_db = storage.DB_PATH
        storage.DB_PATH = Path(self.tmp.name) / "t.db"
        conn = storage._connect()          # creates schema incl. pm_resolved_* cols
        buckets = '[{"label":"22°C","lo":22,"hi":22},{"label":"23°C","lo":23,"hi":23}]'
        with conn:
            for issued in ("2026-06-11T20:00:00", "2026-06-12T08:00:00"):
                conn.execute(
                    "INSERT INTO market_snapshots (issued_at, place, target_date, "
                    "grain, buckets_json, sub_degree, realized_label) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (issued, "London, United Kingdom", "2026-06-12", "C", buckets, 0, "22°C"))
            # an unresolved day that must be left NULL
            conn.execute(
                "INSERT INTO market_snapshots (issued_at, place, target_date, "
                "grain, buckets_json, sub_degree) VALUES (?,?,?,?,?,?)",
                ("2026-06-13T08:00:00", "London, United Kingdom", "2026-06-13",
                 "C", buckets, 0))
        conn.close()

    def tearDown(self):
        storage.DB_PATH = self._orig_db
        self.tmp.cleanup()

    def _labels(self):
        conn = sqlite3.connect(storage.DB_PATH)
        rows = conn.execute(
            "SELECT target_date, pm_resolved_label FROM market_snapshots "
            "ORDER BY issued_at").fetchall()
        conn.close()
        return rows

    def test_backfill_writes_authoritative_label_to_all_rows_of_a_day(self):
        stub = _StubMarket({
            "highest-temperature-in-london-on-june-12-2026":
                Resolution(slug="x", resolved=True, winning_label="23°C",
                           winning_lo=23, winning_hi=23, source="Wunderground EGLC"),
            "highest-temperature-in-london-on-june-13-2026":
                Resolution(slug="y", resolved=False),     # not finalized
        })
        report = storage.backfill_pm_resolutions(stub, cutoff_days=0)
        self.assertEqual(len(report), 1)                  # only the resolved day
        labels = dict()
        for d, lab in self._labels():
            labels.setdefault(d, set()).add(lab)
        self.assertEqual(labels["2026-06-12"], {"23°C"})  # both rows filled
        self.assertEqual(labels["2026-06-13"], {None})    # unresolved left NULL

    def test_backfill_is_idempotent(self):
        stub = _StubMarket({
            "highest-temperature-in-london-on-june-12-2026":
                Resolution(slug="x", resolved=True, winning_label="23°C",
                           winning_lo=23, winning_hi=23),
            "highest-temperature-in-london-on-june-13-2026": Resolution(slug="y", resolved=False),
        })
        storage.backfill_pm_resolutions(stub, cutoff_days=0)
        stub.fetches.clear()
        again = storage.backfill_pm_resolutions(stub, cutoff_days=0)
        self.assertEqual(again, [])                        # nothing new written
        # the already-filled 06-12 is not re-fetched; only the still-NULL 06-13 is
        self.assertNotIn("highest-temperature-in-london-on-june-12-2026", stub.fetches)


class TestLiveScorecard(unittest.TestCase):
    """The realized served-vs-settlement hit-rate (the honest number that replaces
    the backtest-optimistic conviction)."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = storage.DB_PATH
        storage.DB_PATH = Path(self.tmp.name) / "t.db"
        conn = storage._connect()
        bj = '[{"label":"22\\u00b0C","lo":22,"hi":22}]'
        days = [  # (target, served_high, pm_resolved_label) — round-half-up London
            ("2026-06-12", 21.8, "23°C"),   # served 22, settled 23 -> miss
            ("2026-06-13", 21.5, "22°C"),   # served 22, settled 22 -> hit
            ("2026-06-14", 20.0, "20°C"),   # served 20, settled 20 -> hit
        ]
        with conn:
            for tgt, high, pm in days:
                conn.execute(
                    "INSERT INTO market_snapshots (issued_at, place, target_date, "
                    "grain, buckets_json, sub_degree, pm_resolved_label) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (tgt + "T20:00:00", "London, United Kingdom", tgt, "C", bj, 0, pm))
                conn.execute(
                    "INSERT INTO verdicts (issued_at, place, target_date, high, low, "
                    "confidence) VALUES (?,?,?,?,?,?)",
                    (tgt + "T08:00:00", "London, United Kingdom", tgt, high, 12.0, "MODERATE"))
        conn.close()

    def tearDown(self):
        storage.DB_PATH = self._orig
        self.tmp.cleanup()

    def test_realized_rate_uses_contract_bucket_not_backtest(self):
        sc = storage.live_bucket_scorecard("London, United Kingdom")
        self.assertEqual(sc["n"], 3)
        self.assertEqual(sc["hits"], 2)            # 06-13 and 06-14 hit; 06-12 missed
        self.assertAlmostEqual(sc["rate"], 2 / 3)

    def test_no_settled_days_is_empty(self):
        self.assertEqual(storage.live_bucket_scorecard("Nowhere, ZZ")["n"], 0)


if __name__ == "__main__":
    unittest.main()
