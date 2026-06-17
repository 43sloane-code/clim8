"""Network-free tests for the mechanism-convergence layer (convergence.py).

Stdlib unittest only. Run with:
    PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import unittest



class TestMechanismConvergence(unittest.TestCase):
    """The recommend-only convergence layer: independent mechanisms either cohere
    into one affirmed reading or they don't, and we report which — never moving
    the headline. Locks in the three guardrails (one-directional lineage
    de-correlation, significance gating, C7 gate) and the ABSTAIN/CONTESTED
    honesty."""

    def _m(self, name, lineage, est, mae, n=40):
        from weather_council.convergence import Mechanism
        return Mechanism(name=name, lineage=lineage, estimate_c=est, mae_c=mae, n=n)

    def test_scores_best_is_100_and_unusable_is_zero(self):
        from weather_council.convergence import score_mechanisms
        scores = score_mechanisms([
            self._m("council", "nwp", 20.0, 0.5),
            self._m("naive avg", "nwp", 20.2, 1.0),
            self._m("climatology", "clim", 21.0, 1.0, n=3),  # too few held-out days
        ])
        by = {s.name: s for s in scores}
        self.assertEqual(by["council"].score, 100.0)       # most precise = 100
        self.assertLess(by["naive avg"].score, 100.0)
        self.assertTrue(by["council"].usable)
        self.assertFalse(by["climatology"].usable)         # n<MIN_N → unused
        self.assertEqual(by["climatology"].score, 0.0)

    def test_affirmed_when_independents_cohere(self):
        from weather_council.convergence import converge
        c = converge("high", 20.0, [
            self._m("council", "nwp", 20.0, 0.5),
            self._m("naive avg", "nwp", 20.2, 0.6),
            self._m("climatology", "clim", 20.1, 1.0),
        ], residual_spread_c=1.0, n_resid=40)
        self.assertEqual(c.status, "AFFIRMED")
        self.assertEqual(c.independent_lineages, 2)
        self.assertGreaterEqual(c.affirmation, 50.0)
        self.assertFalse(c.significant)
        self.assertFalse(c.allowed_to_move)
        # Consensus sits on the headline; affirmed value ≈ headline.
        self.assertAlmostEqual(c.affirmed_c, 20.0, delta=0.15)

    def test_contested_when_independents_diverge_beyond_noise(self):
        from weather_council.convergence import converge
        c = converge("high", 20.0, [
            self._m("council", "nwp", 20.0, 0.5),
            self._m("climatology", "clim", 26.0, 1.0),
            self._m("persistence", "persist", 14.0, 1.0),
        ], residual_spread_c=1.0, n_resid=40)
        from weather_council.convergence import AFFIRM_MIN
        self.assertEqual(c.status, "CONTESTED")
        self.assertLess(c.affirmation, AFFIRM_MIN)
        self.assertIsNone(c.nudge_c)          # never fabricate a consensus nudge
        self.assertFalse(c.allowed_to_move)

    def test_abstains_with_fewer_than_two_independent_lineages(self):
        from weather_council.convergence import converge
        # Two mechanisms but SAME lineage → only one independent lineage.
        c = converge("high", 20.0, [
            self._m("council", "nwp", 20.0, 0.5),
            self._m("naive avg", "nwp", 20.1, 0.6),
        ], residual_spread_c=1.0, n_resid=40)
        self.assertEqual(c.status, "ABSTAIN")
        self.assertEqual(c.independent_lineages, 1)
        self.assertIsNone(c.affirmed_c)
        self.assertFalse(c.allowed_to_move)

    def test_lineage_de_correlation_counts_shared_lineage_once(self):
        from weather_council.convergence import converge
        # Two nwp members agree at 20; one climatology at 25. The shared lineage
        # must count ONCE (2 independent lineages, not 3), and it is represented by
        # its BEST member's estimate (council 20.0), never dragged toward a sibling.
        c = converge("high", 20.0, [
            self._m("council", "nwp", 20.0, 0.5),
            self._m("naive avg", "nwp", 20.0, 0.9),
            self._m("climatology", "clim", 25.0, 0.5),
        ], residual_spread_c=1.0, n_resid=40)
        self.assertEqual(c.independent_lineages, 2)
        self.assertEqual(len(c.lineages), 2)
        self.assertEqual(len(c.scores), 3)    # all shown, but lineage counted once
        nwp = [le for le in c.lineages if le.lineage == "nwp"][0]
        self.assertAlmostEqual(nwp.estimate_c, 20.0)   # best member, not an average
        self.assertAlmostEqual(nwp.eff_mae_c, 0.5)

    def test_significant_nudge_is_recommend_only_until_c7_validates(self):
        from weather_council.convergence import converge
        mechs = [
            self._m("council", "nwp", 20.0, 1.0),
            self._m("climatology", "clim", 21.0, 0.9),
            self._m("persistence", "persist", 21.2, 1.0),
        ]
        # A precise pair of independent lineages pulls the headline > floor.
        c = converge("high", 20.0, mechs, residual_spread_c=1.0, n_resid=40,
                     c7_validated=False)
        self.assertEqual(c.status, "AFFIRMED_NUDGE")
        self.assertTrue(c.significant)
        self.assertIsNotNone(c.nudge_c)
        self.assertGreater(c.nudge_c, 0.0)
        self.assertFalse(c.allowed_to_move)   # C7 NOT validated → annotation only
        # Same evidence, but once C7 has earned a validated edge it MAY move.
        c2 = converge("high", 20.0, mechs, residual_spread_c=1.0, n_resid=40,
                      c7_validated=True)
        self.assertEqual(c2.status, "AFFIRMED_NUDGE")
        self.assertTrue(c2.allowed_to_move)

    def test_tiny_nudge_is_not_significant(self):
        from weather_council.convergence import converge
        c = converge("high", 20.0, [
            self._m("council", "nwp", 20.0, 0.5),
            self._m("climatology", "clim", 20.1, 0.5),
        ], residual_spread_c=1.0, n_resid=40)
        # Independents agree; the sub-floor pull is not surfaced as a nudge.
        self.assertEqual(c.status, "AFFIRMED")
        self.assertFalse(c.significant)


if __name__ == "__main__":
    unittest.main()

if __name__ == "__main__":
    unittest.main()
