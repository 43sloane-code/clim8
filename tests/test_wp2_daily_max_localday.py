"""KAT for WP-2 (served-number campaign): wunderground_daily_max must regroup observations onto the
station's LOCAL civil day (the day the contract settles on) before taking the max — not max() over
whatever the endpoint's UTC-ish window returned. This value is the phantom-cap ceiling feeding
_fuse_live_floor, so a straddle obs from the adjacent local day is a served-bucket defect.

Network-free: a fake http returns a fixed observation set (each carrying valid_time_gmt) regardless of
params. Confirmed RED on the pre-fix code (no timezone param + no local-day filter) then GREEN.

Run:  PYTHONPATH=. python3 -m unittest tests.test_wp2_daily_max_localday -v
"""
from __future__ import annotations

import datetime as dt
import unittest
from zoneinfo import ZoneInfo

from weather_council.sources import Sources


def _epoch(y, mo, d, h, mi, tzname):
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=ZoneInfo(tzname)).timestamp())


def _obs(temp_f, y, mo, d, h, mi, tzname):
    return {"temp": temp_f, "valid_time_gmt": _epoch(y, mo, d, h, mi, tzname)}


class _FakeHTTP:
    def __init__(self, obs):
        self.obs = obs
        self.calls = []

    def get_json(self, url, params):
        self.calls.append(params)
        return {"observations": self.obs}


def _src(obs):
    s = Sources()
    s.http = _FakeHTTP(obs)
    return s


class TestWp2LocalDay(unittest.TestCase):
    def test_f2a_utc8_next_day_local_obs_excluded(self):
        tz, tgt = "Asia/Singapore", dt.date(2026, 7, 11)
        obs = [_obs(90, 2026, 7, 11, 14, 0, tz),    # target 14:00 SGT -> belongs to target
               _obs(95, 2026, 7, 12, 0, 30, tz)]    # target+1 00:30 SGT -> STRADDLE, must be excluded
        r = _src(obs).wunderground_daily_max("WSSS", tgt, tz)
        self.assertEqual(r["max_f"], 90.0)          # NOT the 95 next-day straddle
        self.assertEqual(r["n_obs"], 1)
        self.assertNotEqual(r["max_f"], max(o["temp"] for o in obs))   # the delta vs naive max (95)

    def test_f2b_london_bst_straddle(self):
        tz, tgt = "Europe/London", dt.date(2026, 7, 15)   # BST = UTC+1
        obs = [_obs(82, 2026, 7, 15, 23, 30, tz),   # target 23:30 BST -> target
               _obs(85, 2026, 7, 16, 0, 30, tz)]    # target+1 00:30 BST -> excluded
        r = _src(obs).wunderground_daily_max("EGLC", tgt, tz)
        self.assertEqual(r["max_f"], 82.0)
        self.assertEqual(r["n_obs"], 1)

    def test_f2c_dst_fallback_groups_correctly(self):
        tz, tgt = "Europe/London", dt.date(2026, 10, 25)   # DST ends 02:00 BST -> 01:00 GMT
        obs = [_obs(60, 2026, 10, 25, 1, 30, tz),   # fold-hour local -> still 2026-10-25
               _obs(58, 2026, 10, 25, 14, 0, tz),   # afternoon GMT -> 2026-10-25
               _obs(70, 2026, 10, 24, 23, 30, tz)]  # prior day -> excluded though it's the naive max
        r = _src(obs).wunderground_daily_max("EGLC", tgt, tz)
        self.assertEqual(r["max_f"], 60.0)          # 70 (prior day) excluded across the DST boundary
        self.assertEqual(r["n_obs"], 2)

    def test_f2d_no_straddle_is_parity(self):
        tz, tgt = "Asia/Singapore", dt.date(2026, 7, 11)
        obs = [_obs(88, 2026, 7, 11, 10, 0, tz),
               _obs(91, 2026, 7, 11, 14, 0, tz),
               _obs(89, 2026, 7, 11, 16, 0, tz)]
        r = _src(obs).wunderground_daily_max("WSSS", tgt, tz)
        self.assertEqual(r["max_f"], max(o["temp"] for o in obs))   # no-op where there's no straddle
        self.assertEqual((r["max_f"], r["n_obs"]), (91.0, 3))

    def test_none_tz_defaults_to_utc_grouping(self):
        tgt = dt.date(2026, 7, 11)
        obs = [_obs(90, 2026, 7, 11, 14, 0, "UTC")]   # 14:00 UTC on target
        r = _src(obs).wunderground_daily_max("WSSS", tgt)   # no tz -> UTC grouping, no crash
        self.assertEqual(r["max_f"], 90.0)

    def test_empty_obs_returns_none(self):
        self.assertIsNone(_src([]).wunderground_daily_max("WSSS", dt.date(2026, 7, 11), "Asia/Singapore"))


if __name__ == "__main__":
    unittest.main()
