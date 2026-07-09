"""KAT for storage.utc_now_iso() (Phase 6a — charter: UTC is non-optional).

The persisted-timestamp convention: a UTC wall-clock instant in NAIVE ISO format (no offset),
seconds precision — byte-compatible with legacy rows so the lexicographic issued_at ordering
paper_pnl relies on never breaks. Must be UTC regardless of the host TZ env.
"""
from __future__ import annotations

import datetime as dt
import os
import time
import unittest

from weather_council.storage import utc_now_iso


class TestUtcNowIso(unittest.TestCase):
    def test_naive_seconds_utc_format(self):
        s = utc_now_iso()
        self.assertNotIn("+", s)                 # no offset (naive)
        self.assertNotIn("Z", s)
        self.assertEqual(len(s), 19)             # YYYY-MM-DDTHH:MM:SS, seconds precision
        parsed = dt.datetime.fromisoformat(s)
        self.assertIsNone(parsed.tzinfo)         # parses as naive
        ref = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        self.assertLess(abs((ref - parsed).total_seconds()), 5)   # equals the UTC instant

    def test_unaffected_by_host_tz(self):
        old = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "America/Los_Angeles"   # UTC-7/8
            time.tzset()
            got = dt.datetime.fromisoformat(utc_now_iso())
            ref = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
            # if it leaked local time it would be ~7-8h off; it must stay UTC
            self.assertLess(abs((ref - got).total_seconds()), 5)
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()


if __name__ == "__main__":
    unittest.main()
