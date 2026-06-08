"""Network-free tests for market-comparison rendering, incl. the sub-degree settlement withheld path (market.py).

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

if __name__ == "__main__":
    unittest.main()
