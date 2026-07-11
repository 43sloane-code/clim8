"""KAT for the shadow scorer + human promotion gate (tools/shadow_score.py, Plan 3 Phase 5).

The loop's L1→L2 boundary. Pins, in order of what would hurt most if it broke:
  * the served path is NEVER touched — run_shadow + run_gate write only shadow_forecasts + the
    candidate's STATUS; the verdicts.high column is byte-for-byte identical before and after;
  * the transform is a pure function of frozen provenance (scale_bias / toward_naive), and a
    transform that moves the point TOWARD the truth scores a POSITIVE paired delta (and AWAY, negative);
  * all four terminal gate states resolve correctly — PROMOTE (human-gated, never auto-applied),
    FALSIFIED-SIGN (significant wrong direction), KILLED (autonomous early), EXPIRED;
  * the bootstrap runs at the Bonferroni-deflated α = 0.05 / K_candidates_ever — the fishing
    denominator from Phase 4 is carried into the significance bar, not dropped;
  * PROMOTE prints a human-review brief and only flags the candidate — it applies no transform.
Network-free; isolated temp DB + temp queue.

Run with:  PYTHONPATH=. python3 -m unittest tests.test_shadow -v
"""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from tools import lessons, shadow_score

# A provenance blob: raw votes 30.0/31.0 (naive 30.5), weighted-raw 30.7, bias +0.8 -> final 31.5.
_PROV = {"blend": {"high": 31.5, "high_pre_bias": 30.7, "bias_high": 0.8},
         "included_high": ["a", "b"],
         "votes": [{"member_id": "a", "raw_high": 30.0}, {"member_id": "b", "raw_high": 31.0}],
         "spread": {"high": 1.0}}
_CAND = {"id": "cand-test01", "place": "Testville",
         "transform": {"op": "scale_bias", "place": "Testville", "factor": 0.5},
         "predicted_effect_sign": "+", "status": "ACTIVE", "created_month": "2026-06",
         "claim": "halve the applied bias", "born_from": {"cells_scanned": 1}}
_TODAY = dt.date(2026, 7, 11)


