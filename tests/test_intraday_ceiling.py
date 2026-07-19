"""Network-free tests for the intraday-ceiling sharpening (the lead-0 conviction
lever). Verifies the pure rise/sharpen core (leak-free, settlement-rule quantize,
monotone safety) and the orchestrator's applicability gates (London only; HK and
others abstain; today-only; feed/empty/thin-history all degrade to 'unavailable').

Run with:  PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import datetime as dt
import unittest

from weather_council.sources import Place
from weather_council.intraday_ceiling import (
    IntradayCeiling, _city_key, intraday_ceiling, remaining_rise_samples,
    sharpen_pmf, MIN_RISE_SAMPLES)


LDN = Place(name="London", country="GB", latitude=51.51, longitude=-0.13,
            timezone="Europe/London")
HK = Place(name="Hong Kong", country="HK", latitude=22.30, longitude=114.17,
           timezone="Asia/Hong_Kong")
TOKYO = Place(name="Tokyo", country="JP", latitude=35.68, longitude=139.69,
              timezone="Asia/Tokyo")
MANILA = Place(name="Manila", country="PH", latitude=14.51, longitude=121.02,
               timezone="Asia/Manila")
TODAY = dt.date(2026, 6, 12)


class FakeSources:
    """Serves a canned EGLC hourly METAR series and records the call."""
    def __init__(self, obs=None, raise_metar=False):
        self._obs = obs or []
        self._raise = raise_metar
        self.calls = []

    def fetch_metar_observations(self, icao, start, end, timezone):
        self.calls.append((icao, start, end, timezone))
        if self._raise:
            raise RuntimeError("IEM archive unreachable")
        return list(self._obs)


def _day(date_iso, hours_temps):
    return [(f"{date_iso} {h:02d}:00", t) for h, t in hours_temps]


def _history_obs(n_days, *, end_before=TODAY):
    """n_days of earlier obs: 15:00 running max 20.0, final (18:00) 21.0 -> a
    constant remaining-rise of 1.0 at 15:00, 6.0 at 09:00."""
    obs = []
    for i in range(n_days):
        d = (end_before - dt.timedelta(days=i + 1)).isoformat()
        obs += _day(d, [(9, 15.0), (12, 18.0), (15, 20.0), (18, 21.0)])
    return obs


# ---- pure core ------------------------------------------------------------- #
class TestCore(unittest.TestCase):
    def test_remaining_rise_is_leak_free_and_conditional_on_hour(self):
        hist = {"2026-06-01": _day("2026-06-01", [(9, 15.0), (15, 20.0), (18, 21.0)])[0:],
                "2026-06-02": _day("2026-06-02", [(9, 16.0), (15, 19.0), (18, 22.0)])[0:]}
        hist = {d: [(int(ts[11:13]), c) for ts, c in v] for d, v in hist.items()}
        # rise@15 = final - max(by 15): day1 21-20=1.0, day2 22-19=3.0
        self.assertEqual(sorted(remaining_rise_samples(hist, 15)), [1.0, 3.0])
        # rise@9 = final - max(by 9): day1 21-15=6.0, day2 22-16=6.0
        self.assertEqual(sorted(remaining_rise_samples(hist, 9)), [6.0, 6.0])

    def test_sharpen_quantizes_with_settlement_rule(self):
        # London round-half-up: 20.4 + 1.0 = 21.4 -> 21
        self.assertEqual(sharpen_pmf(20.4, [1.0], sub_degree=False)[0], (21, 1.0))
        # HK floor: 28.6 + 0.0 -> 28 (not 29)
        self.assertEqual(sharpen_pmf(28.6, [0.0], sub_degree=True)[0], (28, 1.0))

    def test_sharpen_never_below_running_max_bucket(self):
        pmf = sharpen_pmf(21.4, [0.0, 0.4, 1.2, 2.6], sub_degree=False)
        self.assertTrue(all(b >= 21 for b, _ in pmf))     # round(21.4)=21 is the floor

    def test_pmf_normalised(self):
        pmf = sharpen_pmf(20.0, [0.0, 0.4, 0.6, 1.1, 1.9], sub_degree=False)
        self.assertAlmostEqual(sum(p for _, p in pmf), 1.0)


# ---- orchestrator gates ---------------------------------------------------- #
class TestGates(unittest.TestCase):
    def test_non_basket_city_is_noop(self):
        c = intraday_ceiling(TOKYO, TODAY, sources=FakeSources(), today=TODAY)
        self.assertEqual(c.kind, "not_basket")
        self.assertFalse(c.is_sharpened)

    def test_hong_kong_abstains_no_hourly_record(self):
        c = intraday_ceiling(HK, TODAY, sources=FakeSources(), today=TODAY)
        self.assertEqual(c.kind, "unavailable")
        self.assertIn("HKO Observatory", c.note)

    def test_future_target_is_not_today_without_fetch(self):
        src = FakeSources(obs=_history_obs(30))
        c = intraday_ceiling(LDN, TODAY + dt.timedelta(days=1), sources=src, today=TODAY)
        self.assertEqual(c.kind, "not_today")
        self.assertEqual(src.calls, [])          # no network for a non-current day

    def test_feed_error_is_unavailable(self):
        c = intraday_ceiling(LDN, TODAY, sources=FakeSources(raise_metar=True), today=TODAY)
        self.assertEqual(c.kind, "unavailable")
        self.assertIn("errored", c.note)

    def test_no_today_obs_is_unavailable(self):
        c = intraday_ceiling(LDN, TODAY, sources=FakeSources(obs=_history_obs(30)),
                             today=TODAY)
        self.assertEqual(c.kind, "unavailable")     # history but nothing today yet

    def test_thin_history_is_unavailable(self):
        obs = _history_obs(5) + _day(TODAY.isoformat(), [(9, 16.0), (15, 20.4)])
        c = intraday_ceiling(LDN, TODAY, sources=FakeSources(obs=obs), today=TODAY)
        self.assertEqual(c.kind, "unavailable")
        self.assertLess(c.n_rise, MIN_RISE_SAMPLES)


# ---- sharpened result ------------------------------------------------------ #
class TestSharpened(unittest.TestCase):
    def _src(self):
        obs = (_history_obs(25)
               + _day(TODAY.isoformat(), [(9, 16.0), (12, 19.0), (15, 20.4)])
               + _day((TODAY + dt.timedelta(days=1)).isoformat(), [(9, 99.0)]))  # future: must be ignored
        return FakeSources(obs=obs)

    def test_sharpened_modal_bucket_and_leak_free(self):
        c = intraday_ceiling(LDN, TODAY, sources=self._src(), today=TODAY)
        self.assertEqual(c.kind, "sharpened")
        self.assertEqual(c.hour, 15)                 # latest observed hour today
        self.assertAlmostEqual(c.running_max_c, 20.4)
        self.assertEqual(c.n_rise, 25)               # today + future excluded
        # rise@15 is a constant 1.0 across history -> 20.4+1.0=21.4 -> bucket 21 @ 100%
        self.assertEqual(c.modal_bucket, 21)
        self.assertAlmostEqual(c.modal_prob, 1.0)
        self.assertAlmostEqual(sum(p for _, p in c.pmf), 1.0)

    def test_explicit_now_hour_uses_earlier_running_max(self):
        # At 09:00 today the running max is 16.0 and the learned rise@9 is 6.0
        # -> 16.0+6.0 = 22.0 -> bucket 22.
        c = intraday_ceiling(LDN, TODAY, sources=self._src(), today=TODAY, now_hour=9)
        self.assertEqual(c.kind, "sharpened")
        self.assertEqual(c.modal_bucket, 22)


class TestFrozen(unittest.TestCase):
    def test_frozen_dataclass(self):
        c = IntradayCeiling(kind="sharpened", city="London", target="2026-06-12",
                            sub_degree=False, modal_bucket=22)
        with self.assertRaises(Exception):
            c.modal_bucket = 5  # type: ignore[misc]


class TestManila(unittest.TestCase):
    """Manila replaces Hong Kong as a tracked city — and unlike HK it settles on an
    airport (Ninoy Aquino RPLL) with an hourly METAR record, so it GETS the intraday
    lever (round-half-up, exactly like London)."""
    def test_manila_is_hourly_configured_and_sharpens(self):
        obs = (_history_obs(25)
               + _day(TODAY.isoformat(), [(9, 31.0), (12, 33.0), (15, 33.4)]))
        c = intraday_ceiling(MANILA, TODAY, sources=FakeSources(obs=obs), today=TODAY)
        self.assertEqual(c.kind, "sharpened")        # NOT "not_basket"/"unavailable"
        self.assertFalse(c.sub_degree)               # round-half-up, like London
        self.assertEqual(c.hour, 15)
        self.assertAlmostEqual(c.running_max_c, 33.4)
        self.assertEqual(sum(p for _, p in c.pmf) > 0, True)


class TestCityKey(unittest.TestCase):
    def test_exact_match(self):
        self.assertEqual(_city_key(LDN), "london")
        self.assertEqual(_city_key(MANILA), "manila")

    def test_decorated_form_matches_word_boundary(self):
        p = Place(name="London, GB", country="GB", latitude=51.5, longitude=-0.1,
                  timezone="Europe/London")
        self.assertEqual(_city_key(p), "london")

    def test_substring_collision_returns_none(self):
        for bad in ["Londonderry", "Manilal", "San Francisco de Macorís",
                    "Singaporean"]:
            p = Place(name=bad, country="XX", latitude=0, longitude=0,
                      timezone="UTC")
            self.assertIsNone(_city_key(p), f"{bad!r} must not match")

    def test_unknown_city_returns_none(self):
        p = Place(name="Tokyo", country="JP", latitude=35.68, longitude=139.69,
                  timezone="Asia/Tokyo")
        self.assertIsNone(_city_key(p))


if __name__ == "__main__":
    unittest.main()
