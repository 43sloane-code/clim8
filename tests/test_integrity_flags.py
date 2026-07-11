"""KAT for the served-number remediation campaign Phase-0 ship: the integrity_flags field + the
passes_integrity() measurement filter (storage.py, §1.3).

The whole point of Phase 0 is that this ships PROVABLY INERT: the column is added, the filter exists,
but no row is flagged yet, so every measurement still includes every row — shipping it changes no
served or measured number. The remediation WPs (F1/F2/...) later WRITE flags; only then does the
filter start excluding. This KAT pins: (a) both tables gained the nullable column, (b) NULL/empty
flags are INCLUDED (the inert default), (c) *_SUSPECT excludes, *_CORRECTED/*_CONFIRMED do not,
(d) unparseable flags fail closed (excluded).

Run:  PYTHONPATH=. python3 -m unittest tests.test_integrity_flags -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weather_council import storage
from weather_council.storage import passes_integrity


class TestPassesIntegrity(unittest.TestCase):
    # ── the inert default: unflagged rows are always included ─────────────────────────────────────
    def test_null_and_empty_are_included(self):
        self.assertTrue(passes_integrity(None))        # NULL column -> included (inert)
        self.assertTrue(passes_integrity(""))
        self.assertTrue(passes_integrity("[]"))
        self.assertTrue(passes_integrity([]))

    def test_suspect_excludes(self):
        self.assertFalse(passes_integrity('["F1_RESOLUTION_SUSPECT"]'))
        self.assertFalse(passes_integrity('["F2_DAYMAX_SUSPECT"]'))
        self.assertFalse(passes_integrity(["F1_RESOLUTION_SUSPECT"]))            # list form too

    def test_corrected_and_confirmed_do_not_exclude(self):
        self.assertTrue(passes_integrity('["F1_RESOLUTION_CORRECTED"]'))
        self.assertTrue(passes_integrity('["F1_RESOLUTION_CONFIRMED"]'))
        self.assertTrue(passes_integrity('["F2_DAYMAX_CORRECTED"]'))

    def test_any_suspect_among_others_excludes(self):
        self.assertFalse(passes_integrity('["F2_DAYMAX_CORRECTED", "F1_RESOLUTION_SUSPECT"]'))

    def test_unparseable_fails_closed(self):
        self.assertFalse(passes_integrity("not-json"))     # can't prove clean -> excluded


class TestMigrationInert(unittest.TestCase):
    def setUp(self):
        self._dir = Path(tempfile.mkdtemp())
        self._orig = storage.DB_PATH
        storage.DB_PATH = self._dir / "t.db"

    def tearDown(self):
        storage.DB_PATH = self._orig

    def test_column_added_to_both_tables_and_defaults_null_included(self):
        conn = storage._connect()                          # runs the migration
        try:
            vcols = {r[1] for r in conn.execute("PRAGMA table_info(verdicts)")}
            mcols = {r[1] for r in conn.execute("PRAGMA table_info(market_snapshots)")}
            self.assertIn("integrity_flags", vcols)
            self.assertIn("integrity_flags", mcols)
            # a freshly logged-style row has NULL integrity_flags -> passes (inert at ship time)
            conn.execute("INSERT INTO verdicts (issued_at, place, target_date, high, low, confidence) "
                         "VALUES ('t','C','2026-07-11',30.0,25.0,'HIGH')")
            flag = conn.execute("SELECT integrity_flags FROM verdicts").fetchone()[0]
        finally:
            conn.close()
        self.assertIsNone(flag)
        self.assertTrue(passes_integrity(flag))            # NULL -> included: provably inert


if __name__ == "__main__":
    unittest.main()
