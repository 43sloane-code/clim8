"""Network-free KATs for two storage hardening fixes:

1. `PRAGMA busy_timeout = 5000` in `storage._connect` — verdicts.db is written by
   >=4 concurrent launchd jobs plus run.py, and sqlite's default busy timeout is
   0, so an overlapping writer got an immediate "database is locked". `_connect`
   is the single choke point every storage connection goes through; a fresh
   connection from it must report the timeout.

2. Per-row guard on `json.loads(buckets_json)` in `storage.fetch_settled_snapshots`
   — one corrupt row used to abort the ENTIRE C7 scoring read. The settle path
   already guarded the identical parse and skipped the row ("One corrupt row must
   not abort the whole settle batch"); the read path now does the same, WITHOUT
   letting the corrupt row claim its (place, target_date) dedup slot.

Run with:  PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weather_council import storage


class _DbTestCase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patch = mock.patch.object(storage, "DB_PATH", Path(self._tmp.name))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        Path(self._tmp.name).unlink(missing_ok=True)


class TestBusyTimeout(_DbTestCase):

    def test_connect_sets_busy_timeout(self):
        """A fresh connection from `_connect` (the single connection choke point)
        reports busy_timeout 5000, so concurrent launchd writers wait each other
        out instead of failing instantly with "database is locked"."""
        conn = storage._connect()
        try:
            val = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(val, 5000)


class TestFetchSettledCorruptBuckets(_DbTestCase):

    def _insert(self, issued_at, place, target, buckets_json,
                realized_label="19°C"):
        conn = storage._connect()
        with conn:
            conn.execute(
                "INSERT INTO market_snapshots "
                "(issued_at, place, target_date, grain, buckets_json, realized_label) "
                "VALUES (?,?,?,?,?,?)",
                (issued_at, place, target, "C", buckets_json, realized_label))
        conn.close()

    def test_corrupt_row_is_skipped_not_fatal(self):
        good_buckets = json.dumps(
            [{"label": "19°C", "lo": 19, "hi": 19, "model_prob": 0.5, "market_prob": 0.4}])
        # A corrupt row issued EARLIER for day 1, an intact later row for the same
        # day, and an intact row for day 2.
        self._insert("2026-07-01T00:00:00", "Hong Kong, HK", "2026-06-30", "{corrupt")
        self._insert("2026-07-01T01:00:00", "Hong Kong, HK", "2026-06-30", good_buckets)
        self._insert("2026-07-02T00:00:00", "Hong Kong, HK", "2026-07-01", good_buckets)

        rows = storage.fetch_settled_snapshots()     # must not raise on the bad row

        self.assertEqual(len(rows), 2)
        by_day = {r["target_date"]: r for r in rows}
        # The corrupt row neither aborted the read nor claimed the day's dedup
        # slot: the intact later snapshot for 2026-06-30 is the one scored.
        self.assertEqual(by_day["2026-06-30"]["buckets"][0]["label"], "19°C")
        self.assertEqual(by_day["2026-07-01"]["buckets"][0]["label"], "19°C")


if __name__ == "__main__":
    unittest.main()
