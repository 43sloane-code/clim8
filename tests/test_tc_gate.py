"""Network-free tests for the tropical-cyclone halt gate (ledger candidate 52).

The gate is a hard risk control, asymmetric by design: a false halt costs one
skipped Hong Kong day, a false "all clear" can cost a blown settlement. These
fixtures pin that asymmetry — every ambiguous outcome must resolve to HALT or
UNVERIFIED, never to a silent clear:

  * Hong Kong only — London and other cities are a no-op (verified clear).
  * A TC forecast circle covering HK within the horizon -> HALT.
  * A TC forecast track that stays away from HK -> verified clear.
  * JMA explicitly reporting zero active TCs -> verified clear.
  * The active-list feed failing -> UNVERIFIED (never clear).
  * A listed TC whose forecast cannot be fetched or parsed -> UNVERIFIED
    (a feed/parse failure is never silently treated as all-clear).

Run with:  PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import datetime as dt
import unittest

from weather_council import tc_gate
from weather_council.tc_gate import (TCHalt, JMA_TARGET_URL, JMA_BASE,
                                     parse_jma_forecast, cone_contains,
                                     _ForecastPoint, _SourceError)


class _Place:
    def __init__(self, name, lat, lon):
        self.name = name
        self.latitude = lat
        self.longitude = lon


# Hong Kong (geocoded centroid) and a clearly-not-HK control.
HK = _Place("Hong Kong", 22.27832, 114.17469)
LONDON = _Place("London", 51.5074, -0.1278)
NOW = dt.datetime(2026, 7, 1, 0, 0, tzinfo=dt.timezone.utc)


def _fc_url(tc_id):
    return f"{JMA_BASE}/{tc_id}/forecast.json"


class FakeHTTP:
    """Minimal stand-in for SafeHTTPClient.get_json_array, keyed by URL."""

    def __init__(self, responses=None, errors=()):
        self.responses = responses or {}
        self.errors = set(errors)
        self.calls = []

    def get_json_array(self, base_url, params=None):
        self.calls.append(base_url)
        if base_url in self.errors:
            raise RuntimeError(f"simulated transport failure for {base_url}")
        if base_url in self.responses:
            return self.responses[base_url]
        raise RuntimeError(f"unexpected URL in test: {base_url}")


# A forecast document whose probability circle covers Hong Kong within 24 h.
_FC_HITS_HK = [{
    "forecast": [
        {"hour": 24, "lat": 22.3, "lon": 114.2, "radius": 120},
        {"hour": 48, "lat": 24.0, "lon": 116.0, "radius": 180},
    ]
}]
# A forecast document tracking up toward Japan — never near Hong Kong.
_FC_MISSES_HK = [{
    "forecast": [
        {"hour": 24, "lat": 28.0, "lon": 135.0, "radius": 150},
        {"hour": 48, "lat": 32.0, "lon": 140.0, "radius": 200},
    ]
}]
# A document with no usable track points (info-only) -> must be UNVERIFIED.
_FC_UNPARSEABLE = [{"category": "info", "note": "no track here"}]


class TestNonHongKongIsNoOp(unittest.TestCase):
    def test_london_returns_clear_without_any_fetch(self):
        http = FakeHTTP()  # any fetch would raise "unexpected URL"
        self.assertIsNone(tc_gate.tc_halt(LONDON, http=http, now=NOW))
        self.assertEqual(http.calls, [])  # gate never touches the network


class TestVerifiedClear(unittest.TestCase):
    def test_empty_active_list_is_clear(self):
        http = FakeHTTP({JMA_TARGET_URL: []})
        self.assertIsNone(tc_gate.tc_halt(HK, http=http, now=NOW))

    def test_active_tc_away_from_hk_is_clear(self):
        http = FakeHTTP({JMA_TARGET_URL: ["2503"],
                         _fc_url("2503"): _FC_MISSES_HK})
        self.assertIsNone(tc_gate.tc_halt(HK, http=http, now=NOW))


class TestHalt(unittest.TestCase):
    def test_tc_cone_over_hk_halts(self):
        http = FakeHTTP({JMA_TARGET_URL: ["2503"],
                         _fc_url("2503"): _FC_HITS_HK})
        res = tc_gate.tc_halt(HK, http=http, now=NOW)
        self.assertIsInstance(res, TCHalt)
        self.assertTrue(res.is_halt)
        self.assertEqual(res.source, "JMA")
        self.assertEqual(res.name, "2503")
        self.assertIsNotNone(res.closest_km)
        self.assertLess(res.closest_km, 120)
        self.assertEqual(res.within_hours, 24)

    def test_halt_wins_even_when_a_later_tc_is_clear(self):
        http = FakeHTTP({JMA_TARGET_URL: ["2503", "2504"],
                         _fc_url("2503"): _FC_HITS_HK,
                         _fc_url("2504"): _FC_MISSES_HK})
        res = tc_gate.tc_halt(HK, http=http, now=NOW)
        self.assertTrue(res.is_halt)


class TestUnverifiedNeverClear(unittest.TestCase):
    def test_active_list_fetch_failure_is_unverified(self):
        http = FakeHTTP(errors=[JMA_TARGET_URL])
        res = tc_gate.tc_halt(HK, http=http, now=NOW)
        self.assertIsInstance(res, TCHalt)
        self.assertTrue(res.is_unverified)
        self.assertFalse(res.is_halt)

    def test_listed_tc_forecast_fetch_failure_is_unverified(self):
        # JMA says a TC is active but we cannot read its forecast -> NOT clear.
        http = FakeHTTP({JMA_TARGET_URL: ["2503"]},
                        errors=[_fc_url("2503")])
        res = tc_gate.tc_halt(HK, http=http, now=NOW)
        self.assertTrue(res.is_unverified)

    def test_unparseable_forecast_is_unverified(self):
        http = FakeHTTP({JMA_TARGET_URL: ["2503"],
                         _fc_url("2503"): _FC_UNPARSEABLE})
        res = tc_gate.tc_halt(HK, http=http, now=NOW)
        self.assertTrue(res.is_unverified)


class TestParsingAndGeometry(unittest.TestCase):
    def test_parse_rejects_empty_track(self):
        with self.assertRaises(_SourceError):
            parse_jma_forecast(_FC_UNPARSEABLE)

    def test_parse_extracts_points_and_default_radius(self):
        doc = [{"forecast": [{"hour": 12, "lat": 20.0, "lon": 130.0}]}]
        pts = parse_jma_forecast(doc)
        self.assertEqual(len(pts), 1)
        self.assertEqual(pts[0].radius_km, tc_gate.DEFAULT_CIRCLE_KM)

    def test_cone_contains_inside_and_outside(self):
        near = [_ForecastPoint(hours=24, lat=22.3, lon=114.2, radius_km=80)]
        hit, km, hrs = cone_contains(near, HK.latitude, HK.longitude)
        self.assertTrue(hit)
        self.assertEqual(hrs, 24)
        far = [_ForecastPoint(hours=24, lat=35.0, lon=139.0, radius_km=80)]
        hit2, _, _ = cone_contains(far, HK.latitude, HK.longitude)
        self.assertFalse(hit2)

    def test_points_beyond_horizon_are_ignored(self):
        beyond = [_ForecastPoint(hours=tc_gate.HORIZON_HOURS + 5,
                                 lat=22.3, lon=114.2, radius_km=300)]
        hit, _, _ = cone_contains(beyond, HK.latitude, HK.longitude)
        self.assertFalse(hit)


if __name__ == "__main__":
    unittest.main()
