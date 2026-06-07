"""Network-free tests for the cross-timescale verdict (timescale.py) and the regime-consensus summary.

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


class TestTimescaleVerdict(unittest.TestCase):
    """The cross-timescale verdict (weather_council/timescale.py) must be a fixed,
    parameter-free function of the data: skillful series read SKILL CONFIRMED,
    i.i.d. noise never does, sub-cadence scales are refused (UNOBSERVABLE), and the
    Diebold-Mariano significance is HAC-valid. No human judgement may enter it."""

    def test_self_test_passes(self):
        from weather_council import timescale as tk
        tk._self_test()

    def test_ar1_is_skill_confirmed_noise_is_not(self):
        from weather_council import timescale as tk
        rng = random.Random(3)
        ar = [0.0]
        for _ in range(500):
            ar.append(0.85 * ar[-1] + rng.gauss(0, 1))
        self.assertEqual(tk.evaluate(ar, "ar1").verdict, "SKILL CONFIRMED")
        noise = [rng.gauss(0, 1) for _ in range(500)]
        self.assertNotEqual(tk.evaluate(noise, "noise").verdict, "SKILL CONFIRMED")

    def test_observability_gate_refuses_subcadence(self):
        from weather_council import timescale as tk
        v = tk.evaluate([1.0, 2.0, 3.0] * 30, "sub", observability=0.02)
        self.assertEqual(v.verdict, "UNOBSERVABLE")

    def test_insufficient_below_min_periods(self):
        from weather_council import timescale as tk
        v = tk.evaluate([1.0, 2.0, 3.0, 4.0, 5.0], "tiny", observability=1.0)
        self.assertEqual(v.verdict, "INSUFFICIENT")

    def test_resample_observability_and_binning(self):
        from weather_council import timescale as tk
        # Two points 1s apart, binned at 10s -> one filled bin, obs=1.0.
        series, obs = tk.resample([(0.0, 4.0), (1.0, 6.0)], 10.0)
        self.assertEqual(series, [5.0])
        self.assertEqual(obs, 1.0)
        # Points 0s and 100s, binned at 10s -> 2 filled of 11 spanned -> obs ~0.18.
        series, obs = tk.resample([(0.0, 1.0), (100.0, 2.0)], 10.0)
        self.assertEqual(series, [1.0, 2.0])
        self.assertAlmostEqual(obs, 2 / 11, places=6)

    def test_dm_symmetric_for_identical_forecasters(self):
        from weather_council import timescale as tk
        dm, p = tk.diebold_mariano([1.0, 2.0, 1.5, 3.0], [1.0, 2.0, 1.5, 3.0])
        self.assertAlmostEqual(dm, 0.0, places=9)
        self.assertGreater(p, 0.99)

class TestRegimeConsensus(unittest.TestCase):
    """regime_consensus is a pure post-hoc summary of a finished Verdict: it
    classifies the regime from already-computed signals and measures whether the
    independent estimators reach a matched verdict. It must never depend on I/O
    and never imply a change to the headline number."""

    def _verdict(self, *, high=30.0, low=20.0, naive_high=None, naive_low=None,
                 mean_high=None, mean_low=None, blend_eligible=False,
                 backtest_days=0, eff=2.0, gap=None, repr_sigma=None, test_days=40):
        from types import SimpleNamespace as NS
        en = NS(mean_high=mean_high, mean_low=mean_low,
                blend_eligible=blend_eligible, backtest_days=backtest_days)
        return NS(high=high, low=low, naive_high=naive_high, naive_low=naive_low,
                  ensemble=en, validation=NS(test_days=test_days),
                  confidence_detail={"effective_uncertainty": eff,
                                     "season_gap_days": gap,
                                     "representativeness_sigma": repr_sigma})

    def test_matched_when_estimators_within_one_sigma(self):
        from weather_council.council import regime_consensus
        v = self._verdict(high=30.0, low=20.0, naive_high=30.5, naive_low=20.4,
                          mean_high=29.6, mean_low=19.8, eff=2.0)
        rc = regime_consensus(v)
        self.assertEqual(rc["consensus"]["status"], "matched")
        self.assertLessEqual(rc["consensus"]["worst_ratio"], 1.0)

    def test_split_when_an_estimator_diverges_beyond_threshold(self):
        from weather_council.council import regime_consensus
        v = self._verdict(high=30.0, low=20.0, naive_high=30.2, naive_low=20.1,
                          mean_high=34.0, mean_low=20.0, eff=2.0)  # 2.0σ on high
        rc = regime_consensus(v)
        self.assertEqual(rc["consensus"]["status"], "split")
        self.assertEqual(rc["consensus"]["worst_axis"], "high")

    def test_out_of_season_regime_and_trusted_validation(self):
        from weather_council.council import regime_consensus
        v = self._verdict(gap=39, naive_high=30.1, mean_high=30.0, naive_low=20.0, mean_low=20.0)
        rc = regime_consensus(v)
        self.assertEqual(rc["regime"]["season"], "out-of-season")
        self.assertTrue(any("trailing-window hit-rate" in t.lower()
                            for t in rc["trusted_validation"]))

    def test_benign_regime_reports_face_value(self):
        from weather_council.council import regime_consensus
        v = self._verdict(gap=0, eff=1.0, repr_sigma=0.2, blend_eligible=True,
                          backtest_days=30, naive_high=30.1, mean_high=30.0,
                          naive_low=20.0, mean_low=20.0)
        rc = regime_consensus(v)
        self.assertEqual(rc["regime"]["volatility"], "calm")
        self.assertEqual(rc["regime"]["spatial"], "flat")
        self.assertEqual(rc["regime"]["data"], "rich")
        self.assertTrue(any("face value" in t for t in rc["trusted_validation"]))

    def test_sigma_floor_used_when_effective_unavailable(self):
        from weather_council.council import regime_consensus
        v = self._verdict(eff=None, naive_high=30.5, mean_high=30.0,
                          naive_low=20.0, mean_low=20.0)
        rc = regime_consensus(v)
        self.assertFalse(rc["consensus"]["scaled_by_effective_sigma"])
        self.assertEqual(rc["consensus"]["sigma_used"], 1.0)

if __name__ == "__main__":
    unittest.main()
