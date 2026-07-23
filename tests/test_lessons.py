"""KAT for the lessons aggregator + budgeted candidate queue (tools/lessons.py, Plan 3 Phase 4).

The loop's throttle. Pins: the binomial sign-test; the n≥8 floor; INPUT (no transform) is never a
candidate; the cells-scanned denominator is carried onto every candidate (the multiple-comparisons
denominator, never hidden); and — the crux — the HARD budget (≤2 ACTIVE/city, ≤4/month) sends
excess patterns to DEFERRED-BUDGET instead of certifying an unbounded portfolio of noise.
Network-free; isolated temp DB + temp queue.

Run with:  PYTHONPATH=. python3 -m unittest tests.test_lessons -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import lessons
from weather_council import storage


def _bias_hurt_cell(conn, place, month="2026-07", n=10, hurt=9):
    with conn:
        for i in range(n):
            comp = 0.8 if i < hurt else -0.8      # same-sign-as-total(+1) => hurt
            conn.execute(
                "INSERT INTO postmortems (place, target_date, attr, scored_at, total_error, "
                "attributed_cause, components_json) VALUES (?,?, 'high', ?, 1.0, 'BIAS', ?)",
                (place, f"{month}-{i+1:02d}", f"{month}-11T00:00:00",
                 json.dumps({"bias_contribution": comp})))


class TestLessons(unittest.TestCase):
    def setUp(self):
        self._dir = Path(tempfile.mkdtemp())
        self._db = self._dir / "t.db"
        self._q = self._dir / "candidates.json"

    def _conn(self):
        return storage._connect_at(self._db)

    def test_module_selftest(self):
        lessons._selftest()

    def test_binom_sign_test(self):
        self.assertLess(lessons._binom_p(10, 10), 0.05)
        self.assertLess(lessons._binom_p(9, 10), 0.05)
        self.assertGreater(lessons._binom_p(6, 10), 0.05)   # not significant
        self.assertGreater(lessons._binom_p(4, 7), 0.05)    # below n floor territory

    def test_thin_cell_not_a_pattern(self):
        conn = self._conn()
        _bias_hurt_cell(conn, "Thin, X", n=5, hurt=5)        # < MIN_CELL_N
        conn.close()
        det = lessons.detect_patterns(db_path=self._db)
        self.assertEqual(det["patterns"], [])
        self.assertEqual(det["cells_scanned"], 1)            # scanned but not emitted

    def test_pattern_emits_candidate_with_denominator(self):
        conn = self._conn()
        _bias_hurt_cell(conn, "Karachi, Pakistan")
        conn.close()
        det = lessons.detect_patterns(db_path=self._db)
        self.assertEqual(len(det["patterns"]), 1)
        res = lessons.emit_candidates(det, queue_path=self._q, db_path=self._db)
        self.assertEqual(len(res["emitted"]), 1)
        cand = res["emitted"][0]
        self.assertEqual(cand["transform"]["op"], "scale_bias")
        self.assertEqual(cand["predicted_effect_sign"], "+")
        self.assertEqual(cand["born_from"]["cells_scanned"], 1)   # denominator carried
        self.assertEqual(cand["status"], "ACTIVE")

    def test_budget_per_month_defers_excess(self):
        # 5 cities each with a BIAS-hurt pattern, same month -> 4 ACTIVE, 5th DEFERRED-BUDGET.
        conn = self._conn()
        cities = ["A, X", "B, X", "C, X", "D, X", "E, X"]
        for c in cities:
            _bias_hurt_cell(conn, c)
        conn.close()
        det = lessons.detect_patterns(db_path=self._db)
        self.assertEqual(len(det["patterns"]), 5)
        res = lessons.emit_candidates(det, queue_path=self._q, db_path=self._db)
        self.assertEqual(len(res["emitted"]), lessons.MAX_ACTIVE_PER_MONTH)   # 4 active
        self.assertEqual(len(res["deferred"]), 5 - lessons.MAX_ACTIVE_PER_MONTH)
        self.assertTrue(all(c["status"] == "DEFERRED-BUDGET" for c in res["deferred"]))
        # every candidate ever carries a running K (the Bonferroni denominator)
        self.assertEqual(res["K_candidates_ever"], 5)

    def test_idempotent_reemit(self):
        conn = self._conn()
        _bias_hurt_cell(conn, "Karachi, Pakistan")
        conn.close()
        det = lessons.detect_patterns(db_path=self._db)
        lessons.emit_candidates(det, queue_path=self._q, db_path=self._db)
        again = lessons.emit_candidates(det, queue_path=self._q, db_path=self._db)
        self.assertEqual(again["emitted"], [])
        self.assertEqual(again["skipped_existing"], 1)
        self.assertEqual(len(json.loads(self._q.read_text())), 1)   # no duplicate row


if __name__ == "__main__":
    unittest.main()
