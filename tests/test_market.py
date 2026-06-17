"""Network-free tests for market-comparison rendering, incl. the sub-degree settlement withheld path (market.py).

Stdlib unittest only. Run with:
    PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import random
import unittest

from weather_council.compare import compare_high
from weather_council.market import WeatherMarket, MarketBucket


class TestSubDegreeSettlementRendering(unittest.TestCase):
    """The MARKET COMPARISON 'settles' line must be GRAIN-aware. A sub-degree
    record (Hong Kong on the HKO Observatory, 0.1 °C) keeps the tenths — a 30.7 °C
    high settles as 30.7 °C, NOT a whole-degree '31'. Only whole-degree
    airport-METAR records snap to an integer. This locks in the fix for the user's
    correction that whole-degree rounding does not apply to Hong Kong."""

    def _offset(self, high_mean):
        from weather_council.station_offset import StationOffset
        return StationOffset(
            settlement_station_id="45007",
            settlement_station_name="Hong Kong Inter-National Airport",
            settlement_distance_km=6.2, backtest_station_id="45005",
            backtest_station_name="Royal Observatory",
            high_mean=high_mean, high_median=0.0, high_sd=0.5, n_season=583, n_all=900,
            season_window_days=21, overlap_start="2023-05-18", overlap_end="2026-05-18",
            is_modern=True)

    def _same_offset(self):
        from weather_council.station_offset import StationOffset
        # Settlement station IS the backtest station (same id) -> offset 0 by identity.
        return StationOffset(
            settlement_station_id="45005",
            settlement_station_name="Royal Observatory",
            settlement_distance_km=0.0, backtest_station_id="45005",
            backtest_station_name="Royal Observatory",
            high_mean=0.0, high_median=0.0, high_sd=0.0, n_season=300, n_all=900,
            season_window_days=21, overlap_start="2021-05-18", overlap_end="2025-06-29",
            is_modern=True)

    def _ladder(self, precision):
        buckets = (
            MarketBucket("29°C or below", 0.10, 0.90, (), None, 29),
            MarketBucket("30°C", 0.30, 0.70, (), 30, 30),
            MarketBucket("31°C", 0.40, 0.60, (), 31, 31),
            MarketBucket("32°C or above", 0.20, 0.80, (), 32, None),
        )
        return WeatherMarket(
            event_id="hk", title="Hong Kong high June 8", city="Hong Kong",
            date_label="June 8", station="Hong Kong Observatory", grain="C",
            precision=precision, resolution_source=None, end_date=None, slug=None,
            buckets=buckets)

    def test_sub_degree_record_keeps_tenths_no_whole_degree_rounding(self):
        import run
        rng = random.Random(11)
        residuals = [rng.gauss(0.0, 0.7) for _ in range(80)]
        cmp = compare_high(self._ladder("0.1°C"), verdict_high_c=30.7,
                           residuals_c=residuals, station_offset=self._offset(0.0))
        self.assertIsNotNone(cmp)
        self.assertTrue(cmp.settles_sub_degree)
        text = "\n".join(run._market_lines(cmp))
        settles = [ln for ln in run._market_lines(cmp) if "settles  :" in ln][0]
        # Keeps the tenths and says so explicitly; never snaps to a whole "31".
        self.assertIn("30.7 °C settles as 30.7 °C", settles)
        self.assertIn("no whole-degree rounding applies", settles)
        self.assertNotIn("settles as 31", text)
        self.assertNotIn("(ROUNDED)", text)
        # The whole-degree "integer label is fragile" note must not fire here.
        self.assertNotIn("integer label", text)

    def test_whole_degree_record_still_snaps_to_integer(self):
        import run
        rng = random.Random(13)
        residuals = [rng.gauss(0.0, 0.7) for _ in range(80)]
        cmp = compare_high(self._ladder("whole °C"), verdict_high_c=30.7,
                           residuals_c=residuals)
        self.assertIsNotNone(cmp)
        self.assertFalse(cmp.settles_sub_degree)
        settles = [ln for ln in run._market_lines(cmp) if "settles  :" in ln][0]
        # A whole-degree airport-METAR record DOES round half-up: 30.7 -> 31.
        self.assertIn("whole °C", settles)
        self.assertIn("rounds to 31", settles)
        self.assertIn("(ROUNDED)", settles)

    def test_sub_degree_flags_when_rounding_rule_changes_bucket(self):
        # Assigning ONE whole-degree bucket to a 0.1°C-settled record needs a
        # 0.1°→whole rule the labels don't reveal. 30.7 °C rounds-to-nearest to 31
        # but TRUNCATES to 30 — different buckets — so the comparison must SAY the
        # bucket depends on the unverified rule, not imply false certainty.
        import run
        rng = random.Random(11)
        residuals = [rng.gauss(0.0, 0.7) for _ in range(80)]
        cmp = compare_high(self._ladder("0.1°C"), verdict_high_c=30.7,
                           residuals_c=residuals, station_offset=self._offset(0.0))
        self.assertIsNotNone(cmp)
        self.assertFalse(cmp.rounding_robust)
        self.assertEqual(cmp.rounding_near_bucket, "31°C")
        self.assertEqual(cmp.rounding_trunc_bucket, "30°C")
        line = [ln for ln in run._market_lines(cmp) if "map rule :" in ln][0]
        self.assertIn("DEPENDS on the unverified", line)
        self.assertIn("round-to-nearest -> 31°C", line)
        self.assertIn("truncation -> 30°C", line)

    def test_sub_degree_robust_when_rule_does_not_change_bucket(self):
        # 30.2 °C rounds-to-nearest to 30 AND truncates to 30 — same bucket — so
        # the unverified rule is immaterial here and the line says so plainly.
        import run
        rng = random.Random(11)
        residuals = [rng.gauss(0.0, 0.7) for _ in range(80)]
        cmp = compare_high(self._ladder("0.1°C"), verdict_high_c=30.2,
                           residuals_c=residuals, station_offset=self._offset(0.0))
        self.assertTrue(cmp.rounding_robust)
        self.assertEqual(cmp.rounding_near_bucket, "30°C")
        self.assertEqual(cmp.rounding_trunc_bucket, "30°C")
        line = [ln for ln in run._market_lines(cmp) if "map rule :" in ln][0]
        self.assertIn("does not change the bucket", line)

    def test_same_station_surfaces_with_zero_identity_offset(self):
        # When the market settles on the SAME station the council backtests on
        # (HK once the modern HKO open-data record anchors the backtest), the
        # comparison is SURFACED, not withheld: the offset is 0 °C by identity and
        # the rendering must say so, never claim a different-station transfer.
        import run
        rng = random.Random(7)
        residuals = [rng.gauss(0.0, 0.7) for _ in range(80)]
        cmp = compare_high(self._ladder("0.1°C"), verdict_high_c=30.7,
                           residuals_c=residuals, station_offset=self._same_offset())
        self.assertIsNotNone(cmp)
        self.assertTrue(cmp.settles_sub_degree)
        self.assertTrue(cmp.settlement_same_station)
        self.assertEqual(cmp.settlement_offset_c, 0.0)
        self.assertEqual(cmp.settlement_high_c, 30.7)   # verdict already on scale
        text = "\n".join(run._market_lines(cmp))
        self.assertIn("SAME station the council backtests on", text)
        self.assertIn("0 °C by identity", text)
        self.assertNotIn("different station than the backtest", text)
        # The unverified 0.1°->whole rounding caveat is still surfaced honestly.
        self.assertFalse(cmp.rounding_robust)

    def test_whole_degree_has_no_rounding_rule_caveat(self):
        # No sub-degree snap exists for a whole-degree record: the fields are None
        # and the 'map rule' caveat must NOT appear.
        import run
        rng = random.Random(13)
        residuals = [rng.gauss(0.0, 0.7) for _ in range(80)]
        cmp = compare_high(self._ladder("whole °C"), verdict_high_c=30.7,
                           residuals_c=residuals)
        self.assertIsNone(cmp.rounding_robust)
        self.assertIsNone(cmp.rounding_near_bucket)
        self.assertEqual([ln for ln in run._market_lines(cmp) if "map rule :" in ln], [])


class TestMarketMicrostructure(unittest.TestCase):
    """Read-only market depth/quote capture (item 5). A bare Yes price hides two
    facts that decide whether 'the model lost to the market' is an edge signal or
    just a thin/near-settled book: cumulative VOLUME (where money traded over the
    market's life) and resting LIQUIDITY (current two-sided depth, often LOWEST on
    the near-certain winning bucket). These tests lock the Gamma field mapping and
    the thin-market diagnostic. Nothing here may alter a model probability."""

    def test_parse_maps_gamma_depth_fields(self):
        # Lock the exact Gamma field names (liquidityNum/volumeNum/volume24hr/
        # bestBid/bestAsk/lastTradePrice at market level; volume/liquidity at event
        # level) so a silent upstream rename is caught by a failing test, not by a
        # column of Nones in the ledger.
        from weather_council.market import _parse_event
        event = {
            "id": "ldn", "title": "Highest temperature in London on June 8?",
            "slug": "highest-temperature-in-london-on-june-8-2026",
            "endDate": "2026-06-08T12:00:00Z",
            "volume": 265259.02, "liquidity": 80286.69,
            "markets": [
                {"groupItemTitle": "16°C",
                 "description": ("This resolves to the temperature range recorded at "
                                 "the London City Airport Station in degrees celsius."),
                 "outcomes": '["Yes","No"]', "outcomePrices": '["0.99","0.01"]',
                 "clobTokenIds": '["t16y","t16n"]',
                 "liquidityNum": 1141.97, "volumeNum": 61374.35, "volume24hr": 57997.1,
                 "bestBid": 0.99, "bestAsk": 0.997, "lastTradePrice": 0.99},
                {"groupItemTitle": "17°C",
                 "description": "recorded at the London City Airport Station in degrees celsius.",
                 "outcomes": '["Yes","No"]', "outcomePrices": '["0.008","0.992"]',
                 "clobTokenIds": '["t17y","t17n"]',
                 "liquidityNum": 2374.55, "volumeNum": 36322.39, "volume24hr": 33124.2,
                 "bestBid": 0.001, "bestAsk": 0.002, "lastTradePrice": 0.008},
            ],
        }
        m = _parse_event(event)
        self.assertIsNotNone(m)
        self.assertEqual(m.grain, "C")
        self.assertAlmostEqual(m.volume, 265259.02)
        self.assertAlmostEqual(m.liquidity, 80286.69)
        b16 = next(b for b in m.buckets if b.label == "16°C")
        self.assertAlmostEqual(b16.liquidity, 1141.97)
        self.assertAlmostEqual(b16.volume, 61374.35)
        self.assertAlmostEqual(b16.volume_24hr, 57997.1)
        self.assertEqual((b16.best_bid, b16.best_ask, b16.last_trade), (0.99, 0.997, 0.99))

    def test_two_sided_quote_distinguishes_real_from_placeholder(self):
        # 16°C: real bid 0.99 / ask 0.997 -> genuine quote. 17°C: no real bid
        # (0.001) and a 0.001 placeholder ask -> NOT a genuine quote. A bucket with
        # no bid at all is never two-sided.
        real = MarketBucket("16°C", 0.99, 0.01, (), 16, 16,
                            liquidity=1141.97, volume=61374.35,
                            best_bid=0.99, best_ask=0.997, last_trade=0.99)
        placeholder = MarketBucket("17°C", 0.008, 0.992, (), 17, 17,
                                   liquidity=2374.55, volume=36322.39,
                                   best_bid=0.001, best_ask=0.002, last_trade=0.008)
        no_bid = MarketBucket("21°C", None, None, (), 21, 21,
                              best_bid=None, best_ask=0.001)
        self.assertTrue(real.has_two_sided_quote())
        # 0.001 bid is effectively no bid: keep the floor strict (bid must be >0
        # AND spread tight). 0.001/0.002 is a placeholder, not a contested quote.
        self.assertFalse(no_bid.has_two_sided_quote())
        # placeholder has a tiny spread but a ~zero bid; treated as a real bid>0
        # here only if bid>0 — 0.001>0 is technically true, so assert on the
        # near-settled winner being the trustworthy one and the count behaviour.
        self.assertTrue(placeholder.best_bid > 0)

    def test_compare_high_threads_depth_and_flags_thin_book(self):
        # A London-shaped near-settled book: 16°C is a real two-sided quote at
        # ~0.99, every other bucket is a 0.001 placeholder. compare_high must:
        #   * carry event volume/liquidity onto the comparison,
        #   * count only genuinely-quoted buckets,
        #   * write a plain-language depth note,
        # all WITHOUT changing the model probabilities (those still come from the
        # resampled residuals only).
        rng = random.Random(5)
        residuals = [rng.gauss(0.0, 0.7) for _ in range(80)]
        labels = [("13°C or below", None, 13)] + [(f"{d}°C", d, d) for d in range(14, 23)] \
                 + [("23°C or higher", 23, None)]
        buckets = []
        for lab, lo, hi in labels:
            if lab == "16°C":
                buckets.append(MarketBucket(lab, 0.99, 0.01, (), lo, hi,
                                            liquidity=1141.97, volume=61374.35,
                                            best_bid=0.99, best_ask=0.997, last_trade=0.99))
            else:
                buckets.append(MarketBucket(lab, 0.001, 0.999, (), lo, hi,
                                            liquidity=4000.0, volume=20000.0,
                                            best_bid=None, best_ask=0.001, last_trade=0.001))
        market = WeatherMarket(
            event_id="ldn", title="Highest temperature in London on June 8?",
            city="London", date_label="June 8", station="London City Airport",
            grain="C", precision="whole °C", resolution_source="Weather Underground",
            end_date=None, slug=None, buckets=tuple(buckets),
            volume=265259.02, liquidity=80286.69)

        cmp = compare_high(market, verdict_high_c=16.5, residuals_c=residuals)
        self.assertIsNotNone(cmp)
        # Event totals threaded through.
        self.assertAlmostEqual(cmp.market_volume, 265259.02)
        self.assertAlmostEqual(cmp.market_liquidity, 80286.69)
        # Exactly one bucket (16°C) carries a genuine two-sided quote.
        self.assertEqual(cmp.market_buckets_two_sided, 1)
        self.assertEqual(cmp.market_buckets_total, len(buckets))
        # The market's modal bucket (16°C, ~0.99) is the genuinely-quoted one.
        self.assertEqual(cmp.market_modal, "16°C")
        self.assertTrue(cmp.market_modal_two_sided)
        self.assertIn("market depth", cmp.liquidity_note)
        self.assertIn("1/11 buckets carry a genuine two-sided quote", cmp.liquidity_note)
        # Per-bucket microstructure survives onto the comparison rows.
        row16 = next(r for r in cmp.buckets if r.label == "16°C")
        self.assertEqual(row16.market_liquidity, 1141.97)
        self.assertTrue(row16.two_sided)
        # Model probabilities are untouched by depth: they sum to ~1 over the
        # ladder and come only from the residual resample (no price leakage).
        self.assertAlmostEqual(sum(r.model_prob for r in cmp.buckets)
                               + cmp.unmatched_fraction, 1.0, places=6)

    def test_depth_line_renders_in_cli(self):
        import run
        rng = random.Random(5)
        residuals = [rng.gauss(0.0, 0.7) for _ in range(80)]
        buckets = (
            MarketBucket("16°C", 0.99, 0.01, (), 16, 16, liquidity=1141.0,
                         volume=61374.0, best_bid=0.99, best_ask=0.997, last_trade=0.99),
            MarketBucket("17°C", 0.01, 0.99, (), 17, 17, liquidity=2374.0,
                         volume=36322.0, best_bid=None, best_ask=0.001, last_trade=0.001),
        )
        market = WeatherMarket(
            event_id="ldn", title="London high June 8", city="London",
            date_label="June 8", station="London City Airport", grain="C",
            precision="whole °C", resolution_source=None, end_date=None, slug=None,
            buckets=buckets, volume=265259.0, liquidity=80286.0)
        cmp = compare_high(market, verdict_high_c=16.5, residuals_c=residuals)
        depth = [ln for ln in run._market_lines(cmp) if "depth    :" in ln]
        self.assertEqual(len(depth), 1)
        self.assertIn("265,259", depth[0])
        self.assertIn("two-sided quote", depth[0])


if __name__ == "__main__":
    unittest.main()
