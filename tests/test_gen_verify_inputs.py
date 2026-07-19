"""Network-free KATs for tools/gen_verify_inputs.py helpers.

Run with:  PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import gen_verify_inputs as gvi


class TestBucketHelpers(unittest.TestCase):
    def test_bucket_f_rounds_half_up(self):
        # 87°F -> 30.555...°C -> round-half-up -> 31°C
        self.assertEqual(gvi._bucket_f(87.0), 31)
        # 86°F -> 30.0°C -> 30°C
        self.assertEqual(gvi._bucket_f(86.0), 30)

    def test_int_extracts_first_integer(self):
        self.assertEqual(gvi._int("18°C"), 18)
        self.assertEqual(gvi._int("-2"), -2)
        self.assertIsNone(gvi._int("none"))


class _FakeHTTP:
    def __init__(self, payloads, fail_indices=None):
        self.payloads = payloads
        self.fail_indices = fail_indices or set()
        self.calls = []
        self.idx = 0

    def get_json(self, url, params):
        self.calls.append((url, params))
        if self.idx in self.fail_indices:
            self.idx += 1
            raise ConnectionError("chunk failed")
        payload = self.payloads[self.idx]
        self.idx += 1
        return payload


class _FakeSources:
    def __init__(self, http):
        self.http = http


class TestFetchDailyMaxRange(unittest.TestCase):
    def _obs(self, ts, temp):
        return {"temp": temp, "valid_time_gmt": ts}

    def test_groups_local_day_drops_incomplete_and_counts_failures(self):
        # Two chunks. Chunk 0: two full days of 24 obs each (>=12).
        # Chunk 1: one full day + one partial day (3 obs) + one simulated failure.
        base = dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.timezone.utc)

        def ts(day, hour):
            return int((base + dt.timedelta(days=day - 1, hours=hour)).timestamp())

        chunk0 = {"observations": [
            self._obs(ts(1, h), 80.0 + h) for h in range(24)
        ]}
        chunk1 = {"observations": [
            self._obs(ts(2, h), 85.0 + h) for h in range(24)
        ]}
        http = _FakeHTTP([chunk0, chunk1], fail_indices={1})
        src = _FakeSources(http)
        vals, failures = gvi._fetch_daily_max_range(
            src, "WSSS", dt.date(2026, 1, 1), dt.date(2026, 2, 4), "UTC")
        self.assertEqual(failures, 1)
        # Day 1 from successful chunk 0; day 2's chunk failed -> no data.
        self.assertEqual(len(vals), 1)
        self.assertEqual(vals, [103.0])


class TestRecords(unittest.TestCase):
    def test_lead_label_and_issue_hour(self):
        with tempfile.TemporaryDirectory() as td:
            dbp = Path(td) / "t.db"
            conn = sqlite3.connect(dbp)
            with conn:
                conn.execute(
                    "CREATE TABLE market_snapshots (place TEXT, target_date TEXT, "
                    "issued_at TEXT, buckets_json TEXT, realized_label TEXT, "
                    "pm_resolved_label TEXT)")
                # Day-ahead: issued the day before.
                conn.execute(
                    "INSERT INTO market_snapshots VALUES (?, ?, ?, ?, ?, ?)",
                    ("London, GB", "2026-06-10",
                     "2026-06-09T18:00:00+00:00",
                     json.dumps({"buckets": [{"label": "20°C", "model_prob": 0.9}]}),
                     "20", None))
                # Same-day: issued on target day at 14:30 local.
                conn.execute(
                    "INSERT INTO market_snapshots VALUES (?, ?, ?, ?, ?, ?)",
                    ("London, GB", "2026-06-11",
                     "2026-06-11T13:30:00+00:00",
                     json.dumps({"buckets": [{"label": "21°C", "model_prob": 0.85}]}),
                     "21", None))
            conn.close()

            old_db = gvi.DB
            try:
                gvi.DB = dbp
                recs = gvi.records()
            finally:
                gvi.DB = old_db

        self.assertEqual(len(recs), 2)
        by_date = {r["date"]: r for r in recs}
        self.assertEqual(by_date["2026-06-10"]["lead"], "day_ahead")
        # London local = UTC+1 in June; 18:00 UTC -> 19:00 local.
        self.assertEqual(by_date["2026-06-10"]["issue_hour"], 19.0)
        self.assertEqual(by_date["2026-06-11"]["lead"], "same_day")
        # London local = UTC+1 in June; 13:30 UTC -> 14:30 local.
        self.assertAlmostEqual(by_date["2026-06-11"]["issue_hour"], 14.5)
