"""KAT for the book_snapshots schema migration (weather_council/storage.py, Phase 3).

The order-book archive table must be created ADDITIVELY by storage._connect: a fresh DB gets it,
an existing DB that predates it gains it on the next open (no data loss, no error), and the PK is
(place, target_date, issued_at, token_id) so each token's book at a snapshot instant is one row.
A fetch_ok=0 row (failed token) must be insertable with NULL stats — that is how a silent capture
gap is made impossible. Network-free; isolated temp DB.

Run with:  PYTHONPATH=. python3 -m unittest tests.test_book_snapshots_schema -v
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weather_council import storage

_EXPECTED_COLS = {
    "issued_at", "place", "target_date", "token_id", "bucket_label", "fetch_ok",
    "best_bid", "best_ask", "mid", "spread", "bid_depth_usd", "ask_depth_usd",
    "n_bid_levels", "n_ask_levels", "book_ts", "book_json", "error",
}


class TestBookSnapshotsSchema(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patch = mock.patch.object(storage, "DB_PATH", Path(self._tmp.name))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        Path(self._tmp.name).unlink(missing_ok=True)

    def _cols_and_pk(self):
        conn = sqlite3.connect(self._tmp.name)
        info = list(conn.execute("PRAGMA table_info(book_snapshots)"))
        conn.close()
        cols = {r[1] for r in info}
        pk = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5] > 0]  # r[5]=pk order
        return cols, pk

    def test_fresh_db_gets_table_with_expected_columns(self):
        storage._connect().close()
        cols, pk = self._cols_and_pk()
        self.assertEqual(cols, _EXPECTED_COLS)
        self.assertEqual(pk, ["place", "target_date", "issued_at", "token_id"])

    def test_migration_is_additive_on_a_pre_existing_db(self):
        # Simulate an OLD db: one unrelated table with a row, no book_snapshots.
        conn = sqlite3.connect(self._tmp.name)
        conn.execute("CREATE TABLE legacy (k TEXT PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO legacy VALUES ('a', '1')")
        conn.commit()
        conn.close()
        storage._connect().close()                       # migration runs
        conn = sqlite3.connect(self._tmp.name)
        # legacy data preserved, book_snapshots now present
        self.assertEqual(conn.execute("SELECT v FROM legacy WHERE k='a'").fetchone()[0], "1")
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        self.assertIn("book_snapshots", names)

    def test_second_connect_is_idempotent(self):
        storage._connect().close()
        storage._connect().close()                       # must not raise on re-create
        cols, _pk = self._cols_and_pk()
        self.assertEqual(cols, _EXPECTED_COLS)

    def test_fetch_ok_zero_row_with_null_stats_is_insertable(self):
        conn = storage._connect()
        with conn:
            conn.execute(
                "INSERT INTO book_snapshots "
                "(issued_at, place, target_date, token_id, bucket_label, fetch_ok, error) "
                "VALUES (?,?,?,?,?,?,?)",
                ("2026-07-10T12:00:00", "London", "2026-07-11", "TKN", "34C", 0,
                 "timeout"))
        row = conn.execute(
            "SELECT fetch_ok, best_ask, book_json, error FROM book_snapshots").fetchone()
        conn.close()
        self.assertEqual(row[0], 0)
        self.assertIsNone(row[1])                        # stats NULL on a failure row
        self.assertIsNone(row[2])
        self.assertEqual(row[3], "timeout")

    def test_primary_key_dedups_same_token_same_instant(self):
        conn = storage._connect()
        args = ("2026-07-10T12:00:00", "London", "2026-07-11", "TKN", "34C", 1)
        with conn:
            conn.execute("INSERT OR REPLACE INTO book_snapshots "
                         "(issued_at, place, target_date, token_id, bucket_label, fetch_ok, "
                         " best_ask) VALUES (?,?,?,?,?,?,0.50)", args)
            conn.execute("INSERT OR REPLACE INTO book_snapshots "
                         "(issued_at, place, target_date, token_id, bucket_label, fetch_ok, "
                         " best_ask) VALUES (?,?,?,?,?,?,0.60)", args)   # same PK, updates
        n, ask = conn.execute(
            "SELECT COUNT(*), best_ask FROM book_snapshots").fetchone()
        conn.close()
        self.assertEqual(n, 1)                            # one row, not two
        self.assertEqual(ask, 0.60)                       # last write wins


if __name__ == "__main__":
    unittest.main()
