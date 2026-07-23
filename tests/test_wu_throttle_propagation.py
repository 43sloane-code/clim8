"""KAT: a throttled Wunderground oracle must FAIL LOUD, never silently degrade.

_wu_daily_raw / _wu_hourly_raw used to swallow every per-chunk exception into
`data = {}` — including RateLimitError. That made council.py's
`except RateLimitError: raise` in _resolve_truth unreachable, so a 429'd WU
oracle silently re-anchored Manila/Singapore/SF on ~91-day-lagged Meteostat —
the exact "never silently re-anchor" breach that code documents against. The
raw chunk core now re-raises RateLimitError BEFORE the generic swallow (which
itself routes through record_soft_failure).

Stdlib unittest only. Run with:
    PYTHONPATH=. python3 -m unittest tests.test_wu_throttle_propagation -v
"""
from __future__ import annotations

import datetime as dt
import unittest

from weather_council.security import RateLimitError
from weather_council.sources import Sources


class _ThrottledHTTP:
    """Every WU history request is rate-limited (retry budget exhausted)."""
    def get_json(self, url, params=None):
        raise RateLimitError("429 Too Many Requests")


class _DeadHTTP:
    """A generic (non-throttle) transport failure — must STILL be swallowed."""
    def get_json(self, url, params=None):
        raise ConnectionError("DNS dead")


class TestWUThrottlePropagation(unittest.TestCase):
    START = dt.date(2026, 7, 1)
    END = dt.date(2026, 7, 5)

    def test_wu_daily_raw_reraises_rate_limit(self):
        s = Sources()
        s.http = _ThrottledHTTP()
        with self.assertRaises(RateLimitError):
            s._wu_daily_raw("RPLL", self.START, self.END, "Asia/Manila")

    def test_wu_hourly_raw_reraises_rate_limit(self):
        s = Sources()
        s.http = _ThrottledHTTP()
        with self.assertRaises(RateLimitError):
            s._wu_hourly_raw("RPLL", self.START, self.END, "Asia/Manila")

    def test_public_daily_series_propagates_throttle(self):
        # End-to-end through the obs-cache seam: a window entirely inside the
        # always-fresh recent tail (start > cache cutoff) never touches the
        # disk cache, so the throttle reaches the caller unmuted.
        s = Sources()
        s.http = _ThrottledHTTP()
        today = dt.date.today()
        with self.assertRaises(RateLimitError):
            s.wunderground_daily_series("RPLL", today, today, "Asia/Manila")

    def test_generic_failure_still_swallowed_empty(self):
        # The resilience contract is unchanged for non-throttle failures: an
        # empty result, not an exception (and now a recorded soft failure).
        s = Sources()
        s.http = _DeadHTTP()
        self.assertEqual(
            s._wu_daily_raw("RPLL", self.START, self.END, "Asia/Manila"), [])
        self.assertEqual(
            s._wu_hourly_raw("RPLL", self.START, self.END, "Asia/Manila"), [])


if __name__ == "__main__":
    unittest.main()
