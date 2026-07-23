"""KAT: Interpretation.outliers_set_aside must equal the number of members
ACTUALLY set aside by _blend. Previously _interpret counted 'outlier' notes,
which the keep-all fallback (every member flagged -> all re-included) left
behind, reporting N set aside while members_used still included all N.

Stdlib unittest only. Run with:
    PYTHONPATH=. python3 -m unittest tests.test_outliers_set_aside -v
"""
from __future__ import annotations

import unittest

from weather_council.agents import Vote, MemberSpec, Skill
from weather_council.council import Council


def _vote(mid, high):
    spec = MemberSpec(mid, mid, mid, "")
    sk = Skill(bias=0.0, mae_raw=1.0, mae_corrected=1.0, n=30)
    return Vote(spec, "2026-07-23", high, high - 9.0, high, high - 9.0,
                sk, sk, True)


class TestOutliersSetAsideConsistency(unittest.TestCase):
    def test_excluded_member_counted_and_out_of_blend(self):
        c = Council.__new__(Council)        # _blend/_interpret need no init state
        votes = [_vote(m, h) for m, h in
                 (("a", 20.0), ("b", 21.0), ("c", 19.5), ("d", 40.0))]
        blend, inc, _spread, _wts, n_set_aside = c._blend(votes, "high")
        self.assertEqual(n_set_aside, 1)                          # only d trips the screen
        self.assertEqual(sorted(inc), ["a", "b", "c"])
        excluded = [v for v in votes if any("outlier" in n for n in v.notes)]
        self.assertEqual([v.spec.member_id for v in excluded], ["d"])
        self.assertEqual(n_set_aside + len(inc), 4)               # usable = kept + set aside
        self.assertAlmostEqual(blend, (20.0 + 21.0 + 19.5) / 3)   # equal weights (same skill)
        # the Interpretation threads the ACTUAL count — self-consistent with the blend
        itp = c._interpret(votes, inc, 60, n_set_aside)
        self.assertEqual(itp.outliers_set_aside, 1)
        self.assertEqual(itp.members_used, 4)

    def test_no_outliers_reports_zero_and_adds_no_notes(self):
        c = Council.__new__(Council)
        votes = [_vote(m, h) for m, h in
                 (("a", 20.0), ("b", 21.0), ("c", 19.0), ("d", 20.5))]
        _blend, inc, _s, _w, n_set_aside = c._blend(votes, "high")
        self.assertEqual(n_set_aside, 0)
        self.assertEqual(len(inc), 4)
        self.assertFalse(any("outlier" in n for v in votes for n in v.notes))
        itp = c._interpret(votes, inc, 60, n_set_aside)
        self.assertEqual(itp.outliers_set_aside, 0)


if __name__ == "__main__":
    unittest.main()
