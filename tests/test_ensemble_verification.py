"""Network-free oracle tests for the ensemble-calibration verification suite
(ensemble_verification.py): the shared uniformity core, its orthogonal
shape decomposition, the Talagrand rank histogram, and the PIT histogram.

Every fixture has a dispersion/bias known BY CONSTRUCTION, so each diagnostic is
checked against a ground truth rather than against itself. The headline
regression guard is `test_calibrated_panel_is_not_a_discreteness_artifact`: an
8-member calibrated panel must read FLAT/CALIBRATED, proving the randomized-rank
fix actually removed the m+1-into-B binning artifact that earlier made a
perfectly calibrated panel look under-dispersed.

Stdlib unittest only. Run with:
    PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import random
import unittest

from weather_council.ensemble_verification import (
    uniformity_eval, rank_histogram_eval, pit_calibration_eval,
    _decompose, _normalised_rank, DEFAULT_BINS, MIN_PER_BIN,
)


def _panel(member_sd, obs_mu, obs_sd, seed, m=8, days=1200):
    """Synthetic (members, obs) days with a KNOWN dispersion ratio. obs_sd is the
    realized-error scale; member_sd is the panel's. tight: member_sd<obs_sd."""
    rng = random.Random(seed)
    return [([rng.gauss(0.0, member_sd) for _ in range(m)], rng.gauss(obs_mu, obs_sd))
            for _ in range(days)]


class TestUniformityCore(unittest.TestCase):
    def test_uniform_sample_reads_flat(self):
        rng = random.Random(1)
        u = uniformity_eval([rng.random() for _ in range(4000)])
        self.assertIsNotNone(u)
        self.assertTrue(u.uniform)
        self.assertEqual(u.shape, "flat")
        self.assertLess(abs(u.edge_ratio - 1.0), 0.15)

    def test_too_few_values_returns_none(self):
        # Below bins * min_per_bin the chi-square approximation is untrustworthy.
        rng = random.Random(2)
        few = [rng.random() for _ in range(DEFAULT_BINS * MIN_PER_BIN - 1)]
        self.assertIsNone(uniformity_eval(few))

    def test_fewer_than_two_bins_returns_none(self):
        rng = random.Random(3)
        self.assertIsNone(uniformity_eval([rng.random() for _ in range(500)], bins=1))

    def test_out_of_range_values_are_dropped(self):
        # Defensive guard: values outside [0,1] must not be binned. A clean uniform
        # core polluted with out-of-range junk still reads flat on the in-range part.
        rng = random.Random(4)
        vals = [rng.random() for _ in range(2000)] + [1.5, -0.2, 9.9, float("nan")]
        u = uniformity_eval(vals)
        self.assertIsNotNone(u)
        self.assertEqual(u.n, 2000)            # only the in-range values counted

    def test_edge_piled_sample_is_u_shaped(self):
        # Mass pushed to the tails -> convex (U), edge_ratio > 1, not uniform.
        rng = random.Random(5)
        vals = []
        for _ in range(4000):
            x = rng.random()
            vals.append(x * x if rng.random() < 0.5 else 1.0 - x * x)
        u = uniformity_eval(vals)
        self.assertFalse(u.uniform)
        self.assertEqual(u.shape, "u")
        self.assertGreater(u.edge_ratio, 1.0)
        self.assertGreater(u.convex_coef, 0.0)

    def test_centre_piled_sample_is_dome_shaped(self):
        # Mass pulled to the centre (mean of two uniforms) -> dome, edge_ratio < 1.
        rng = random.Random(6)
        vals = [(rng.random() + rng.random()) / 2.0 for _ in range(4000)]
        u = uniformity_eval(vals)
        self.assertFalse(u.uniform)
        self.assertEqual(u.shape, "dome")
        self.assertLess(u.edge_ratio, 1.0)
        self.assertLess(u.convex_coef, 0.0)

    def test_tilted_sample_is_named_a_tilt(self):
        # A linear ramp toward 1 (max of two uniforms) -> tilt, not a dispersion read.
        rng = random.Random(7)
        vals = [max(rng.random(), rng.random()) for _ in range(4000)]
        u = uniformity_eval(vals)
        self.assertFalse(u.uniform)
        self.assertIn(u.shape, ("tilt-up", "tilt-down"))
        # Mass toward 1 means rising counts -> positive tilt -> 'tilt-up'.
        self.assertEqual(u.shape, "tilt-up")
        self.assertGreater(u.tilt_coef, 0.0)


class TestDecompositionOrthogonality(unittest.TestCase):
    """The convex (dispersion) and linear (bias) bases must be orthogonal, so a
    pure deviation of one kind leaks no chi-square into the other."""

    def test_pure_linear_deviation_has_no_convex_chi2(self):
        # Counts on a straight ramp: deviation is purely linear.
        expected = 50.0
        counts = [int(expected + 4.0 * (i - 4.5)) for i in range(10)]
        convex, tilt, chi2_convex, chi2_tilt = _decompose(counts, expected)
        self.assertGreater(chi2_tilt, 0.0)
        self.assertAlmostEqual(chi2_convex, 0.0, places=6)
        self.assertGreater(abs(tilt), 0.0)
        self.assertAlmostEqual(convex, 0.0, places=6)

    def test_pure_symmetric_u_has_no_tilt_chi2(self):
        # A symmetric parabola in the counts: deviation is purely convex.
        expected = 50.0
        counts = [int(expected + 2.0 * ((i - 4.5) ** 2 - 8.25)) for i in range(10)]
        convex, tilt, chi2_convex, chi2_tilt = _decompose(counts, expected)
        self.assertGreater(chi2_convex, 0.0)
        self.assertAlmostEqual(chi2_tilt, 0.0, places=6)
        self.assertGreater(convex, 0.0)
        self.assertAlmostEqual(tilt, 0.0, places=6)


