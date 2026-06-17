"""Network-free tests for the calibration/compare layer: the unified residual-calibration estimator, empirical bucket probabilities, and conditional-spread calibration.

Stdlib unittest only. Run with:
    PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import random
import unittest

from weather_council.scoring import interval_coverage, quantile
from weather_council.compare import residual_calibration, compare_high, MIN_RESIDUALS
from weather_council.market import WeatherMarket, MarketBucket


class TestUnifiedCalibration(unittest.TestCase):
    """residual_calibration must now use the SAME estimator as Validation."""

    def test_coverage_matches_growing_prefix_reference(self):
        rng = random.Random(11)
        res = [rng.gauss(0.3, 2.0) for _ in range(60)]
        cal = residual_calibration(res)
        hits = cov_n = 0
        for i in range(MIN_RESIDUALS, len(res)):
            covered, _ = interval_coverage(res[:i], res[i])
            hits += 1 if covered else 0
            cov_n += 1
        self.assertEqual(cal.coverage_n, cov_n)
        self.assertAlmostEqual(cal.coverage_80, round(hits / cov_n, 2), places=9)

    def test_quantiles_use_linear_interp_convention(self):
        rng = random.Random(12)
        res = [rng.gauss(0, 1.5) for _ in range(40)]
        cal = residual_calibration(res)
        self.assertAlmostEqual(cal.p10, round(quantile(res, 0.10), 2), places=9)
        self.assertAlmostEqual(cal.p90, round(quantile(res, 0.90), 2), places=9)

    def test_below_floor_returns_none(self):
        self.assertIsNone(residual_calibration([0.1] * (MIN_RESIDUALS - 1)))

class TestBucketProbabilities(unittest.TestCase):
    def _ladder(self):
        # Contiguous whole-°C ladder 16..20 with open tails, summing prices ~1.
        buckets = (
            MarketBucket("16°C or below", 0.10, 0.90, (), None, 16),
            MarketBucket("17°C", 0.20, 0.80, (), 17, 17),
            MarketBucket("18°C", 0.35, 0.65, (), 18, 18),
            MarketBucket("19°C", 0.25, 0.75, (), 19, 19),
            MarketBucket("20°C or above", 0.10, 0.90, (), 20, None),
        )
        return WeatherMarket(
            event_id="t", title="Test City high", city="Test", date_label="d",
            station=None, grain="C", precision="whole °C", resolution_source=None,
            end_date=None, slug=None, buckets=buckets,
        )

    def test_probabilities_are_a_valid_distribution(self):
        rng = random.Random(7)
        residuals = [rng.gauss(0.0, 1.2) for _ in range(80)]
        cmp = compare_high(self._ladder(), verdict_high_c=18.3, residuals_c=residuals)
        self.assertIsNotNone(cmp)
        probs = [b.model_prob for b in cmp.buckets]
        for p in probs:
            self.assertGreaterEqual(p, 0.0)
            self.assertLessEqual(p, 1.0)
        # Contiguous ladder with open tails: every dressed draw lands somewhere.
        self.assertAlmostEqual(sum(probs), 1.0, places=9)
        self.assertIsNotNone(cmp.calibration)

    def test_declines_below_residual_floor(self):
        self.assertIsNone(
            compare_high(self._ladder(), 18.0, [0.1] * (MIN_RESIDUALS - 1)))

class TestConditionalSpreadCalibration(unittest.TestCase):
    """The recommend-only conditional-spread check must accept real
    heteroscedastic signal, reject homoscedastic noise, stay leak-free, and never
    claim to change the served verdict."""

    def test_module_self_test(self):
        from weather_council.calibration import _self_test
        _self_test()

    def test_recommends_when_error_tracks_dispersion(self):
        from weather_council.calibration import conditional_spread_eval
        rng = random.Random(3)
        pairs = []
        for _ in range(400):
            disp = rng.uniform(0.5, 4.0)
            pairs.append((rng.gauss(0.0, disp), disp))   # error scales with dispersion
        ev = conditional_spread_eval(pairs)
        self.assertIsNotNone(ev)
        self.assertTrue(ev.recommend)
        self.assertGreater(ev.improvement, 0)
        self.assertGreaterEqual(ev.z, 2.0)

    def test_declines_on_homoscedastic_noise(self):
        from weather_council.calibration import conditional_spread_eval
        rng = random.Random(4)
        pairs = [(rng.gauss(0.0, 1.5), rng.uniform(0.5, 4.0)) for _ in range(400)]
        ev = conditional_spread_eval(pairs)
        self.assertIsNotNone(ev)
        self.assertFalse(ev.recommend)

    def test_thin_sample_returns_none(self):
        from weather_council.calibration import conditional_spread_eval
        rng = random.Random(5)
        pairs = [(rng.gauss(0, 1), rng.uniform(1, 3)) for _ in range(15)]
        self.assertIsNone(conditional_spread_eval(pairs))

    def test_leak_free_first_warmup_days_unscored(self):
        # With exactly warmup+min_scored pairs, only those past the warmup can be
        # scored — proving each day uses strictly-earlier pairs, never the future.
        from weather_council.calibration import conditional_spread_eval, WARMUP
        rng = random.Random(6)
        n = WARMUP + 25
        pairs = [(rng.gauss(0, 2), rng.uniform(0.5, 4)) for _ in range(n)]
        ev = conditional_spread_eval(pairs, min_scored=1)
        self.assertEqual(ev.n_scored, n - WARMUP)


class TestCoverageCalibration(unittest.TestCase):
    """The recommend-only constant-factor coverage check must: flag genuine
    under-dispersion and recommend widening only when it also improves CRPS; leave
    an already-calibrated cloud alone (factor ≈ 1, decline); stay leak-free; and
    never assert it changed the served verdict."""

    def test_recommends_widening_on_under_dispersed(self):
        from weather_council.calibration import coverage_calibration_eval
        rng = random.Random(3)
        # Variance trends up: a cloud from older (narrower) days under-covers today,
        # so widening must help on coverage AND CRPS, and be recommended.
        resid = [rng.gauss(0.0, 1.0 + 0.006 * i) for i in range(700)]
        ev = coverage_calibration_eval(resid)
        self.assertIsNotNone(ev)
        self.assertTrue(ev.recommend)
        self.assertGreater(ev.final_factor, 1.0)
        self.assertGreater(ev.improvement, 0)
        self.assertGreater(ev.coverage_calibrated, ev.coverage_incumbent)

    def test_declines_on_already_calibrated(self):
        from weather_council.calibration import coverage_calibration_eval
        rng = random.Random(5)
        resid = [rng.gauss(0.0, 1.5) for _ in range(700)]
        ev = coverage_calibration_eval(resid)
        self.assertIsNotNone(ev)
        self.assertFalse(ev.recommend)          # no scale deficit -> hold the cloud
        # A stationary Gaussian cloud should cover ~nominal out of sample.
        self.assertGreaterEqual(ev.coverage_incumbent, 0.76)
        self.assertLessEqual(ev.coverage_incumbent, 0.84)

    def test_thin_sample_returns_none(self):
        from weather_council.calibration import coverage_calibration_eval
        rng = random.Random(7)
        self.assertIsNone(coverage_calibration_eval(
            [rng.gauss(0, 1) for _ in range(15)]))

    def test_band_floor_leaves_early_days_unscored(self):
        # Days are scored only once the prior cloud reaches band_floor, proving the
        # factor is learned from well-estimated bands and strictly-earlier data.
        from weather_council.calibration import coverage_calibration_eval, BAND_FLOOR
        rng = random.Random(9)
        n = BAND_FLOOR + 40
        resid = [rng.gauss(0, 2) for _ in range(n)]
        ev = coverage_calibration_eval(resid, min_scored=1)
        self.assertEqual(ev.n_scored, n - BAND_FLOOR)


class TestGroupedCoverageCalibration(unittest.TestCase):
    """The council serves a SEPARATE residual cloud per attribute (compare_high uses
    residuals_high, compare_low uses residuals_low), so the coverage check must score
    each attribute against its OWN cloud and pool the per-day outcomes — never pool the
    raw high+low residuals into one mixture the council never emits."""

    def test_grouped_equals_n_weighted_per_stream(self):
        # The exact invariant: grouped incumbent coverage == the n-weighted average of
        # the two single-stream coverages, because each day is scored against its own
        # attribute's prior cloud. (Incumbent coverage is independent of the learned
        # factor, so this is an identity up to rounding.)
        from weather_council.calibration import (
            coverage_calibration_eval, coverage_calibration_eval_grouped)
        rng = random.Random(404)
        hi = [rng.gauss(+0.8, 1.1) for _ in range(500)]
        lo = [rng.gauss(-0.6, 0.6) for _ in range(500)]
        g = coverage_calibration_eval_grouped([hi, lo])
        e_hi = coverage_calibration_eval(hi)
        e_lo = coverage_calibration_eval(lo)
        self.assertEqual(g.n_scored, e_hi.n_scored + e_lo.n_scored)
        expected = (e_hi.coverage_incumbent * e_hi.n_scored
                    + e_lo.coverage_incumbent * e_lo.n_scored) / g.n_scored
        self.assertAlmostEqual(g.coverage_incumbent, expected, places=3)

    def test_grouped_diverges_from_pooled_raw(self):
        # Pooling raw high+low residuals measures a different object: with different
        # centres/spreads the mixed band reads a materially different coverage than the
        # faithful per-attribute scoring. This divergence is why grouped exists.
        from weather_council.calibration import (
            coverage_calibration_eval, coverage_calibration_eval_grouped)
        rng = random.Random(404)
        hi = [rng.gauss(+0.8, 1.1) for _ in range(500)]
        lo = [rng.gauss(-0.6, 0.6) for _ in range(500)]
        g = coverage_calibration_eval_grouped([hi, lo])
        pooled = [x for pair in zip(hi, lo) for x in pair]
        p = coverage_calibration_eval(pooled)
        self.assertGreater(abs(p.coverage_incumbent - g.coverage_incumbent), 5e-3)

    def test_grouped_declines_when_each_attribute_calibrated(self):
        from weather_council.calibration import coverage_calibration_eval_grouped
        rng = random.Random(404)
        hi = [rng.gauss(+0.8, 1.1) for _ in range(500)]
        lo = [rng.gauss(-0.6, 0.6) for _ in range(500)]
        g = coverage_calibration_eval_grouped([hi, lo])
        self.assertIsNotNone(g)
        self.assertFalse(g.recommend)
        self.assertGreaterEqual(g.coverage_incumbent, 0.76)
        self.assertLessEqual(g.coverage_incumbent, 0.84)

    def test_grouped_catches_real_per_attribute_deficit(self):
        # Both attributes built narrow-from-older-days (variance trending up): grouped
        # must surface the genuine under-coverage and recommend widening.
        from weather_council.calibration import coverage_calibration_eval_grouped
        rng = random.Random(405)
        hi = [rng.gauss(0.0, 1.0 + 0.006 * i) for i in range(700)]
        lo = [rng.gauss(0.0, 0.8 + 0.005 * i) for i in range(700)]
        g = coverage_calibration_eval_grouped([hi, lo])
        self.assertIsNotNone(g)
        self.assertTrue(g.recommend)
        self.assertGreater(g.final_factor, 1.0)
        self.assertGreater(g.improvement, 0)

    def test_grouped_thin_streams_return_none(self):
        from weather_council.calibration import coverage_calibration_eval_grouped
        rng = random.Random(7)
        short = [rng.gauss(0, 1) for _ in range(12)]
        self.assertIsNone(coverage_calibration_eval_grouped([short, short]))


if __name__ == "__main__":
    unittest.main()
