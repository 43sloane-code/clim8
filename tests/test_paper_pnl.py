"""Tests for the realized paper-P&L instrument (tools/paper_pnl).

Verifies the money logic deterministically: the LEGACY mid-view (simulate) — a thin-price value
bet wins the right amount, a wrong bet loses 1 unit, an untradeable (priced-~0) bucket is skipped,
the robust floor drops thin-price wins — AND that it is bit-identical after the executable layer
was added (it must ignore exec_books). Plus the EXECUTABLE view (simulate_executable, Phase 5):
walking a real book gives win/loss at q_exec, classifies a too-thin book UNTRADEABLE-EXEC and a
missing book NO-BOOK."""
import unittest

from tools.paper_pnl import simulate, simulate_executable
from weather_council.clob_book import parse_book


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

    def test_legacy_mid_view_ignores_exec_books_bit_identical(self):
        # Adding exec_books (Phase 5) must NOT change a single legacy number.
        plain = simulate(_days(), floor=0.05)
        enriched = [{**d, "exec_books": {"31C": parse_book(
            {"asset_id": "X", "asks": [{"price": "0.5", "size": "100"}]})}}
            for d in _days()]
        self.assertEqual(simulate(enriched, floor=0.05), plain)


class TestExecutablePnl(unittest.TestCase):
    def setUp(self):
        # $1 buys 2 shares at 0.50 (deep); the thin book holds only $0.50 of asks.
        self.deep = parse_book({"asset_id": "A", "bids": [{"price": "0.30", "size": "50"}],
                                "asks": [{"price": "0.50", "size": "100"}]})
        self.thin = parse_book({"asset_id": "B", "asks": [{"price": "0.50", "size": "1"}]})

    def test_executable_win_pays_shares_minus_stake(self):
        days = [{"place": "T", "date": "e1", "settled": "31C",
                 "buckets": {"31C": (0.7, 0.40), "32C": (0.3, 0.55)},
                 "exec_books": {"31C": self.deep}}]
        r = simulate_executable(days, stake=1.0)
        self.assertEqual(r["bets"], 1)
        self.assertAlmostEqual(r["exec_pnl"], 1.0)       # 2 shares - $1
        self.assertTrue(r["has_books"])

    def test_executable_loss_is_minus_stake(self):
        days = [{"place": "T", "date": "e2", "settled": "99C",
                 "buckets": {"31C": (0.7, 0.40)}, "exec_books": {"31C": self.deep}}]
        self.assertAlmostEqual(simulate_executable(days)["exec_pnl"], -1.0)

    def test_thin_book_is_untradeable_exec(self):
        days = [{"place": "T", "date": "e3", "settled": "31C",
                 "buckets": {"31C": (0.7, 0.40)}, "exec_books": {"31C": self.thin}}]
        r = simulate_executable(days, stake=1.0)
        self.assertEqual(r["untradeable_exec"], 1)
        self.assertEqual(r["bets"], 0)
        self.assertAlmostEqual(r["exec_pnl"], 0.0)

    def test_missing_book_is_no_book(self):
        days = [{"place": "T", "date": "e4", "settled": "31C",
                 "buckets": {"31C": (0.7, 0.40)}, "exec_books": {}}]
        r = simulate_executable(days)
        self.assertEqual(r["no_book"], 1)
        self.assertEqual(r["bets"], 0)

    def test_no_edge_at_executable_price_does_not_bet(self):
        # model prob 0.45 < q_exec 0.50 -> crossing the spread kills the edge.
        days = [{"place": "T", "date": "e5", "settled": "31C",
                 "buckets": {"31C": (0.45, 0.40)}, "exec_books": {"31C": self.deep}}]
        r = simulate_executable(days)
        self.assertEqual(r["bets"], 0)
        self.assertEqual(r["untradeable_exec"], 0)
        self.assertEqual(r["no_book"], 0)


if __name__ == "__main__":
    unittest.main()
