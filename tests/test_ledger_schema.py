"""Network-free tests for candidate 45 — the ledger SCHEMA v2 validator. Proves the
classifier and validator behave, AND that the LIVE ledger is schema-clean (every
in-scope candidate entry of the 40-45 program carries the honesty fields).

  * classify_entry separates LEGACY (no id) / PRE_V2 (id, no candidate) / SCOPE
    (has a candidate field, must validate);
  * validate_v2 requires every honesty field as a KEY (null allowed where N/A),
    accepts EITHER bootstrap_ci or bootstrap_ci_95, and enforces light types;
  * audit aggregates correctly and the live ledger is CLEAN (no INVALID in-scope
    entries) — the 40-45 program entries are uniformly v2.

Stdlib unittest only. Run with:
    PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import json
import os
import unittest

from tools.ledger_schema import (
    classify_entry, validate_v2, audit,
)

LEDGER = os.path.join(os.path.dirname(__file__), "..", ".harness_opt", "ledger.json")


def _good_scope_entry(**overrides):
    e = {
        "id": 99, "candidate": 99, "date": "2026-06-10", "kind": "ACCURACY",
        "title": "t", "verdict": "NO_BAKE", "evaluation": "held-out",
        "K_candidates_evaluated": 1, "n_paired_rows": {"a": 346},
        "score_delta_crps": {"a": 0.0}, "bootstrap_ci_95": None,
        "cost": {"wall_minutes": 10, "web_fetches": 0},
        "artifacts": ["x.py"], "acceptance": "bar", "result": "no edge",
    }
    e.update(overrides)
    return e


class TestLedgerSchema(unittest.TestCase):
    def test_classify_three_eras(self):
        self.assertEqual(classify_entry({"ts": "t", "name": "n", "verdict": "NOTE"}), "LEGACY")
        self.assertEqual(classify_entry({"id": 38, "kind": "NO-BAKE+SIM"}), "PRE_V2")
        self.assertEqual(classify_entry({"id": 40, "candidate": 40}), "SCOPE")

    def test_validate_clean_entry(self):
        self.assertEqual(validate_v2(_good_scope_entry()), [])

    def test_ci_oneof_either_key_satisfies(self):
        # bootstrap_ci alone is fine.
        e = _good_scope_entry()
        del e["bootstrap_ci_95"]
        e["bootstrap_ci"] = {"ci_level": 0.9}
        self.assertEqual(validate_v2(e), [])
        # neither key -> a problem.
        e2 = _good_scope_entry()
        del e2["bootstrap_ci_95"]
        probs = validate_v2(e2)
        self.assertTrue(any("bootstrap-CI" in p for p in probs))

    def test_missing_required_is_flagged(self):
        e = _good_scope_entry()
        del e["result"]
        probs = validate_v2(e)
        self.assertIn("missing required field: result", probs)

    def test_type_checks(self):
        self.assertTrue(any("K_candidates_evaluated must be an int"
                            in p for p in validate_v2(_good_scope_entry(K_candidates_evaluated="two"))))
        self.assertTrue(any("cost must be a dict" in p
                            for p in validate_v2(_good_scope_entry(cost=10))))
        self.assertTrue(any("artifacts must be a list" in p
                            for p in validate_v2(_good_scope_entry(artifacts="x.py"))))
        # null score_delta_crps / n_paired_rows are allowed (non-accuracy kinds).
        self.assertEqual(validate_v2(_good_scope_entry(score_delta_crps=None, n_paired_rows=None,
                                                       kind="PROCESS")), [])

    def test_audit_counts_and_scope(self):
        log = [
            {"ts": "t", "name": "n", "verdict": "NOTE"},          # legacy
            {"id": 38, "kind": "NO-BAKE+SIM"},                    # pre-v2
            _good_scope_entry(id=40, candidate=40),               # v2 ok
            _good_scope_entry(id=41, candidate=41, result=None),  # invalid? result=None is present -> ok
        ]
        rep = audit(log)
        self.assertEqual(rep["legacy"], 1)
        self.assertEqual(rep["pre_v2"], [38])
        self.assertIn(40, rep["v2_conformant"])
        self.assertTrue(rep["clean"])
        # now make one genuinely invalid
        bad = _good_scope_entry(id=42, candidate=42)
        del bad["evaluation"]
        rep2 = audit(log + [bad])
        self.assertFalse(rep2["clean"])
        self.assertIn(42, rep2["invalid"])

    def test_live_ledger_is_clean(self):
        """The real ledger: every in-scope candidate entry (the 40-45 program) is
        schema-v2 conformant; legacy/pre-v2 entries are grandfathered."""
        if not os.path.exists(LEDGER):
            self.skipTest("missing ledger")
        with open(LEDGER, encoding="utf-8") as fh:
            log = json.load(fh)["log"]
        rep = audit(log)
        self.assertTrue(rep["clean"], f"INVALID entries: {rep['invalid']}")
        # the program entries we have appended so far are all present & conformant.
        for cid in (40, 41, 42, 43, 44):
            self.assertIn(cid, rep["v2_conformant"], cid)
        self.assertEqual(rep["pre_v2"], [35, 36, 37, 38, 39])


if __name__ == "__main__":
    unittest.main()
