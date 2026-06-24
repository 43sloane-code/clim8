"""Tests for the realized paper-P&L instrument (tools/paper_pnl.simulate).

Verifies the money logic deterministically: a thin-price value bet wins the right
amount, a wrong bet loses 1 unit, an untradeable (priced-~0) bucket is skipped,
and the robust floor drops thin-price wins."""
import unittest

from tools.paper_pnl import simulate


def _days():
    return [
        # d1: thin-price WIN — model 0.6 > mkt 0.08 on 31C, settles 31C -> +(1-.08)/.08 = +11.5
        {"settled": "31C", "buckets": {"31C": (0.6, 0.08), "32C": (0.4, 0.85)}},
        # d2: normal-price LOSS — model bets 31C @0.40, settles 32C -> -1
        {"settled": "32C", "buckets": {"31C": (0.6, 0.40), "32C": (0.4, 0.50)}},
        # d3: UNTRADEABLE — model modal 33C priced 0.00 (no quote) -> skipped
        {"settled": "33C", "buckets": {"33C": (0.7, 0.00), "34C": (0.3, 0.95)}},
    ]


class TestPaperPnl(unittest.TestCase):
    def test_value_bet_pnl_and_liquidity_floor(self):
        r = simulate(_days(), floor=0.05)
        self.assertEqual(r["scored"], 3)
        self.assertEqual(r["bets"], 2)
        self.assertEqual(r["skipped_no_liq"], 1)        # d3 untradeable
        self.assertAlmostEqual(r["model_pnl"], 10.5)    # +11.5 (d1) -1 (d2)

    def test_robust_floor_drops_thin_price_wins(self):
        r = simulate(_days(), floor=0.15)               # 0.08 now untradeable
        self.assertEqual(r["bets"], 1)                  # only d2 survives
        self.assertAlmostEqual(r["model_pnl"], -1.0)    # the thin win is gated out

    def test_hit_rate_and_brier(self):
        r = simulate(_days(), floor=0.05)
        self.assertAlmostEqual(r["model_hit_rate"], 2 / 3)   # d1, d3 named correctly
        self.assertAlmostEqual(r["market_hit_rate"], 1 / 3)  # d2 only
        self.assertLess(r["model_brier"], 3.0)


if __name__ == "__main__":
    unittest.main()
