"""Network-free tests for the probabilistic scoring layer (scoring.py): CRPS, interval coverage, PIT, quantiles.

Stdlib unittest only. Run with:
    PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import random
import statistics as st
import unittest

from weather_council import scoring
from weather_council.scoring import crps_sample, crps_gaussian, quantile, pit


class TestScoring(unittest.TestCase):
    def test_module_self_test(self):
        # The module ships its own correctness oracle; make the suite re-run it.
        scoring._self_test()

    def test_point_forecast_is_absolute_error(self):
        self.assertEqual(crps_sample([7.3], 4.1), abs(7.3 - 4.1))

    def test_fast_equals_bruteforce(self):
        rng = random.Random(1)
        for _ in range(200):
            n = rng.randint(2, 30)
            s = [rng.gauss(0, 4) for _ in range(n)]
            y = rng.gauss(0, 4)
            mad = sum(abs(x - y) for x in s) / n
            brute = mad - 0.5 * sum(abs(a - b) for a in s for b in s) / (n * n)
            self.assertAlmostEqual(crps_sample(s, y, fair=False), brute, places=9)

    def test_energy_form_matches_gaussian(self):
        rng = random.Random(2)
        mu, sig = 10.0, 2.5
        samp = [rng.gauss(mu, sig) for _ in range(120_000)]
        for y in (5.0, 8.0, 10.0, 12.0, 15.0):
            self.assertLess(abs(crps_sample(samp, y) - crps_gaussian(mu, sig, y)), 0.03)

    def test_pit_uniform_for_calibrated_forecast(self):
        rng = random.Random(3)
        samp = [rng.gauss(0, 1) for _ in range(3000)]
        vals = [pit(samp, rng.gauss(0, 1)) for _ in range(3000)]
        self.assertAlmostEqual(st.mean(vals), 0.5, delta=0.03)

    def test_quantile_interpolates(self):
        self.assertAlmostEqual(quantile([0.0, 10.0], 0.5), 5.0)
        self.assertEqual(quantile([4.2], 0.9), 4.2)
        xs = [3.0, 1.0, 2.0, 5.0, 4.0]
        self.assertLessEqual(quantile(xs, 0.1), quantile(xs, 0.9))

if __name__ == "__main__":
    unittest.main()
