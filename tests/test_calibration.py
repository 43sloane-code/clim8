"""Network-free tests for the calibration/compare layer: the unified residual-calibration estimator, empirical bucket probabilities, and conditional-spread calibration.

Stdlib unittest only. Run with:
    PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import datetime as dt
import math
import random
import statistics as st
import unittest

from weather_council import scoring
from weather_council.scoring import crps_sample, crps_gaussian, interval_coverage, quantile, pit
from weather_council.compare import residual_calibration, compare_high, MIN_RESIDUALS
from weather_council.market import WeatherMarket, MarketBucket
from weather_council.agents import Vote, MemberSpec, Skill
from weather_council.council import Council


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

if __name__ == "__main__":
    unittest.main()
