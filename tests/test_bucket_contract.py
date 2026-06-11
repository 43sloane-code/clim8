"""Network-free tests for candidate 42 — the market-usable daily verdict CONTRACT
and boundary guard. Validates the schema on REPLAYED logged days (leak-free
trailing sigma) and the live 2026-06-10 centrals, and proves the boundary guard
fires on the half-integer edges the harness actually settles on.

Stdlib unittest only. Run with:
    PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import csv
import os
import statistics
import unittest

from weather_council.bucket_contract import (
    REQUIRED_KEYS, _BANNED, bucket_probabilities, boundary_distance,
    daily_contract, _self_test,
)

REPORTS = os.path.join(os.path.dirname(__file__), "..", "reports")
STREAMS = ("london_high.csv", "hong_kong_high.csv")
# The live council headline centrals for the 2026-06-10 settlement date.
LIVE = {"LONDON CITY (EGLC)": (17.3, 0.8), "HONG KONG (HKO)": (28.9, 1.2)}


def _load(path):
    out = []
    with open(path) as fh:
        for r in csv.DictReader(fh):
            try:
                out.append((r["date"], float(r["point"]), float(r["realized"])))
            except (ValueError, KeyError):
                continue
    return out


def _assert_valid_contract(tc, c):
    tc.assertTrue(REQUIRED_KEYS <= set(c), REQUIRED_KEYS - set(c))
    tc.assertAlmostEqual(sum(c["buckets"].values()), 1.0, places=3)
    blob = " ".join(f"{k}={v}" for k, v in c.items()).lower()
    for banned in _BANNED:
        tc.assertNotIn(banned, blob)
    tc.assertIsInstance(c["boundary_flag"], bool)
    tc.assertIsInstance(c["modal_bucket"], int)


class TestBucketContract(unittest.TestCase):
    def test_module_self_test(self):
        _self_test()

    def test_pmf_is_a_distribution_and_centred(self):
        for c in (12.0, 17.3, 28.9, 33.7):
            p = bucket_probabilities(c, 1.0, grain="F")
            self.assertAlmostEqual(sum(p.values()), 1.0, places=9)
            f = c * 9.0 / 5.0 + 32.0
            self.assertEqual(max(p, key=p.get), round(f) if (f - int(f)) != 0.5 else int(f) + 1)

    def test_boundary_guard_geometry(self):
        # Bucket CENTRE (edges at half-integers) is robust; near a half-integer is fragile.
        c_centre = (64.0 - 32.0) * 5.0 / 9.0
        c_edge = (64.45 - 32.0) * 5.0 / 9.0
        self.assertGreater(boundary_distance(c_centre, grain="F"), 0.45)
        self.assertLess(boundary_distance(c_edge, grain="F"), 0.3)
        self.assertFalse(daily_contract("T", "2026-06-10", c_centre, 0.8)["boundary_flag"])
        self.assertTrue(daily_contract("T", "2026-06-10", c_edge, 0.8)["boundary_flag"])

    def test_schema_validates_on_last_10_logged_days(self):
        """Spec acceptance for candidate 42: replay the last 10 logged days of each
        stream with a leak-free trailing-residual sigma and assert the contract
        schema validates every time."""
        for fname in STREAMS:
            path = os.path.join(REPORTS, fname)
            if not os.path.exists(path):
                self.skipTest(f"missing {fname}")
            rows = _load(path)
            self.assertGreaterEqual(len(rows), 30, fname)
            resid = [rz - pt for _, pt, rz in rows]
            for i in range(len(rows) - 10, len(rows)):
                date, point, _ = rows[i]
                prior = resid[:i]
                sigma = statistics.pstdev(prior) if len(prior) >= 2 else 1.0
                sigma = max(sigma, 0.3)            # floor; never a zero-width claim
                c = daily_contract(fname, date, point, sigma)
                _assert_valid_contract(self, c)

    def test_live_2026_06_10_centrals(self):
        for station, (central, sigma) in LIVE.items():
            c = daily_contract(station, "2026-06-10", central, sigma)
            _assert_valid_contract(self, c)
            # Default (no confirmed live tape) => resolution source is explicitly UNVERIFIED.
            self.assertIn("UNVERIFIED", c["resolution_source"])


if __name__ == "__main__":
    unittest.main()
