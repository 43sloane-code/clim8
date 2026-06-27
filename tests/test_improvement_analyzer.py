"""Tests for the improvement analyzer's pure logic (tools/improvement_analyzer).

Covers miss classification (off-by-one vs gross, under vs over-call), the
dead-candidate guard (a proposal matching a ledger grep term is blocked), and the
recommendation defaults (accrue when thin; never endorse a dead lever)."""
import unittest

from tools.improvement_analyzer import analyze_misses, check_proposal, _recommendations


class TestImprovementAnalyzer(unittest.TestCase):
    def test_miss_classification(self):
        recent = [("d1", 31, 32, False),   # off-by-one, under
                  ("d2", 32, 33, False),   # off-by-one, under
                  ("d3", 31, 31, True),    # hit (ignored)
                  ("d4", 30, 34, False)]   # gross, under
        m = analyze_misses(recent)
        self.assertEqual(m, {"misses": 3, "off_by_one": 2, "gross": 1,
                             "under": 3, "over": 0})

    def test_over_call_and_no_misses(self):
        self.assertEqual(analyze_misses([("d", 34, 33, False)])["over"], 1)
        self.assertEqual(analyze_misses([("d", 31, 31, True)])["misses"], 0)

    def test_dead_ledger_guard_blocks_match(self):
        dead = [{"id": "D02", "candidate": "AIFS member", "verdict": "DEAD",
                 "evidence": "noise", "grep": ["aifs", "ecmwf_aifs025"]}]
        self.assertEqual(check_proposal("add AIFS as a 9th member", dead)["id"], "D02")
        self.assertIsNone(check_proposal("try a Kalman filter", dead))

    def test_recommendations_accrue_and_refuse_dead(self):
        m = analyze_misses([("d1", 31, 32, False), ("d2", 32, 33, False)])
        recs = _recommendations({"Manila": {"n": 3, "hits": 1},
                                 "Singapore": {"n": 5, "hits": 4}},
                                m, [{"id": "D1", "grep": []}], {"scored": 0})
        self.assertTrue(any("ACCRUE" in r for r in recs))          # thin n -> accrue
        self.assertTrue(any("OFF-BY-ONE" in r for r in recs))      # boundary read
        self.assertTrue(any("DEAD" in r for r in recs))            # guard mentioned


if __name__ == "__main__":
    unittest.main()
