"""KAT for tools/kalshi_logger.py — S2b instrumentation (kalshi_expansion.md /
kalshi_sf_seam.md). Pins seam rule 5 (dollar/fp strings; absent → None, NEVER 0 — the
S1 false-empty-books bug made structurally impossible), bucket-bound semantics, and the
idempotence keys. Network duties are not exercised (offline KAT).
"""
from __future__ import annotations

import os
import tempfile
import unittest

from tools.kalshi_logger import _append, _loaded_dates, fnum, parse_market


class TestKalshiLogger(unittest.TestCase):

    def test_module_self_test(self):
        from tools import kalshi_logger
        kalshi_logger._self_test()

    def test_seam_rule_5_absent_is_none_never_zero(self):
        self.assertIsNone(fnum(None))
        self.assertIsNone(fnum(""))
        self.assertIsNone(fnum("garbage"))
        self.assertEqual(fnum("0.4500"), 0.45)
        self.assertEqual(fnum("5342.49"), 5342.49)
        b = parse_market({"ticker": "K-T76", "cap_strike": 75})
        self.assertIsNone(b["yes_bid"])          # absent quote is UNKNOWN, not zero
        self.assertIsNone(b["vol_fp"])

    def test_bucket_bounds_band_and_open_tails(self):
        band = parse_market({"ticker": "K-B82.5", "floor_strike": 82, "cap_strike": 83})
        self.assertEqual((band["floor"], band["cap"]), (82, 83))   # inclusive by contract
        low_tail = parse_market({"ticker": "K-T76", "cap_strike": 75})
        self.assertIsNone(low_tail["floor"])
        self.assertEqual(low_tail["cap"], 75)
        high_tail = parse_market({"ticker": "K-T83", "floor_strike": 84})
        self.assertEqual(high_tail["floor"], 84)
        self.assertIsNone(high_tail["cap"])

    def test_idempotence_keys(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "x.jsonl")
            _append(p, {"date": "2026-07-13"})
            _append(p, {"event": "KXHIGHTSFO-26JUL12"})
            self.assertIn("2026-07-13", _loaded_dates(p, "date"))
            self.assertIn("KXHIGHTSFO-26JUL12", _loaded_dates(p, "event"))
            self.assertNotIn("2026-07-14", _loaded_dates(p, "date"))
            self.assertEqual(_loaded_dates(os.path.join(td, "missing.jsonl"), "date"),
                             set())


if __name__ == "__main__":
    unittest.main()