class TestShadow(unittest.TestCase):
    def setUp(self):
        self._dir = Path(tempfile.mkdtemp())
        self._db = self._dir / "t.db"
        self._q = self._dir / "candidates.json"

    def _seed_verdicts(self, n=22, actual=30.0, high=31.5, place="Testville"):
        conn = lessons._connect_at(self._db)
        with conn:
            for i in range(n):
                conn.execute(
                    "INSERT INTO verdicts (issued_at, place, target_date, high, low, confidence, "
                    " actual_high, provenance_json, provenance_ok) VALUES (?,?,?,?,?,?,?,?,1)",
                    (f"2026-06-{i+1:02d}T00:00:00", place, f"2026-06-{i+1:02d}",
                     high, 25.0, "HIGH", actual, json.dumps(_PROV)))
        conn.close()

    def _write_queue(self, cands):
        self._q.write_text(json.dumps(cands, indent=1) + "\n")

    # ── module selftest ──────────────────────────────────────────────────────────────────────────
    def test_module_selftest(self):
        self.assertEqual(shadow_score._selftest(), 0)

    # ── transform is a pure function of provenance ───────────────────────────────────────────────
    def test_scale_bias_halves_applied_bias(self):
        got = shadow_score.apply_transform({"op": "scale_bias", "factor": 0.5}, _PROV)
        self.assertAlmostEqual(got, 30.7 + 0.4)              # weighted_raw + 0.5*bias

    def test_toward_naive_shrinks_weighting(self):
        got = shadow_score.apply_transform({"op": "toward_naive", "factor": 0.5}, _PROV)
        self.assertAlmostEqual(got, 31.4)                    # 30.5 + 0.5*(30.7-30.5) + 0.8

    def test_unscorable_provenance_returns_none(self):
        self.assertIsNone(shadow_score.apply_transform(
            {"op": "scale_bias", "factor": 0.5}, {"blend": {}}))

    # ── delta sign follows the geometry ──────────────────────────────────────────────────────────
    def test_moving_toward_actual_is_positive_delta(self):
        # actual 30 (bucket 30); shadow 31.1 is closer than served 31.5 -> candidate helped.
        day = shadow_score.score_day(_PROV, 31.5, 30.0, {"op": "scale_bias", "factor": 0.5})
        self.assertGreater(day["delta_logloss"], 0)

    def test_moving_away_from_actual_is_negative_delta(self):
        day = shadow_score.score_day(_PROV, 31.5, 30.0, {"op": "scale_bias", "factor": 3.0})
        self.assertLess(day["delta_logloss"], 0)

    # ── the four terminal gate states (controlled deltas; K=3 -> alpha_eff≈0.0167) ────────────────
    def test_gate_promote(self):
        deltas = [0.30 + 0.02 * ((i % 3) - 1) for i in range(24)]
        gv = shadow_score.evaluate_gate(deltas, 3, "2026-06", _TODAY)
        self.assertEqual(gv["outcome"], "PROMOTE")
        self.assertEqual(gv["status"], "PROMOTION-PENDING-HUMAN")

    def test_gate_falsified_sign(self):
        deltas = [-(0.30 + 0.02 * ((i % 3) - 1)) for i in range(24)]   # n≥20, reliably worse
        gv = shadow_score.evaluate_gate(deltas, 3, "2026-06", _TODAY)
        self.assertEqual(gv["outcome"], "FALSIFIED-SIGN")
        self.assertEqual(gv["status"], "KILLED")

    def test_gate_killed_early(self):
        deltas = [-(0.30 + 0.02 * ((i % 3) - 1)) for i in range(12)]   # 10 ≤ n < 20, wrong dir
        gv = shadow_score.evaluate_gate(deltas, 3, "2026-06", _TODAY)
        self.assertEqual(gv["outcome"], "KILLED")

    def test_gate_expired(self):
        gv = shadow_score.evaluate_gate([0.001, -0.001, 0.0], 3, "2026-06", dt.date(2026, 11, 1))
        self.assertEqual(gv["outcome"], "EXPIRED")

    def test_gate_accruing_default(self):
        gv = shadow_score.evaluate_gate([0.05, -0.04, 0.02], 3, "2026-06", _TODAY)
        self.assertEqual(gv["outcome"], "ACCRUING")
        self.assertEqual(gv["status"], "ACTIVE")

    def test_bonferroni_deflation_widens_the_bar(self):
        # Borderline deltas that straddle zero: at K=1 the CI clears zero and promotes; deflating the
        # SAME evidence to K=50 widens the interval back across zero and must NOT promote.
        deltas = [0.05 + 0.10 * ((i % 3) - 1) for i in range(24)]     # cycle -0.05, 0.05, 0.15
        loose = shadow_score.evaluate_gate(deltas, 1, "2026-06", _TODAY)
        strict = shadow_score.evaluate_gate(deltas, 50, "2026-06", _TODAY)
        self.assertLess(strict["alpha_eff"], loose["alpha_eff"])       # α = 0.05 / K
        self.assertEqual(loose["outcome"], "PROMOTE")
        self.assertNotEqual(strict["outcome"], "PROMOTE")              # the denominator bites
        self.assertLessEqual(strict["ci"][0], loose["ci"][0])          # lower bound moved down
        self.assertGreaterEqual(strict["ci"][1], loose["ci"][1])       # upper bound moved up

    # ── end-to-end: writes ONLY shadow + status, never the served number ─────────────────────────
    def test_run_shadow_then_gate_leaves_served_high_untouched(self):
        self._seed_verdicts(n=22, actual=30.0, high=31.5)
        self._write_queue([_CAND])
        sh = shadow_score.run_shadow(db_path=self._db, queue_path=self._q)
        self.assertEqual(sh["scored"]["cand-test01"], 22)
        g = shadow_score.run_gate(db_path=self._db, queue_path=self._q, today=_TODAY)
        self.assertEqual(g["verdicts"][0]["outcome"], "PROMOTE")
        self.assertTrue(any("HUMAN REVIEW REQUIRED" in b for b in g["briefs"]))
        # the served high is byte-for-byte identical (zero served-path bytes changed)
        conn = lessons._connect_at(self._db)
        highs = {r[0] for r in conn.execute("SELECT high FROM verdicts").fetchall()}
        shadow_rows = conn.execute("SELECT COUNT(*) FROM shadow_forecasts").fetchone()[0]
        conn.close()
        self.assertEqual(highs, {31.5})
        self.assertEqual(shadow_rows, 22)
        # the candidate is only FLAGGED — no transform was applied to any served value
        self.assertEqual(json.loads(self._q.read_text())[0]["status"], "PROMOTION-PENDING-HUMAN")

    def test_gate_skips_deferred_budget_candidates(self):
        self._seed_verdicts(n=22)
        deferred = {**_CAND, "id": "cand-def", "status": "DEFERRED-BUDGET"}
        self._write_queue([deferred])
        sh = shadow_score.run_shadow(db_path=self._db, queue_path=self._q)
        self.assertEqual(sh["candidates"], 0)                # ACTIVE-only; DEFERRED not scored
        g = shadow_score.run_gate(db_path=self._db, queue_path=self._q, today=_TODAY)
        self.assertEqual(g["changed"], 0)
        self.assertEqual(json.loads(self._q.read_text())[0]["status"], "DEFERRED-BUDGET")

    def test_run_shadow_is_idempotent(self):
        self._seed_verdicts(n=12)
        self._write_queue([_CAND])
        shadow_score.run_shadow(db_path=self._db, queue_path=self._q)
        shadow_score.run_shadow(db_path=self._db, queue_path=self._q)   # second pass
        conn = lessons._connect_at(self._db)
        rows = conn.execute("SELECT COUNT(*) FROM shadow_forecasts").fetchone()[0]
        conn.close()
        self.assertEqual(rows, 12)                            # INSERT OR REPLACE — no duplicates


if __name__ == "__main__":
    unittest.main()
