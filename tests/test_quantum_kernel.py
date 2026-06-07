"""Network-free tests for the quantum fidelity-kernel study (quantum_kernel.py).

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


class TestQuantumKernel(unittest.TestCase):
    """The quantum-inspired fidelity kernel must be a *genuine* quantum kernel
    (the squared overlap of product states), classically exact and stdlib-only —
    never a hand-wavy 'quantum-flavoured' function. Its backtested edge is
    measured elsewhere (tools/quantum_backtest.py); here we guard correctness."""

    def test_self_test_passes(self):
        from weather_council import quantum_kernel as qk
        qk._self_test()                      # raises on any failure

    def test_fidelity_equals_product_state_overlap_squared(self):
        import math
        from weather_council import quantum_kernel as qk
        rng = random.Random(7)
        for _ in range(50):
            x = [rng.uniform(-3, 3) for _ in range(4)]
            y = [rng.uniform(-3, 3) for _ in range(4)]
            amp = 1.0
            for tx, ty in zip(x, y):
                amp *= (math.cos(tx / 2) * math.cos(ty / 2)
                        + math.sin(tx / 2) * math.sin(ty / 2))
            self.assertAlmostEqual(qk.fidelity_kernel(x, y), amp * amp, places=10)
        # bounded in [0,1] and symmetric
        self.assertTrue(0.0 <= qk.fidelity_kernel([0.2], [1.3]) <= 1.0)
        self.assertAlmostEqual(qk.fidelity_kernel([0.2, 0.9], [1.3, -0.4]),
                               qk.fidelity_kernel([1.3, -0.4], [0.2, 0.9]), places=12)

    def test_kernel_ridge_solves_spd_system(self):
        from weather_council import quantum_kernel as qk
        A = [[4.0, 1.0], [1.0, 3.0]]
        sol = qk._chol_solve(qk._cholesky(A), [1.0, 2.0])
        self.assertAlmostEqual(A[0][0] * sol[0] + A[0][1] * sol[1], 1.0, places=9)
        self.assertAlmostEqual(A[1][0] * sol[0] + A[1][1] * sol[1], 2.0, places=9)

if __name__ == "__main__":
    unittest.main()
