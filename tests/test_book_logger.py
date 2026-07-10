"""KAT for order-book capture (tools/book_logger.py + storage book helpers, Phase 4).

Pins the capture contract: scope is limited to the focus cities; each bucket's YES token is
fetched read-only, parsed, and archived at the passed instant; a per-token fetch/parse failure
becomes a fetch_ok=0 row (never an abort); and book_snapshot_coverage summarises what landed.
Network-free — a fake MarketData supplies book payloads; storage.DB_PATH points at a temp DB.

Run with:  PYTHONPATH=. python3 -m unittest tests.test_book_logger -v
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from weather_council import storage
from tools import book_logger


def _bucket(label, token_id):
    return SimpleNamespace(label=label, token_ids=(token_id, token_id + "_NO"))


class _FakeMarketData:
    """Stands in for MarketData.fetch_order_book. `books` maps token_id -> raw payload
    (or an Exception to raise, or None to simulate an empty response)."""
    def __init__(self, books):
        self.books = books

    def fetch_order_book(self, token_id):
        v = self.books.get(token_id)
        if isinstance(v, Exception):
            raise v
        return v


_GOOD_BOOK = {
    "asset_id": "T_YES", "timestamp": "2026-07-10T12:00:00Z",
    "bids": [{"price": "0.40", "size": "100"}],
    "asks": [{"price": "0.52", "size": "100"}, {"price": "0.55", "size": "200"}],
}


class TestFocusScope(unittest.TestCase):
    def test_focus_cities_matched_case_insensitively(self):
        for name in ["London", "london, United Kingdom", "Karachi", "Jeddah",
                     "Singapore", "San Francisco"]:
            self.assertTrue(book_logger.in_focus(name), name)

    def test_out_of_scope_cities_excluded(self):
        # Jakarta is excluded: Polymarket lists no Jakarta high-temperature market.
        for name in ["Manila", "Hong Kong", "Jakarta", ""]:
            self.assertFalse(book_logger.in_focus(name), name)


class TestCapture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patch = mock.patch.object(storage, "DB_PATH", Path(self._tmp.name))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        Path(self._tmp.name).unlink(missing_ok=True)

    def _rows(self):
        conn = sqlite3.connect(self._tmp.name)
        storage._connect().close()                         # ensure table exists
        rows = conn.execute(
            "SELECT token_id, bucket_label, fetch_ok, best_ask, ask_depth_usd, "
            "       book_json, error FROM book_snapshots ORDER BY token_id").fetchall()
        conn.close()
        return rows

    def test_good_book_is_parsed_and_archived_with_stats(self):
        md = _FakeMarketData({"T_YES": _GOOD_BOOK})
        summary = book_logger.capture_market_books(
            md, "London", "2026-07-11", "2026-07-10T12:00:00", [_bucket("34C", "T_YES")])
        self.assertEqual(summary, {"ok": 1, "failed": 0, "rows": 1})
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        tok, label, ok, best_ask, ask_depth, book_json, err = rows[0]
        self.assertEqual((tok, label, ok), ("T_YES", "34C", 1))
        self.assertAlmostEqual(best_ask, 0.52)
        self.assertAlmostEqual(ask_depth, 0.52 * 100 + 0.55 * 200)
        self.assertIsNotNone(book_json)                    # full ladder archived
        self.assertIsNone(err)

    def test_per_token_failure_isolation_writes_fetch_ok_zero(self):
        md = _FakeMarketData({
            "OK": _GOOD_BOOK,
            "EMPTY": None,                                  # empty response
            "BOOM": RuntimeError("clob 503"),              # raises
        })
        buckets = [_bucket("33C", "OK"), _bucket("34C", "EMPTY"), _bucket("35C", "BOOM")]
        summary = book_logger.capture_market_books(
            md, "London", "2026-07-11", "2026-07-10T12:00:00", buckets)
        self.assertEqual(summary, {"ok": 1, "failed": 2, "rows": 3})
        by_tok = {r[0]: r for r in self._rows()}
        self.assertEqual(by_tok["OK"][2], 1)
        self.assertEqual(by_tok["EMPTY"][2], 0)
        self.assertEqual(by_tok["EMPTY"][6], "no book returned")
        self.assertEqual(by_tok["BOOM"][2], 0)
        self.assertIn("RuntimeError", by_tok["BOOM"][6])
        self.assertIsNone(by_tok["EMPTY"][3])              # stats NULL on failure

    def test_untokenised_buckets_capture_nothing(self):
        md = _FakeMarketData({})
        summary = book_logger.capture_market_books(
            md, "London", "2026-07-11", "2026-07-10T12:00:00",
            [SimpleNamespace(label="x", token_ids=())])
        self.assertEqual(summary, {"ok": 0, "failed": 0, "rows": 0})
        self.assertEqual(self._rows(), [])

    def test_capture_for_market_skips_out_of_scope_city(self):
        md = _FakeMarketData({"T_YES": _GOOD_BOOK})
        market = SimpleNamespace(buckets=[_bucket("34C", "T_YES")])
        out = book_logger.capture_for_market(
            md, "Manila", "Manila", "2026-07-11", market, "2026-07-10T12:00:00")
        self.assertIsNone(out)                             # Manila not in focus
        self.assertEqual(self._rows(), [])

    def test_capture_for_market_none_market_is_noop(self):
        md = _FakeMarketData({})
        self.assertIsNone(book_logger.capture_for_market(
            md, "London", "London", "2026-07-11", None, "2026-07-10T12:00:00"))

    def test_capture_for_place_normalizes_target_to_date_for_match(self):
        # Regression: a string target was passed straight to match_market (which does
        # target_date.month) -> AttributeError -> swallowed -> zero books captured.
        # capture_for_place must hand match_market a dt.date whether given a str or date.
        import datetime as dt

        class _FakeMD:
            def __init__(self, http=None):
                pass

            def fetch_temperature_markets(self, *a, **k):
                return []

            def fetch_order_book(self, tok):
                return _GOOD_BOOK

        seen = {}

        def _fake_match(markets, city, target_date):
            seen["type"] = type(target_date).__name__
            seen["val"] = target_date
            return SimpleNamespace(buckets=[_bucket("31C", "T_YES")])

        place = SimpleNamespace(name="London", label=lambda: "London")
        src = SimpleNamespace(http=None)
        for target in ("2026-07-11", dt.date(2026, 7, 11)):
            seen.clear()
            with mock.patch.object(book_logger, "MarketData", _FakeMD), \
                 mock.patch.object(book_logger, "match_market", _fake_match):
                out = book_logger.capture_for_place(src, place, target, "2026-07-10T12:00:00")
            self.assertEqual(seen["type"], "date", f"match_market got {seen['type']} for {target!r}")
            self.assertEqual(seen["val"], dt.date(2026, 7, 11))
            self.assertEqual(out["ok"], 1)                # a book was actually captured
        # And the archive stored the ISO-string target (not a date repr).
        conn = sqlite3.connect(self._tmp.name)
        dates = {r[0] for r in conn.execute("SELECT DISTINCT target_date FROM book_snapshots")}
        conn.close()
        self.assertEqual(dates, {"2026-07-11"})

    def test_book_snapshot_coverage_summarises_ok_and_failed(self):
        md = _FakeMarketData({"OK": _GOOD_BOOK, "BAD": None})
        book_logger.capture_market_books(
            md, "London", "2026-07-11", "2026-07-10T12:00:00",
            [_bucket("33C", "OK"), _bucket("34C", "BAD")])
        cov = storage.book_snapshot_coverage(24)
        self.assertEqual(cov["rows"], 2)
        self.assertEqual(cov["ok"], 1)
        self.assertEqual(cov["failed"], 1)
        self.assertEqual(cov["batches"], 1)
        self.assertIn("London", cov["by_place"])
        self.assertEqual(cov["by_place"]["London"], {"rows": 2, "ok": 1, "failed": 1})


if __name__ == "__main__":
    unittest.main()
