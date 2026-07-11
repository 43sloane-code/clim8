"""KAT for WP-6 (served-number campaign): compact_buckets must emit a contiguous interior so the cells
partition the integer line and NO interior mass is dropped (the compacted pmf sums to the input's total).
Pre-fix, a sub-floor interior bucket strictly between lo and hi was silently dropped.

Run:  PYTHONPATH=. python3 -m unittest tests.test_wp6_compact_partition -v
"""
from __future__ import annotations

import unittest

from weather_council.bucket_contract import compact_buckets


class TestWp6Partition(unittest.TestCase):
    def test_interior_subfloor_bucket_not_dropped(self):
        out = compact_buckets({10: 0.5, 11: 0.003, 12: 0.497})   # 11 below tail_floor, BETWEEN lo/hi
        self.assertIn("11", out)                                  # kept (RED pre-fix: it vanished)
        self.assertAlmostEqual(sum(out.values()), 1.0)           # mass preserved (was 0.997)
        self.assertAlmostEqual(out["11"], 0.003)

    def test_degenerate_all_tail_keeps_mode_and_partitions(self):
        # every bucket below tail_floor (a very flat pmf) -> keep the mode, tails partition the rest
        probs = {k: 1 / 21 for k in range(-10, 11)}              # 21 buckets, each ~0.0476 > floor...
        # force all sub-floor by spreading thin:
        probs = {k: 1 / 400 for k in range(-199, 201)}          # 400 buckets each 0.0025 < 0.005
        out = compact_buckets(probs)
        self.assertAlmostEqual(sum(out.values()), 1.0)          # partition holds, mass preserved
        # no integer label appears twice (structural partition)
        int_labels = [k for k in out if not k.startswith("<=") and not k.startswith(">=")]
        self.assertEqual(len(int_labels), len(set(int_labels)))

    def test_parity_on_contiguous_unimodal(self):
        probs = {20: 0.05, 21: 0.25, 22: 0.40, 23: 0.25, 24: 0.05}   # already contiguous, all > floor
        out = compact_buckets(probs)
        self.assertAlmostEqual(sum(out.values()), 1.0)
        self.assertEqual([k for k in out if k.isdigit() or (k[0] == "-" and k[1:].isdigit())],
                         ["20", "21", "22", "23", "24"])         # cells unchanged (no-op)

    def test_property_mass_preserved_over_battery(self):
        import math
        for mu, sd in [(22, 0.8), (30, 2.0), (5, 0.5), (18, 3.0)]:
            probs = {k: math.exp(-((k - mu) ** 2) / (2 * sd * sd)) for k in range(mu - 10, mu + 11)}
            z = sum(probs.values())
            probs = {k: v / z for k, v in probs.items()}
            out = compact_buckets(probs)
            self.assertAlmostEqual(sum(out.values()), sum(probs.values()), places=9,
                                   msg=f"mass not preserved for mu={mu} sd={sd}")

    def test_empty_is_empty(self):
        self.assertEqual(compact_buckets({}), {})


if __name__ == "__main__":
    unittest.main()
