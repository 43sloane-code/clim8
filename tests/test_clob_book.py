"""KAT for the order-book parse + executable depth-walk (weather_council/clob_book.py, Phase 2).

Pins the arithmetic the executable-P&L layer (Phase 5) rides on: string coercion, malformed-level
dropping, best-first sorting, one-sided/empty degradation, and the ask-ladder walk (within a level,
crossing levels with slippage, exact-notional, book-exhaustion → UNTRADEABLE, non-positive target).
Pure and network-free.

Run with:  PYTHONPATH=. python3 -m unittest tests.test_clob_book -v
"""
from __future__ import annotations

import unittest

from weather_council import clob_book
from weather_council.clob_book import (BookLevel, TokenBook, parse_book,
                                       fill_buy, book_stats)

# A canonical two-sided book, given deliberately OUT of order to prove sorting.
RAW = {
    "asset_id": "TKN1", "timestamp": "2026-07-10T12:00:00Z",
    "bids": [{"price": "0.40", "size": "100"}, {"price": "0.45", "size": "50"}],
    "asks": [{"price": "0.55", "size": "200"}, {"price": "0.52", "size": "100"}],
}


class TestModuleSelftest(unittest.TestCase):
    def test_module_selftest_passes(self):
        clob_book._selftest()          # the __main__ path stays green in the gate


class TestParse(unittest.TestCase):
    def test_coerces_strings_and_sorts_best_first(self):
        b = parse_book(RAW)
        self.assertEqual(b.token_id, "TKN1")
        self.assertEqual(b.timestamp, "2026-07-10T12:00:00Z")
        self.assertEqual(b.best_bid, 0.45)          # highest bid first
        self.assertEqual(b.best_ask, 0.52)          # lowest ask first
        self.assertIsInstance(b.asks[0], BookLevel)

    def test_mid_and_spread_only_when_two_sided(self):
        b = parse_book(RAW)
        self.assertAlmostEqual(b.mid, 0.485)
        self.assertAlmostEqual(b.spread, 0.07)

    def test_drops_malformed_zero_and_out_of_range_levels(self):
        dirty = parse_book({
            "asset_id": "TKN2",
            "bids": [{"price": "x", "size": "10"}, {"price": "0.3", "size": "0"},
                     {"price": "0.5"}, {"price": "0.6", "size": "5"}],
            "asks": [{"price": "1.5", "size": "10"}, {"price": "0", "size": "10"},
                     {"price": "0.7", "size": "8"}],
        })
        self.assertEqual([l.price for l in dirty.bids], [0.6])   # only the clean bid
        self.assertEqual([l.price for l in dirty.asks], [0.7])   # >1 and =0 dropped

    def test_one_sided_book_has_no_mid(self):
        one = parse_book({"asset_id": "O", "asks": [{"price": "0.5", "size": "10"}]})
        self.assertIsNone(one.best_bid)
        self.assertIsNone(one.mid)
        self.assertIsNone(one.spread)

    def test_empty_and_non_dict_degrade_safely(self):
        self.assertIsNone(parse_book({"asset_id": "E"}).mid)
        empty = parse_book("not a dict")            # type: ignore[arg-type]
        self.assertEqual(empty.token_id, "")
        self.assertEqual(empty.asks, ())

    def test_token_id_falls_back_to_token_id_key(self):
        b = parse_book({"token_id": "ALT", "asks": [{"price": "0.5", "size": "1"}]})
        self.assertEqual(b.token_id, "ALT")


class TestFill(unittest.TestCase):
    def setUp(self):
        self.b = parse_book(RAW)

    def test_fill_within_first_level_pays_best_ask(self):
        f = fill_buy(self.b, 1.0)
        self.assertTrue(f.filled)
        self.assertEqual(f.levels, 1)
        self.assertAlmostEqual(f.shares, 1.0 / 0.52)
        self.assertAlmostEqual(f.spent, 1.0)
        self.assertAlmostEqual(f.avg_price, 0.52)

    def test_fill_crossing_levels_has_slippage(self):
        f = fill_buy(self.b, 60.0)                   # $52 of L1 + $8 of L2
        self.assertEqual(f.levels, 2)
        self.assertTrue(f.filled)
        self.assertAlmostEqual(f.shares, 100.0 + 8.0 / 0.55)
        self.assertGreater(f.avg_price, 0.52)        # worse than top of book

    def test_exact_level_notional_fills_one_level(self):
        f = fill_buy(self.b, 52.0)                   # exactly all of L1
        self.assertTrue(f.filled)
        self.assertEqual(f.levels, 1)
        self.assertAlmostEqual(f.shares, 100.0)

    def test_book_exhaustion_is_unfilled(self):
        f = fill_buy(self.b, 1000.0)                 # only $162 of depth exists
        self.assertFalse(f.filled)                   # UNTRADEABLE at this size
        self.assertAlmostEqual(f.spent, 162.0)
        self.assertAlmostEqual(f.shares, 300.0)

    def test_nonpositive_and_no_ask_side_return_zero(self):
        bidsonly = parse_book({"asset_id": "B", "bids": [{"price": "0.4", "size": "10"}]})
        for res in (fill_buy(self.b, 0.0), fill_buy(self.b, -1.0), fill_buy(bidsonly, 1.0)):
            self.assertEqual(res.shares, 0.0)
            self.assertEqual(res.spent, 0.0)
            self.assertIsNone(res.avg_price)
            self.assertFalse(res.filled)

    def test_dollar_depth_walk_pnl_identity(self):
        # At $1: q_exec == 1/shares; win P&L = shares-1; loss P&L = -1.
        f = fill_buy(self.b, 1.0)
        self.assertAlmostEqual(1.0 / f.shares, f.avg_price)
        self.assertGreater(f.shares - 1.0, 0.0)


class TestStats(unittest.TestCase):
    def test_depth_spread_counts(self):
        st = book_stats(parse_book(RAW))
        self.assertEqual(st["best_ask"], 0.52)
        self.assertEqual(st["n_ask_levels"], 2)
        self.assertAlmostEqual(st["ask_depth_usd"], 162.0)
        self.assertAlmostEqual(st["bid_depth_usd"], 0.45 * 50 + 0.40 * 100)
        self.assertAlmostEqual(st["spread"], 0.07)

    def test_stats_none_safe_on_one_sided(self):
        one = parse_book({"asset_id": "O", "asks": [{"price": "0.5", "size": "10"}]})
        st = book_stats(one)
        self.assertIsNone(st["mid"])
        self.assertIsNone(st["spread"])
        self.assertEqual(st["n_bid_levels"], 0)


if __name__ == "__main__":
    unittest.main()
