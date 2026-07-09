"""Network-free KATs for daily-LOW market support (the feature that was missing —
run.py only ever fetched the highest-temperature event).

Pins: the lowest-temperature event slug, the highest|lowest title regex, fetch-by-
slug parsing, and compare_low — the read-only model-vs-market bucket comparison
built on the council's LOW residual cloud. compare_high is unchanged (it now
delegates to the same shared core; the existing high KATs guard that path).

Run with:  PYTHONPATH=. python3 -m unittest tests.test_low_market -v
"""
from __future__ import annotations

import datetime as dt
import unittest

from weather_council.market import (WeatherMarket, MarketBucket, MarketData,
                                     event_slug, resolved_event_slug, _TITLE_RE)
from weather_council.compare import compare_low


def _low_ladder():
    # whole-°C London low market (the July-9 shape: modal 22, heavy 23)
    return (
        MarketBucket("20°C or below", 0.05, 0.95, (), None, 20),
        MarketBucket("21°C", 0.19, 0.81, (), 21, 21),
        MarketBucket("22°C", 0.40, 0.60, (), 22, 22),
        MarketBucket("23°C", 0.36, 0.64, (), 23, 23),
        MarketBucket("24°C or above", 0.05, 0.95, (), 24, None),
    )


def _low_market():
    return WeatherMarket(
        event_id="ldn-low", title="Lowest temperature in London on July 9?",
        city="London", date_label="July 9", station="London City Airport",
        grain="C", precision="whole °C", resolution_source=None, end_date=None,
        slug="lowest-temperature-in-london-on-july-9-2026", buckets=_low_ladder())


class TestEventSlug(unittest.TestCase):
    def test_low_and_high_slugs(self):
        t = dt.date(2026, 7, 9)
        self.assertEqual(event_slug("London, United Kingdom", t, "low"),
                         "lowest-temperature-in-london-on-july-9-2026")
        self.assertEqual(event_slug("London", t, "high"),
                         "highest-temperature-in-london-on-july-9-2026")
        # back-compat alias still returns the HIGH slug (settlement audit relies on it)
        self.assertEqual(resolved_event_slug("Hong Kong, HK", dt.date(2026, 6, 12)),
                         "highest-temperature-in-hong-kong-on-june-12-2026")


class TestTitleRegex(unittest.TestCase):
    def test_matches_lowest_and_highest(self):
        m = _TITLE_RE.search("Lowest temperature in London on July 9?")
        self.assertIsNotNone(m)
        self.assertEqual(m.group("kind").lower(), "lowest")
        self.assertEqual(m.group("city"), "London")
        h = _TITLE_RE.search("Highest temperature in Hong Kong on June 12?")
        self.assertIsNotNone(h)
        self.assertEqual(h.group("kind").lower(), "highest")
        self.assertEqual(h.group("city"), "Hong Kong")


class TestFetchBySlug(unittest.TestCase):
    def test_parses_event_from_stub_http(self):
        slug = "lowest-temperature-in-london-on-july-9-2026"
        event = {
            "id": "1", "slug": slug,
            "title": "Lowest temperature in London on July 9?",
            "markets": [
                {"groupItemTitle": "22°C", "outcomes": '["Yes", "No"]',
                 "outcomePrices": '["0.40", "0.60"]',
                 "description": "the lowest temperature recorded at the London City "
                                "Airport Station in Celsius"},
                {"groupItemTitle": "23°C", "outcomes": '["Yes", "No"]',
                 "outcomePrices": '["0.36", "0.64"]',
                 "description": "the London City Airport Station in Celsius"},
            ],
        }

        class _Stub:
            def get_json_array(self, url, params):
                return [event] if params.get("slug") == slug else []

        md = MarketData(http=_Stub())
        wm = md.fetch_market_by_slug(slug)
        self.assertIsNotNone(wm)
        self.assertEqual(wm.city, "London")
        self.assertEqual({b.label for b in wm.buckets}, {"22°C", "23°C"})
        b22 = next(b for b in wm.buckets if b.label == "22°C")
        self.assertAlmostEqual(b22.yes_price, 0.40, places=2)
        # a slug that returns no matching event -> None (not an exception)
        self.assertIsNone(md.fetch_market_by_slug("no-such-slug"))


class TestCompareLow(unittest.TestCase):
    def test_low_pmf_built_on_low_cloud(self):
        market = _low_market()
        # tight low residual cloud around a 21.8°C verdict -> bucket 22 dominates
        residuals = [0.0, 0.1, -0.1, 0.2, -0.2, 0.3, -0.3, 0.15, -0.15, 0.05] * 4
        c = compare_low(market, 21.8, residuals)
        self.assertIsNotNone(c)
        self.assertEqual(c.model_modal, "22°C")
        self.assertEqual(c.market_modal, "22°C")
        # the LOW verdict value flows through (the shared field holds the compared verdict)
        self.assertAlmostEqual(c.verdict_high_c, 21.8, places=2)
        row22 = next(b for b in c.buckets if b.label == "22°C")
        self.assertGreater(row22.model_prob, 0.5)         # model concentrated on 22
        self.assertIsNotNone(row22.market_prob)           # de-vigged market present
        # a colder verdict re-centres the model pmf onto 21 — proves it uses the value
        c_cold = compare_low(market, 21.0, residuals)
        self.assertEqual(c_cold.model_modal, "21°C")

    def test_declines_below_min_residuals(self):
        self.assertIsNone(compare_low(_low_market(), 21.8, [0.1, 0.2]))


if __name__ == "__main__":
    unittest.main()
