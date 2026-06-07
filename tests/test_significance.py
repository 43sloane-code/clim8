"""Network-free tests for the paired-bootstrap significance gate that the daily
health check uses to decide whether a challenger constant is signal or noise.

Why this exists: the health check previously surfaced a CONSIDER recommendation
whenever a challenger's basket-mean MAE beat current by a FIXED 0.03 °C — an
assertion, never a test. That bare threshold is biased downward (it picks the
best of four variants) and ignores how much the "win" rides on which cities were
in season. These tests pin the replacement honest:

  * deterministic — identical per-city deltas yield an identical CI, so the same
    data always produces the same recommendation (seeded bootstrap);
  * a genuinely separated improvement yields a 90% CI strictly ABOVE 0;
  * a noisy wash (mean ≈ 0, large spread) yields a CI that STRADDLES 0, so the
    gate refuses to call it signal;
  * the deltas are paired on the SAME cities (a challenger is never credited for
    an easier city set);
  * degenerate inputs (empty / single city) degrade honestly, never crash.

Loaded by file path because tools/ is intentionally not an importable package.

Run with:  PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_HC_PATH = Path(__file__).resolve().parent.parent / "tools" / "daily_healthcheck.py"
_spec = importlib.util.spec_from_file_location("daily_healthcheck", _HC_PATH)
hc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hc)


class TestPairedSignificanceGate(unittest.TestCase):

    def test_deltas_pair_on_shared_cities_only(self):
        cur = {"London": 0.90, "Tokyo": 0.80, "Cairo": 0.70}
        chal = {"London": 0.80, "Tokyo": 0.75}        # Cairo missing for challenger
        deltas = hc._paired_city_deltas(cur, chal)
        # Only the two shared cities are paired; Cairo is dropped, not imputed.
        self.assertEqual(sorted(round(d, 2) for d in deltas), [0.05, 0.10])

    def test_bootstrap_is_deterministic(self):
        deltas = [0.04, 0.06, 0.05, 0.03, 0.07, 0.05, 0.04, 0.06]
        a = hc._paired_bootstrap_ci(deltas)
        b = hc._paired_bootstrap_ci(deltas)
        self.assertEqual(a, b)                         # same data -> same verdict

    def test_clear_improvement_ci_excludes_zero(self):
        # Every city improves by ~0.05 with little spread -> CI well above 0.
        deltas = [0.05, 0.06, 0.04, 0.05, 0.06, 0.05, 0.04, 0.05]
        point, lo, hi, n = hc._paired_bootstrap_ci(deltas)
        self.assertEqual(n, 8)
        self.assertGreater(lo, 0.0)                    # significant: excludes 0
        self.assertAlmostEqual(point, sum(deltas) / len(deltas), places=9)

    def test_noisy_wash_ci_straddles_zero(self):
        # Mean ~0 but large city-to-city spread -> the gate must NOT call it signal.
        deltas = [0.40, -0.38, 0.35, -0.42, 0.30, -0.33, 0.05, -0.02]
        point, lo, hi, n = hc._paired_bootstrap_ci(deltas)
        self.assertLess(lo, 0.0)
        self.assertGreater(hi, 0.0)                    # CI straddles 0 -> noise

    def test_one_city_cannot_be_bounded(self):
        point, lo, hi, n = hc._paired_bootstrap_ci([0.05])
        self.assertEqual((point, lo, hi, n), (0.05, None, None, 1))

    def test_empty_is_honest_not_a_crash(self):
        self.assertEqual(hc._paired_bootstrap_ci([]), (None, None, None, 0))


class TestAccuracyPrecisionDecomposition(unittest.TestCase):
    """The per-city error is split into accuracy (bias) and precision (σ) so a
    city is diagnosed by which axis dominates — the call MAE alone can't make."""

    def test_rmse_identity_holds(self):
        # RMSE² must equal bias² + σ² exactly (the whole point of the split).
        rmse, _bfrac, _diag = hc._accuracy_precision(0.6, 0.8)
        self.assertAlmostEqual(rmse, 1.0, places=12)   # 0.36 + 0.64 = 1.0

    def test_bias_dominated_is_accuracy_limited(self):
        # Large systematic offset, tiny scatter -> centred-but-wrong (the
        # "confidently wrong" quadrant): blame accuracy, not precision.
        _rmse, bfrac, diag = hc._accuracy_precision(1.2, 0.2)
        self.assertGreater(bfrac, 0.5)
        self.assertIn("accuracy-limited", diag)

    def test_spread_dominated_is_precision_limited(self):
        # Near-centred but scattered -> blame precision (dispersion), not bias.
        _rmse, bfrac, diag = hc._accuracy_precision(0.1, 1.1)
        self.assertLess(bfrac, 0.5)
        self.assertIn("precision-limited", diag)

    def test_sign_of_bias_is_irrelevant_to_the_split(self):
        # A cold bias and an equal warm bias must read the same fraction; the
        # split is about magnitude of systematic error, not its direction.
        _r1, f_cold, _d1 = hc._accuracy_precision(-0.9, 0.4)
        _r2, f_warm, _d2 = hc._accuracy_precision(+0.9, 0.4)
        self.assertAlmostEqual(f_cold, f_warm, places=12)


if __name__ == "__main__":
    unittest.main()