class TestNormalisedRank(unittest.TestCase):
    def test_rank_is_in_unit_interval_and_uses_strict_below(self):
        random.Random(0)
        # obs above all 4 members: rank=4, value=(4+V)/5 in [0.8,1.0).
        v = _normalised_rank([0.0, 1.0, 2.0, 3.0], 9.0, random.Random(0))
        self.assertTrue(0.8 <= v < 1.0)
        # obs below all members: rank=0, value=(0+V)/5 in [0.0,0.2).
        v = _normalised_rank([0.0, 1.0, 2.0, 3.0], -9.0, random.Random(0))
        self.assertTrue(0.0 <= v < 0.2)


class TestRankHistogram(unittest.TestCase):
    def test_calibrated_panel_reads_flat(self):
        rh = rank_histogram_eval(_panel(1.0, 0.0, 1.0, 11))
        self.assertEqual(rh.verdict, "CALIBRATED")
        self.assertEqual(rh.diag.shape, "flat")

    def test_calibrated_panel_is_not_a_discreteness_artifact(self):
        # REGRESSION GUARD. An 8-member calibrated panel has only 9 discrete ranks;
        # binning those into 10 bins WITHOUT the randomized-rank fix manufactures a
        # spurious U and a huge z. The randomized rank (rank+V)/(m+1) must keep this
        # FLAT with an edge ratio near 1 — proving real under-dispersion, not a
        # binning artifact, is what would trip the U verdict.
        rh = rank_histogram_eval(_panel(1.0, 0.0, 1.0, 12, m=8))
        self.assertEqual(rh.verdict, "CALIBRATED")
        self.assertTrue(rh.diag.uniform)
        self.assertLess(abs(rh.diag.edge_ratio - 1.0), 0.25)

    def test_tight_panel_is_under_dispersed(self):
        rh = rank_histogram_eval(_panel(0.45, 0.0, 1.0, 13))
        self.assertEqual(rh.verdict, "UNDER-DISPERSED")
        self.assertEqual(rh.diag.shape, "u")
        self.assertGreater(rh.diag.edge_ratio, 1.0)

    def test_wide_panel_is_over_dispersed(self):
        rh = rank_histogram_eval(_panel(2.2, 0.0, 1.0, 14))
        self.assertEqual(rh.verdict, "OVER-DISPERSED")
        self.assertEqual(rh.diag.shape, "dome")
        self.assertLess(rh.diag.edge_ratio, 1.0)

    def test_cold_biased_panel_tilts_up(self):
        # obs runs above the members -> rank piles high -> tilt-up -> BIASED COLD.
        rh = rank_histogram_eval(_panel(1.0, 1.2, 1.0, 15))
        self.assertEqual(rh.verdict, "BIASED COLD")
        self.assertEqual(rh.diag.shape, "tilt-up")

    def test_seeded_result_is_reproducible(self):
        data = _panel(1.0, 0.0, 1.0, 16)
        a = rank_histogram_eval(data, seed=99)
        b = rank_histogram_eval(data, seed=99)
        self.assertEqual(a.diag.bins, b.diag.bins)
        self.assertEqual(a.diag.z, b.diag.z)

    def test_min_members_filter_drops_thin_panels(self):
        # Single-member "panels" cannot be ranked; with min_members=2 they are
        # dropped, leaving too few usable days -> None.
        thin = [([0.3], 0.1) for _ in range(1200)]
        self.assertIsNone(rank_histogram_eval(thin, min_members=2))

    def test_too_few_days_returns_none(self):
        self.assertIsNone(rank_histogram_eval(_panel(1.0, 0.0, 1.0, 17, days=20)))


class TestPITCalibration(unittest.TestCase):
    def test_uniform_pit_is_calibrated(self):
        rng = random.Random(21)
        pc = pit_calibration_eval([rng.random() for _ in range(2000)])
        self.assertEqual(pc.verdict, "CALIBRATED")
        self.assertEqual(pc.diag.shape, "flat")

    def test_edge_piled_pit_is_over_confident(self):
        rng = random.Random(22)
        pits = []
        for _ in range(2000):
            x = rng.random()
            pits.append(x * x if rng.random() < 0.5 else 1.0 - x * x)
        pc = pit_calibration_eval(pits)
        self.assertEqual(pc.verdict, "OVER-CONFIDENT")
        self.assertEqual(pc.diag.shape, "u")

    def test_centre_piled_pit_is_under_confident(self):
        rng = random.Random(23)
        pits = [(rng.random() + rng.random()) / 2.0 for _ in range(2000)]
        pc = pit_calibration_eval(pits)
        self.assertEqual(pc.verdict, "UNDER-CONFIDENT")
        self.assertEqual(pc.diag.shape, "dome")

    def test_too_few_pits_returns_none(self):
        rng = random.Random(24)
        self.assertIsNone(pit_calibration_eval([rng.random() for _ in range(20)]))


if __name__ == "__main__":
    unittest.main()
