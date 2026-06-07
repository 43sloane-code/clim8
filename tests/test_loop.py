"""Network-free tests for the hypothesis->deploy feedback loop (loop.py) and its recommend-only gate.

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


class TestFeedbackLoop(unittest.TestCase):
    """The operating loop (weather_council/loop.py) must enforce its gates: no
    stage skipped, recommend-only by default, LIVE only on C7-validation + human
    sign-off, and the hard boundary (no trades/funds/autonomous edits) unbuyable
    by any statistics."""

    def test_self_test_passes(self):
        from weather_council import loop
        loop._self_test()

    def test_recommend_only_is_the_default_deploy(self):
        from weather_council import loop
        r = loop.run(loop._base_good())
        self.assertEqual(r.action, "RECOMMEND_ONLY")
        self.assertEqual(r.reached, loop.Stage.DEPLOY)

    def test_live_requires_c7_and_signoff(self):
        from weather_council import loop
        e = loop._base_good()
        e.c7_validated = True
        self.assertEqual(loop.run(e).action, "RECOMMEND_ONLY")   # signoff missing
        e.human_signoff = True
        self.assertEqual(loop.run(e).action, "LIVE")

    def test_hard_boundary_stops_at_risk(self):
        from weather_council import loop
        for attr in ("places_trades", "moves_funds", "autonomous_code_edit"):
            e = loop._base_good()
            setattr(e, attr, True)
            r = loop.run(e)
            self.assertEqual(r.reached, loop.Stage.RISK)
            self.assertEqual(r.action, "REJECTED")

    def test_no_edge_stops_at_validate(self):
        from weather_council import loop
        e = loop._base_good()
        e.measured_skill = e.threshold - 0.01
        self.assertEqual(loop.run(e).reached, loop.Stage.VALIDATE)

    def test_unlocked_hypothesis_cannot_validate(self):
        from weather_council import loop
        e = loop._base_good()
        e.locked_hash = None
        self.assertEqual(loop.run(e).reached, loop.Stage.HYPOTHESIS)

if __name__ == "__main__":
    unittest.main()
