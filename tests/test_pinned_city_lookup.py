"""KAT: the pinned-city lookups never match an unnamed place (run._pinned_city_for).

Both _settlement_reference_for and _anchor_cross_reference_for match on city-name
containment — and "" is a substring of EVERY key, so an unnamed place used to
silently inherit the first table entry (Hong Kong's cross-reference directive).
The two lookups are now one shared helper with the empty-name guard; this pins
None for an empty/missing name and the containment behavior for real names.

Run with:  PYTHONPATH=. python3 -m unittest tests.test_pinned_city_lookup -v
"""
from __future__ import annotations

import types
import unittest

import run


def _place(name):
    return types.SimpleNamespace(name=name)


class TestPinnedCityLookup(unittest.TestCase):
    def test_empty_name_matches_nothing(self):
        self.assertIsNone(run._settlement_reference_for(_place("")))
        self.assertIsNone(run._anchor_cross_reference_for(_place("")))

    def test_missing_name_matches_nothing(self):
        self.assertIsNone(run._settlement_reference_for(_place(None)))
        self.assertIsNone(run._anchor_cross_reference_for(types.SimpleNamespace()))

    def test_whitespace_name_matches_nothing(self):
        self.assertIsNone(run._settlement_reference_for(_place("   ")))
        self.assertIsNone(run._anchor_cross_reference_for(_place("   ")))

    def test_containment_still_hits(self):
        # 'London, GB' contains the 'london' key; 'Hong Kong' hits the cross-ref table.
        self.assertEqual(run._settlement_reference_for(_place("London, GB"))["icao"],
                         "EGLC")
        self.assertIsNotNone(run._anchor_cross_reference_for(_place("Hong Kong")))
        # the tables are not interchangeable: HK has no settlement pin
        self.assertIsNone(run._settlement_reference_for(_place("Hong Kong")))


if __name__ == "__main__":
    unittest.main()
