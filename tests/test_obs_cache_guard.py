"""Tests for the obs-cache poisoning guard (sources._cached_range_obs).

The 2026-07-02 outage: a dead-DNS launchd window made the raw fetch return [], which was
CACHED and then served for the 7-day TTL — silently breaking the WU-native validation gate
("insufficient history"). The guard: a cached blob is trusted, and a fresh result written,
only if it covers >=25% of the requested days; anything thinner is a miss and refetches."""
import datetime as dt
import unittest
from unittest import mock

from weather_council import sources as S


def _mk_rows(start: dt.date, n_days: int):
    return [(f"{start + dt.timedelta(days=i)} 12:00", 30.0) for i in range(n_days)]


class TestObsCacheGuard(unittest.TestCase):
    def setUp(self):
        self.src = S.Sources()
        self.start = dt.date.today() - dt.timedelta(days=40)
        self.end = dt.date.today()

    def test_days_covered(self):
        self.assertEqual(S._obs_days_covered([]), 0)
        self.assertEqual(S._obs_days_covered(_mk_rows(self.start, 10)), 10)
        self.assertEqual(S._obs_days_covered([("2026-06-01 01:00", 1.0),
                                              ("2026-06-01 02:00", 2.0)]), 1)

    def test_poisoned_empty_blob_is_refetched_and_not_rewritten_thin(self):
        """A cached EMPTY blob must be treated as a miss; a thin refetch must NOT be cached."""
        writes = []
        with mock.patch.object(S, "_history_cache_read",
                               return_value=({"obs": []}, dt.timedelta(hours=1))), \
             mock.patch.object(S, "_history_cache_write",
                               side_effect=lambda k, d: writes.append(d)):
            calls = []

            def raw(a, b):
                calls.append((a, b))
                return []                                   # network still dead -> thin again
            out = self.src._cached_range_obs("t", "WSSS", self.start, self.end, "x", raw)
        self.assertTrue(calls, "poisoned cache must trigger a refetch, not be served")
        self.assertEqual(writes, [], "a thin/empty refetch must never be written to cache")
        self.assertEqual(out, [])

    def test_healthy_refetch_heals_the_key(self):
        """Once the network is back, the refetched healthy blob is cached (self-heal)."""
        writes = []
        healthy = _mk_rows(self.start, 38)                   # ~full coverage of the past window
        with mock.patch.object(S, "_history_cache_read",
                               return_value=({"obs": []}, dt.timedelta(hours=1))), \
             mock.patch.object(S, "_history_cache_write",
                               side_effect=lambda k, d: writes.append(d)):
            out = self.src._cached_range_obs("t", "WSSS", self.start, self.end, "x",
                                             lambda a, b: _mk_rows(a, (b - a).days + 1))
        self.assertEqual(len(writes), 1, "healthy refetch must be cached")
        self.assertGreater(S._obs_days_covered(out), 30)

    def test_healthy_cache_is_served_without_refetch(self):
        cached = _mk_rows(self.start, 38)
        with mock.patch.object(S, "_history_cache_read",
                               return_value=({"obs": [list(r) for r in cached]},
                                             dt.timedelta(hours=1))), \
             mock.patch.object(S, "_history_cache_write") as w:
            calls = []

            def raw(a, b):
                calls.append((a, b))
                return _mk_rows(a, (b - a).days + 1)
            self.src._cached_range_obs("t", "WSSS", self.start, self.end, "x", raw)
        # exactly ONE raw fetch: the never-cached fresh tail; the past came from cache
        self.assertEqual(len(calls), 1)
        cutoff = dt.date.today() - S.OBS_CACHE_MARGIN
        self.assertEqual(calls[0][0], cutoff + dt.timedelta(days=1))
        w.assert_not_called()


if __name__ == "__main__":
    unittest.main()
