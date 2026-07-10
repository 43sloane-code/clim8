"""KAT for the soft-failure ledger (weather_council/failures.py, Phase 6b).

The settlement path deliberately swallows fetch/parse exceptions to stay resilient. A swallowed
SETTLEMENT-source failure is invisible data corruption — the day just does not settle and absence
reads as success. `failures.record_soft_failure` makes those swallows MEASURABLE without changing
control flow. This KAT pins the contract the callers rely on:
  * recording NEVER raises back into the caller (even on a broken db path / weird exc),
  * the 24h window and per-tag counts are correct,
  * detail is truncated (bounded row size),
  * it is a LEAF module — it must not import storage/sources (or instrumenting them cycles).
Network-free, isolated to a temp db.

Run with:  PYTHONPATH=. python3 -m unittest tests.test_failures -v
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path

from weather_council import failures


class TestSoftFailures(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "sf.db"

    def _record(self, tag, exc):
        failures.record_soft_failure(tag, exc, db_path=self.tmp)

    def test_record_query_and_counts(self):
        self._record("settle_wu_fetch", ValueError("boom"))
        self._record("settle_wu_fetch", TimeoutError("slow"))
        self._record("settle_station_fetch", KeyError("k"))
        self.assertEqual(failures.soft_failure_counts(24, db_path=self.tmp),
                         {"settle_wu_fetch": 2, "settle_station_fetch": 1})
        rows = failures.recent_soft_failures(24, db_path=self.tmp)
        self.assertEqual(len(rows), 3)
        # newest first; etype captured from the exception class
        self.assertEqual({r["etype"] for r in rows},
                         {"ValueError", "TimeoutError", "KeyError"})

    def test_detail_is_truncated(self):
        self._record("settle_wu_fetch", RuntimeError("x" * 5000))
        r = failures.recent_soft_failures(24, db_path=self.tmp)[0]
        self.assertIsNotNone(r["detail"])
        self.assertLessEqual(len(r["detail"]), 200)

    def test_recording_never_raises(self):
        # A broken db path must be swallowed — the ledger can never break the
        # resilience it observes. No assertion needed beyond "does not raise".
        failures.record_soft_failure("settle_wu_fetch", ValueError("x"),
                                     db_path=Path("/nonexistent/dir/deeper/x.db"))

    def test_window_excludes_old_rows(self):
        self._record("settle_wu_fetch", ValueError("recent"))
        old = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
               - dt.timedelta(hours=48)).isoformat(timespec="seconds")
        with sqlite3.connect(self.tmp) as c:
            c.execute("INSERT INTO soft_failures (at, tag, etype, detail) VALUES (?,?,?,?)",
                      (old, "settle_station_fetch", "E", None))
        counts = failures.soft_failure_counts(24, db_path=self.tmp)
        self.assertEqual(counts, {"settle_wu_fetch": 1})           # old row excluded
        self.assertIn("settle_station_fetch",
                      failures.soft_failure_counts(72, db_path=self.tmp))  # wider window sees it

    def test_default_db_write_is_suppressed_under_test(self):
        # The instrumentation calls record_soft_failure on the DEFAULT (production) DB.
        # Under the test harness that must be a NO-OP, or every test that trips a
        # swallowed except pollutes the real ledger and the healthcheck cries wolf.
        # (unittest is in sys.modules here, so _under_test() is True.)
        from unittest import mock
        default = Path(tempfile.mkdtemp()) / "prod.db"
        with mock.patch.object(failures, "DB_PATH", default):
            failures.record_soft_failure("settle_wu_fetch", ValueError("x"))  # db_path=None
        # Nothing written: the file/table is absent or empty.
        self.assertEqual(failures.recent_soft_failures(24, db_path=default), [])
        # An EXPLICIT db_path still records (test_failures itself relies on this).
        failures.record_soft_failure("settle_wu_fetch", ValueError("x"), db_path=self.tmp)
        self.assertEqual(failures.soft_failure_counts(24, db_path=self.tmp),
                         {"settle_wu_fetch": 1})

    def test_is_a_leaf_module(self):
        # failures.py must import nothing from weather_council, or instrumenting
        # storage/sources would create an import cycle. Assert on its source text.
        src = Path(failures.__file__).read_text()
        for banned in ("import storage", "from .storage", "from .sources",
                       "import weather_council", "from weather_council"):
            self.assertNotIn(banned, src, f"leaf-module rule broken: found {banned!r}")


if __name__ == "__main__":
    unittest.main()
