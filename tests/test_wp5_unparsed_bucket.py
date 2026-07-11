"""KAT for WP-5 (served-number campaign): an unparseable outcome label must be QUARANTINED — excluded
from the bucket ladder AND from the de-vig denominator (Σ yes in implied_probabilities), and recorded
in WeatherMarket.unparsed_outcomes. Pre-fix it survived as a (None,None) bucket at ladder index 0 and
its price diluted every real bucket's implied probability.

Network-free. Confirmed RED pre-fix, GREEN with the fix.
Run:  PYTHONPATH=. python3 -m unittest tests.test_wp5_unparsed_bucket -v
"""
from __future__ import annotations

import unittest

from weather_council.market import _parse_event, _bucket_edges


def _mkt(label, yes, *, unit="Celsius"):
    return {"groupItemTitle": label, "outcomes": '["Yes", "No"]',
            "outcomePrices": f'["{yes}", "{1 - float(yes):.2f}"]', "clobTokenIds": '["a", "b"]',
            "description": (f"This market resolves to the highest temperature recorded at the "
                            f"London City Airport in degrees {unit} on 15 Jul '26.")}


def _event(markets):
    return {"id": "e", "title": "Highest temperature in London on July 15?",
            "slug": "highest-temperature-in-london-on-july-15-2026",
            "endDate": "2026-07-15T12:00:00Z", "closed": False, "markets": markets}


class TestWp5UnparsedBucket(unittest.TestCase):
    def test_bucket_edges_precondition(self):
        # the fixture's junk label really is unparseable -> (None, None); if this ever changes,
        # the KAT below would be vacuous, so pin it.
        self.assertEqual(_bucket_edges("Scattered showers"), (None, None))
        self.assertNotEqual(_bucket_edges("22°C"), (None, None))

    def test_malformed_bucket_quarantined_and_devig_excludes_it(self):
        m = _parse_event(_event([
            _mkt("22°C", "0.30"), _mkt("23°C", "0.50"),
            _mkt("Scattered showers", "0.20"),          # unparseable -> must be quarantined
        ]))
        labels = [b.label for b in m.buckets]
        self.assertNotIn("Scattered showers", labels)              # excluded from the ladder
        self.assertIn("Scattered showers", m.unparsed_outcomes)    # but recorded as evidence
        ip = m.implied_probabilities()
        self.assertNotIn("Scattered showers", ip)                  # not in the de-vig output
        # de-vig over the REAL buckets only: 0.30 / (0.30+0.50) = 0.375, NOT 0.30 (diluted by 0.20)
        self.assertAlmostEqual(ip["22°C"], 0.375)
        self.assertAlmostEqual(ip["23°C"], 0.625)

    def test_all_parseable_is_parity(self):
        m = _parse_event(_event([_mkt("22°C", "0.40"), _mkt("23°C", "0.60")]))
        self.assertEqual(m.unparsed_outcomes, ())
        self.assertEqual([b.label for b in m.buckets], ["22°C", "23°C"])
        ip = m.implied_probabilities()
        self.assertAlmostEqual(ip["22°C"], 0.40)                   # no vig here -> unchanged
        self.assertAlmostEqual(ip["23°C"], 0.60)


if __name__ == "__main__":
    unittest.main()
