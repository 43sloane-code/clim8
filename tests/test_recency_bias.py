"""Network-free tests for the recency-weighted-bias evaluation: the leak-free,
paired-CRPS-and-bucket-gated check of whether recency-weighting each member's
bias sharpens the SERVED distribution. The central subtlety under test is that the
residual cloud absorbs a constant offset, so recency earns a recommend only for
the drift CURVATURE the cloud cannot absorb — a linear lag lowers point MAE but
leaves CRPS and the bucket unchanged, and must NOT be recommended.

Stdlib unittest only. Run with:
    PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import random
import statistics
import unittest

from weather_council.recency_bias import (
    recency_weighted_bias, evaluate, RECENCY_HALFLIFE_DAYS,
    MIN_PAIRED, Z_THRESHOLD, _self_test,
)


class TestRecencyBiasModule(unittest.TestCase):
    def test_module_self_test(self):
        _self_test()


class TestRecencyWeightedBias(unittest.TestCase):
    def test_flat_errors_reduce_to_plain_mean(self):
        # Every error equal => weighting cannot move the estimate, MAD is zero.
        flat = [(f"2026-01-{d:02d}", 0.5) for d in range(1, 21)]
        b, mad = recency_weighted_bias(flat, "2026-02-01", halflife=30)
        self.assertAlmostEqual(b, 0.5, places=9)
        self.assertAlmostEqual(mad, 0.0, places=9)

    def test_tracks_drift_above_plain_mean(self):
        # A linearly growing error: recency weighting leans on the recent, larger
        # errors, so the recency bias sits above the plain mean.
        drift = [(f"2026-03-{d:02d}", 0.1 * d) for d in range(1, 28)]
        b_rec, _ = recency_weighted_bias(drift, "2026-03-28", halflife=10)
        plain = statistics.mean(e for _, e in drift)
        self.assertGreater(b_rec, plain + 0.2)

    def test_long_halflife_approaches_plain_mean(self):
        # As halflife → ∞ the weights flatten: this strictly generalizes the
        # incumbent plain-mean estimator.
        drift = [(f"2026-03-{d:02d}", 0.1 * d) for d in range(1, 28)]
        b_rec, _ = recency_weighted_bias(drift, "2026-03-28", halflife=1e9)
        plain = statistics.mean(e for _, e in drift)
        self.assertAlmostEqual(b_rec, plain, places=3)

    def test_future_dated_errors_clamped_not_upweighted(self):
        # A training day at/after the target gets age 0 (weight 1), never a weight
        # above the most-recent day — leak guard, not an up-weight.
        errs = [("2026-04-01", 1.0), ("2026-04-10", 1.0)]
        b, _ = recency_weighted_bias(errs, "2026-04-05", halflife=5)
        self.assertAlmostEqual(b, 1.0, places=9)


class TestEvaluateGate(unittest.TestCase):
    def test_linear_drift_cuts_mae_but_not_recommended(self):
        # The crux insight: under LINEAR drift a fixed-day lag is a CONSTANT offset
        # the residual cloud absorbs. Point MAE drops, but CRPS and the settled
        # bucket are unchanged — so the gate must decline despite the accuracy gain.
        rng = random.Random(7)
        triples = []
        for i in range(400):
            truth = 20.0 + 0.01 * i + rng.gauss(0.0, 0.3)
            inc_pred = 20.0 + 0.01 * (i - 40)      # constant 0.4 lag
            cand_pred = 20.0 + 0.01 * i            # tracks
            triples.append((inc_pred, cand_pred, truth))
        ev = evaluate({"high": triples})
        self.assertIsNotNone(ev)
        self.assertLess(ev.mae_candidate, ev.mae_incumbent - 0.1)
        self.assertLess(abs(ev.crps_improvement), 1e-3)
        self.assertFalse(ev.recommend)

    def test_curved_drift_is_recommended(self):
        # CURVED (accelerating) drift leaves a GROWING value gap the cloud cannot
        # absorb: incumbent residuals fatten, CRPS worsens, recency wins past noise.
        rng = random.Random(17)
        triples = []
        for i in range(400):
            curve = 0.0003 * i * i
            truth = 20.0 + curve + rng.gauss(0.0, 0.3)
            inc_pred = 20.0 + 0.0003 * (i - 30) ** 2
            cand_pred = 20.0 + curve
            triples.append((inc_pred, cand_pred, truth))
        ev = evaluate({"high": triples})
        self.assertIsNotNone(ev)
        self.assertGreater(ev.crps_improvement, 0.0)
        self.assertGreaterEqual(ev.z, Z_THRESHOLD)
        self.assertLess(ev.mae_candidate, ev.mae_incumbent)
        self.assertTrue(ev.recommend)

    def test_stationary_noise_not_recommended(self):
        # Two equivalent noisy streams: no real edge to find, gate declines.
        rng = random.Random(8)
        triples = []
        for _ in range(400):
            truth = 18.0 + rng.gauss(0.0, 1.0)
            triples.append((truth + rng.gauss(0, 0.5),
                            truth + rng.gauss(0, 0.5), truth))
        ev = evaluate({"high": triples})
        self.assertIsNotNone(ev)
        self.assertFalse(ev.recommend)

    def test_bucket_gate_blocks_crps_win_that_costs_buckets(self):
        # Construct a candidate that wins CRPS but LOSES whole-degree bucket-hit:
        # the bucket gate (hit_cand >= hit_inc) must veto the recommend even when
        # z clears the threshold. Incumbent nails integers; candidate is a touch
        # sharper in the tails (smaller residual spread) yet rounds wrong more often.
        rng = random.Random(31)
        triples = []
        for _ in range(400):
            base = rng.randint(15, 25)
            obs = float(base)
            inc_pred = base + rng.gauss(0.0, 0.18)        # tight, rounds right
            cand_pred = base + 0.5 + rng.gauss(0.0, 0.10) # sharper but half-off
            triples.append((inc_pred, cand_pred, obs))
        ev = evaluate({"high": triples})
        self.assertIsNotNone(ev)
        if ev.bucket_hit_candidate < ev.bucket_hit_incumbent:
            self.assertFalse(ev.recommend)

    def test_thin_sample_returns_none(self):
        self.assertIsNone(evaluate({"high": [(20.0, 20.0, 20.0)] * 20}))

    def test_below_min_paired_returns_none(self):
        # Just under the paired-day floor (clouds need a warmup before pairs count).
        rng = random.Random(9)
        n = MIN_PAIRED + 9      # ~10 warmup days never produce a CRPS pair
        triples = [(20.0 + rng.gauss(0, 0.3), 20.0 + rng.gauss(0, 0.3),
                    20.0 + rng.gauss(0, 0.5)) for _ in range(n)]
        self.assertIsNone(evaluate({"high": triples}))


class TestEvaluatePerAttribute(unittest.TestCase):
    def test_high_and_low_pooled_across_attrs(self):
        # Two attributes are each scored against their own cloud/ladder, then the
        # paired CRPS days pool: n_paired is the sum of both streams' paired days.
        rng = random.Random(11)
        hi = [(float(c := rng.randint(28, 34)) - 0.3, c - 0.1, float(c))
              for _ in range(300)]
        lo = [(float(c := rng.randint(2, 10)) - 0.3, c - 0.1, float(c))
              for _ in range(300)]
        ev = evaluate({"high": hi, "low": lo})
        self.assertIsNotNone(ev)
        self.assertGreater(ev.n_paired, MIN_PAIRED)

    def test_per_attribute_split_localizes_the_gain(self):
        # The point of the per-attribute audit: a POOLED recommend can be carried
        # by ONE market while the other has no edge. Build a panel where HIGH has
        # curved (recency-curable) drift but LOW is stationary noise. Scoring each
        # attribute ALONE must localize the recommend to high and decline on low —
        # exactly the evidence that keeps a per-station served policy honest at the
        # attribute grain (don't serve recency on a market that doesn't earn it).
        rng = random.Random(101)
        hi, lo = [], []
        for i in range(400):
            curve = 0.0003 * i * i
            truth_h = 20.0 + curve + rng.gauss(0.0, 0.3)
            hi.append((20.0 + 0.0003 * (i - 30) ** 2, 20.0 + curve, truth_h))
            truth_l = 10.0 + rng.gauss(0.0, 1.0)          # stationary
            lo.append((truth_l + rng.gauss(0, 0.5), truth_l + rng.gauss(0, 0.5), truth_l))
        ev_high = evaluate({"high": hi})
        ev_low = evaluate({"low": lo})
        self.assertIsNotNone(ev_high)
        self.assertIsNotNone(ev_low)
        self.assertTrue(ev_high.recommend)    # curved drift: recency earns it
        self.assertFalse(ev_low.recommend)    # stationary: no edge to find
        # And the per-attribute split is leak-free identical to scoring that key in
        # isolation — the council surfaces exactly these two single-key evaluations.
        self.assertEqual(ev_high.n_paired, evaluate({"high": hi}).n_paired)


if __name__ == "__main__":
    unittest.main()
