"""KATs for the TWC 9th-member gate scorer (tools/twc_gate_score.py) and its D30 outcome.

The gate was scored ONCE (2026-07-29) under the frozen prereg — these tests pin the machinery
that produced the verdict so a later edit cannot silently move the bar, plus the ledger record
of the outcome itself (D30: TWC stays a display-only cross-reference, never blended)."""
import json
import unittest
from pathlib import Path

from tools import twc_gate_score as g

ROOT = Path(__file__).resolve().parent.parent


class TestGateMachinery(unittest.TestCase):
    def test_selftest_passes(self):
        self.assertEqual(g._selftest(), 0)

    def test_walkforward_is_leak_free(self):
        """TWC stats at date t may use only pairs settled at capture time (<= t-2)."""
        pairs = [{"place": "X", "target_date": f"2026-07-{d:02d}", "issued_at": "i",
                  "fc_high": 31.0, "actual_high": 30.0} for d in range(1, 9)]
        self.assertIsNone(g.twc_walkforward_stats(pairs, "X", "2026-07-06"))   # < 5 usable
        st = g.twc_walkforward_stats(pairs, "X", "2026-07-08")
        self.assertEqual(st["n_prior"], 6)                                     # 07-07 NOT used

    def test_screen_matches_shipped_rule(self):
        """MAD screen with keep-all fallback; the outlier never reaches the blend."""
        panel = [("a", 30.0, 1.0), ("b", 30.2, 0.5), ("c", 31.0, 0.8), ("x", 39.0, 1.0)]
        _, surv = g._screen_and_blend(panel)
        self.assertNotIn("x", surv)
        _, surv2 = g._screen_and_blend([("a", 10.0, 1.0), ("b", 50.0, 1.0)])
        self.assertEqual(set(surv2), {"a", "b"})                               # keep-all fallback

    def test_folds_are_chronological_halves(self):
        fp = [{"place": "X", "target_date": f"2026-07-0{d}"} for d in (5, 1, 3, 2, 4)]
        g.assign_folds(fp)
        got = {p["target_date"]: p["fold"] for p in fp}
        self.assertEqual(got, {"2026-07-01": "A", "2026-07-02": "A", "2026-07-03": "B",
                               "2026-07-04": "B", "2026-07-05": "B"})

    def test_recon_validates_at_served_grain(self):
        """verdicts.high is stored at 0.1 C — the recon check compares AT THAT GRAIN (the
        2026-07-29 tolerance bug mis-quarantined half the comparator set at 0.02)."""
        served = 30.4                                  # stored round(blend, 1)
        self.assertLessEqual(abs(round(30.3913, 1) - served), g.RECON_TOL)   # passes
        self.assertGreater(abs(round(30.34, 1) - served), g.RECON_TOL)       # genuine mismatch


class TestD30Outcome(unittest.TestCase):
    def test_dead_ledger_records_d30(self):
        rows = [json.loads(x) for x in
                (ROOT / "ledger" / "dead_candidates.jsonl").read_text().splitlines() if x.strip()]
        d30 = [r for r in rows if r.get("id") == "D30"]
        self.assertEqual(len(d30), 1)
        self.assertEqual(d30[0]["verdict"], "DEAD")
        self.assertIn("twc", d30[0]["candidate"].lower())
        self.assertIn("0.9172", d30[0]["evidence"])          # the G4 driver-killer, pinned

    def test_prereg_is_stamped_failed(self):
        text = (ROOT / "ledger" / "preregistered" / "twc_member_gate.md").read_text()
        self.assertIn("FAILED THE GATE (2026-07-29", text)
        self.assertIn("D30", text)

    def test_gate_report_exists(self):
        # the .txt rendering is a generated local artifact (reports/*.txt is gitignored by
        # convention); the machine-readable .json is the tracked, citable evidence (D29 pattern)
        self.assertTrue((ROOT / "reports" / "twc_gate_2026-07-29.json").exists())


if __name__ == "__main__":
    unittest.main()
