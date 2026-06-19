"""Tests for the prospective EPS-ensemble accumulator (tools/ensemble_accumulate).

Mirrors tests/test_hko_intraday_accumulate.py: runs the tool's deterministic
self-test plus a few direct checks on the settlement-bucket summary, so the
recommend-only accumulation pipeline is covered by the same green-floor gate as
the rest of the project."""
import unittest

from tools.ensemble_accumulate import (
    _self_test, _summarize, _round_half_up, MIN_MEMBERS,
)


class TestEnsembleAccumulate(unittest.TestCase):
    def test_self_test_passes(self):
        # Deterministic oracle: fake Sources, exact pmf, idempotent re-capture,
        # honest validate status. Raises AssertionError on any drift.
        _self_test()

    def test_round_half_up_settlement(self):
        # Whole-°C round-half-up: the London/Manila settlement convention.
        self.assertEqual(_round_half_up(22.4), 22)
        self.assertEqual(_round_half_up(22.5), 23)
        self.assertEqual(_round_half_up(22.6), 23)
        self.assertEqual(_round_half_up(27.0), 27)

    def test_summarize_thin_returns_none(self):
        # Below the member floor the day's spread is untrustworthy -> no row.
        self.assertIsNone(_summarize([22.0] * (MIN_MEMBERS - 1)))

    def test_summarize_pmf_and_spread(self):
        s = _summarize([22.0] * 20 + [23.0] * 5)
        self.assertIsNotNone(s)
        self.assertEqual(s["n_members"], 25)
        self.assertEqual(s["modal_bucket"], 22)
        self.assertAlmostEqual(s["modal_prob"], 20 / 25)
        self.assertGreater(s["sd_high"], 0.0)  # a real (non-degenerate) spread


if __name__ == "__main__":
    unittest.main()
