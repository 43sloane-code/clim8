"""Network-free tests for run._bucket_call — the unified bucket verdict that reports
conviction IN THE BUCKET (not ±2°C) and prefers the intraday lever when it is
sharpened and confident. This is the fix for "it said Confidence: HIGH and missed":
a day-ahead coin-flip now reads LOW.

Run with:  PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import types
import unittest

from run import _bucket_call


def _v(label, high, resid):
    return types.SimpleNamespace(
        place=types.SimpleNamespace(label=lambda l=label: l),
        high=high,
        validation=types.SimpleNamespace(residuals_high=resid))


def _ceiling(modal_bucket, modal_prob, sharpened=True):
    pmf = ((modal_bucket, modal_prob), (modal_bucket - 1, round(1 - modal_prob, 4)))
    return types.SimpleNamespace(
        is_sharpened=sharpened, modal_bucket=modal_bucket, modal_prob=modal_prob,
        pmf=pmf, running_max_c=25.4, hour=15)


# A spread residual cloud around a boundary -> day-ahead is a coin-flip (LOW).
_SPREAD = [-0.7, -0.4, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3, 0.4, 0.6, 0.8, 1.1]


class TestBucketCall(unittest.TestCase):
    def test_day_ahead_coinflip_reads_low_not_high(self):
        c = _bucket_call(_v("London, United Kingdom", 24.5, _SPREAD), ceiling=None)
        self.assertFalse(c["used_intraday"])
        self.assertIsNotNone(c["prob"])
        self.assertLess(c["prob"], 0.70)            # a spread cloud is not HIGH
        self.assertIn(c["tier"], ("LOW", "MODERATE"))
        self.assertEqual(c["rule"], "round-half-up / whole °C")

    def test_span_is_high_conviction_when_single_bucket_is_not(self):
        c = _bucket_call(_v("London, United Kingdom", 24.5, _SPREAD), ceiling=None)
        # a single bucket is a coin-flip, but the span clears the high bar
        self.assertGreaterEqual(c["span_prob"], 0.80)
        self.assertGreaterEqual(len(c["span"]), 2)
        self.assertGreater(c["span_prob"], c["prob"])

    def test_confident_intraday_span_is_single_bucket(self):
        c = _bucket_call(_v("London, United Kingdom", 24.5, _SPREAD), ceiling=_ceiling(26, 0.92))
        self.assertTrue(c["used_intraday"])
        self.assertEqual(c["span"], [26])           # σ collapsed -> one confident bucket
        self.assertGreaterEqual(c["span_prob"], 0.80)

    def test_confident_intraday_overrides_day_ahead(self):
        v = _v("London, United Kingdom", 24.5, _SPREAD)
        c = _bucket_call(v, ceiling=_ceiling(26, 0.92))
        self.assertTrue(c["used_intraday"])
        self.assertEqual(c["bucket"], 26)
        self.assertEqual(c["tier"], "HIGH")
        self.assertIn("intraday", c["source"])

    def test_weak_intraday_falls_back_to_day_ahead(self):
        v = _v("London, United Kingdom", 24.5, _SPREAD)
        c = _bucket_call(v, ceiling=_ceiling(26, 0.40))
        self.assertFalse(c["used_intraday"])        # 0.40 < 0.70 bar
        self.assertEqual(c["source"], "day-ahead distribution")

    def test_hong_kong_uses_floor_rule(self):
        # 28.2 with a tight cloud floors to 28 (HK sub-degree), never round-to-28.5 etc.
        c = _bucket_call(_v("Hong Kong, HK", 28.2, [0.0] * 12 + [0.1, -0.1]), ceiling=None)
        self.assertEqual(c["rule"], "floor / 0.1°C")
        self.assertEqual(c["bucket"], 28)

    def test_thin_history_has_no_conviction(self):
        c = _bucket_call(_v("London, United Kingdom", 24.5, [0.1, -0.1, 0.0]), ceiling=None)
        self.assertIsNone(c["prob"])
        self.assertEqual(c["bucket"], 25)           # round(24.5) fallback
        self.assertEqual(c["tier"], "LOW")


if __name__ == "__main__":
    unittest.main()
