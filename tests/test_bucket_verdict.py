"""Network-free tests for the bucket-verdict simulation: the leak-free
whole-degree settlement-bucket hit-rate, directional off-by-one bias, and
edge-distance fragility — the object the market actually pays on.

Stdlib unittest only. Run with:
    PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import random
import unittest

from weather_council.bucket_verdict import (
    bucket_verdict_eval, bucket_verdict_eval_grouped, _edge_distance,
    _modal_bucket, WARMUP, MIN_SCORED, _self_test,
)
from weather_council.sources import _round_half_up


class TestBucketVerdictModule(unittest.TestCase):
    def test_module_self_test(self):
        _self_test()

    def test_edge_distance_boundary_vs_centre(self):
        # On a settlement boundary (k+0.5) edge-distance is 0; at a bucket centre
        # (integer) it is 0.5; halfway is 0.25.
        self.assertAlmostEqual(_edge_distance(18.5), 0.0, places=9)
        self.assertAlmostEqual(_edge_distance(18.0), 0.5, places=9)
        self.assertAlmostEqual(_edge_distance(18.25), 0.25, places=9)
        self.assertAlmostEqual(_edge_distance(17.75), 0.25, places=9)

    def test_modal_bucket_matches_settlement_rounding(self):
        # A tight cloud centred on zero leaves the modal bucket equal to the
        # point's own settlement integer (round-half-up).
        cloud = [0.0, 0.01, -0.01, 0.02, -0.02] * 4
        self.assertEqual(_modal_bucket(18.2, cloud), _round_half_up(18.2))
        self.assertEqual(_modal_bucket(18.5, cloud), _round_half_up(18.5))  # 19, half up


class TestBucketHitRate(unittest.TestCase):
    def test_centred_sharp_cloud_names_buckets(self):
        rng = random.Random(1)
        pairs = [(float(c := rng.randint(10, 30)), c + rng.gauss(0.0, 0.12))
                 for _ in range(400)]
        ev = bucket_verdict_eval(pairs)
        self.assertIsNotNone(ev)
        self.assertGreater(ev.hit_rate, 0.95)
        self.assertLess(abs(ev.signed_bias), 0.05)

    def test_constant_bias_is_corrected_by_cloud(self):
        # A stationary point bias is absorbed by the residual cloud: the modal
        # bucket recovers the truth even though the bare point is off-by-one.
        rng = random.Random(2)
        pairs = [(r - 0.7, float(r)) for r in (rng.randint(10, 30) for _ in range(400))]
        ev = bucket_verdict_eval(pairs)
        self.assertIsNotNone(ev)
        self.assertGreater(ev.hit_rate, 0.95)
        self.assertLess(ev.point_hit_rate, 0.05)
        self.assertLess(abs(ev.signed_bias), 0.05)

    def test_drift_is_not_corrected_directional_miss(self):
        # A drifting bias outruns the trailing cloud -> directional cool miss.
        rng = random.Random(22)
        pairs = []
        for i in range(500):
            realized = rng.randint(10, 30)
            pairs.append((realized - 0.02 * i, float(realized)))
        ev = bucket_verdict_eval(pairs)
        self.assertIsNotNone(ev)
        self.assertLess(ev.signed_bias, -0.3)
        self.assertGreater(ev.frac_under, ev.frac_over)
        self.assertLess(ev.hit_rate, 0.7)

    def test_boundary_pinned_misses_are_fragile(self):
        rng = random.Random(3)
        pairs = []
        for _ in range(2000):
            base = rng.randint(10, 30)
            pred = base + rng.uniform(-0.5, 0.5)
            pairs.append((pred, pred + rng.gauss(0.0, 0.25)))
        ev = bucket_verdict_eval(pairs)
        self.assertIsNotNone(ev)
        self.assertGreater(ev.fragility, 0.05)
        self.assertGreater(ev.mean_edge_hit, ev.mean_edge_miss)


class TestBucketLeakFreeAndFloors(unittest.TestCase):
    def test_leak_free_warmup_unscored(self):
        rng = random.Random(4)
        n = WARMUP + MIN_SCORED
        pairs = [(float(rng.randint(10, 30)), float(rng.randint(10, 30)))
                 for _ in range(n)]
        ev = bucket_verdict_eval(pairs, min_scored=1)
        self.assertEqual(ev.n_scored, n - WARMUP)

    def test_thin_sample_returns_none(self):
        self.assertIsNone(
            bucket_verdict_eval([(20.0, 20.0)] * (WARMUP + MIN_SCORED - 1)))


class TestBucketGrouped(unittest.TestCase):
    def test_grouped_equals_n_weighted_per_stream(self):
        # Each stream scored against its OWN cloud, then pooled: the grouped
        # hit-rate is exactly the n-weighted average of the single-stream rates.
        rng = random.Random(5)
        hi = [(float(rng.randint(20, 34)), float(rng.randint(20, 34))) for _ in range(300)]
        lo = [(float(rng.randint(5, 18)), float(rng.randint(5, 18))) for _ in range(300)]
        g = bucket_verdict_eval_grouped([hi, lo])
        e_hi = bucket_verdict_eval(hi)
        e_lo = bucket_verdict_eval(lo)
        self.assertEqual(g.n_scored, e_hi.n_scored + e_lo.n_scored)
        expected = (e_hi.hit_rate * e_hi.n_scored
                    + e_lo.hit_rate * e_lo.n_scored) / g.n_scored
        self.assertAlmostEqual(g.hit_rate, expected, places=9)

    def test_grouped_thin_streams_return_none(self):
        short = [(20.0, 20.0)] * 12
        self.assertIsNone(bucket_verdict_eval_grouped([short, short]))


if __name__ == "__main__":
    unittest.main()
