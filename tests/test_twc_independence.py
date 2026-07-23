"""KAT for the TWC independence audit (tools/twc_independence.py, Plan 4 Phase 5).

Measures whether TWC's errors carry information the council lacks — a redundancy check that must be
cleared before anyone considers TWC for the blend (and even then, only via the Plan-3 gate). Pins:
  * Pearson r: exact ±1 on perfectly (anti)correlated series, ~0 on independent, None when degenerate;
  * a member whose error is collinear with TWC's is FLAGGED (marginal info ~0); an independent one
    is not;
  * both gates: a city with < THRESHOLD_N paired days reads UNMEASURED, never a spurious correlation;
  * STRICTLY READ-ONLY — the audit path contains no write statement (grep-provable) AND running it
    leaves the DB row counts byte-identical. Blend inclusion is a Plan-3 candidate, never this plan.
Network-free; isolated temp DB.

Run with:  PYTHONPATH=. python3 -m unittest tests.test_twc_independence -v
"""
from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path

from tools import twc_independence as ti
from weather_council import storage


class TestPearson(unittest.TestCase):
    def test_perfect_and_independent(self):
        self.assertAlmostEqual(ti._pearson([1, 2, 3, 4], [2, 4, 6, 8]), 1.0)
        self.assertAlmostEqual(ti._pearson([1, 2, 3, 4], [8, 6, 4, 2]), -1.0)
        self.assertIsNone(ti._pearson([1, 1, 1, 1], [1, 2, 3, 4]))     # zero variance -> None
        self.assertIsNone(ti._pearson([1.0], [2.0]))                   # <3 points -> None


class TestAudit(unittest.TestCase):
    def test_module_selftest(self):
        self.assertEqual(ti._selftest(), 0)

    def test_read_only_grep_provable(self):
        # the AUDIT path (not the test-fixture selftest) issues no write statement.
        for fn in (ti.audit, ti.report_lines, ti._pearson):
            src = inspect.getsource(fn).upper()
            for verb in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "REPLACE INTO"):
                self.assertNotIn(verb, src, f"{fn.__name__} must not {verb.strip()}")

    def test_audit_does_not_mutate_db(self):
        tmp = Path(tempfile.mkdtemp())
        dbp = tmp / "t.db"
        conn = storage._connect_at(dbp)
        with conn:
            prov = {"blend": {"high": 30.0}, "included_high": ["a"],
                    "votes": [{"member_id": "a", "corrected_high": 30.0}]}
            conn.execute("INSERT INTO verdicts (issued_at, place, target_date, high, low, "
                         "confidence, actual_high, provenance_json) VALUES "
                         "('t','C','2026-05-01',30.0,25.0,'HIGH',30.0,?)", (json.dumps(prov),))
            conn.execute("INSERT INTO tracked_forecasts (source, issued_at, place, target_date, "
                         "fc_high, fc_low, actual_high) VALUES ('twc','t','C','2026-05-01',31.0,25.0,30.0)")
        conn.close()

        def _counts():
            c = storage._connect_at(dbp)
            try:
                return (c.execute("SELECT COUNT(*) FROM verdicts").fetchone()[0],
                        c.execute("SELECT COUNT(*) FROM tracked_forecasts").fetchone()[0])
            finally:
                c.close()

        before = _counts()
        ti.audit("twc", db_path=dbp)
        self.assertEqual(_counts(), before)                # read-only: nothing added or changed

    def test_empty_when_no_paired_days(self):
        tmp = Path(tempfile.mkdtemp())
        storage._connect_at(tmp / "t.db").close()
        res = ti.audit("twc", db_path=tmp / "t.db")
        self.assertEqual(res["cities"], {})
        self.assertIn("accruing", "\n".join(ti.report_lines(res)))

    def test_collinear_member_flagged_independent_not(self):
        tmp = Path(tempfile.mkdtemp())
        dbp = tmp / "t.db"
        conn = storage._connect_at(dbp)
        with conn:
            for i in range(31):                            # ≥ THRESHOLD_N paired days
                actual = 30.0
                twc_err = 0.6 if i % 2 else -0.4
                collin = actual + twc_err                  # member error == TWC error (r=1)
                indep = actual + (0.5 if (i // 2) % 2 else -0.5)
                final = (collin + indep) / 2
                prov = {"blend": {"high": final}, "included_high": ["collin", "indep"],
                        "votes": [{"member_id": "collin", "corrected_high": collin},
                                  {"member_id": "indep", "corrected_high": indep}]}
                conn.execute("INSERT INTO verdicts (issued_at, place, target_date, high, low, "
                             "confidence, actual_high, provenance_json) VALUES "
                             "('t','C',?,?,25.0,'HIGH',?,?)",
                             (f"2026-05-{i+1:02d}", final, actual, json.dumps(prov)))
                conn.execute("INSERT INTO tracked_forecasts (source, issued_at, place, "
                             "target_date, fc_high, fc_low, actual_high) VALUES "
                             "('twc','t','C',?,?,25.0,?)",
                             (f"2026-05-{i+1:02d}", actual + twc_err, actual))
        conn.close()
        e = ti.audit("twc", db_path=dbp)["cities"]["C"]
        self.assertEqual(e["status"], "MEASURED")
        self.assertIn("collin", e["flagged_collinear"])
        self.assertNotIn("indep", e["flagged_collinear"])


if __name__ == "__main__":
    unittest.main()
