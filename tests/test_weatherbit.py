"""Network-free tests for the Weatherbit tracked-forecaster source.

Weatherbit is a RECOMMEND-ONLY tracked forecaster (logged/scored prospectively,
never voted into the live blend until it earns history). These tests prove the
fetch path is honest and leak-free WITHOUT any network or API key:
  * no key set -> silent None, and NO request is attempted;
  * the API key travels in the request PARAMS, never in the base_url (so it
    cannot leak into a logged URL);
  * a well-formed response is parsed to (high, low) °C for the target day;
  * a missing day, a missing/garbage field, or a transport error all yield None
    (honest-or-nothing — never a fabricated number, never a raised run-killer).

Stdlib unittest only. Run with:
    PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import datetime as dt
import os
import unittest
from unittest import mock

from weather_council.security import SecurityError
from weather_council.sources import Sources, Place, WEATHERBIT_FORECAST_URL


def _place() -> Place:
    return Place(name="Test", country="X", latitude=51.5, longitude=-0.1,
                 timezone="UTC")


class _FakeHTTP:
    """Records calls and returns a canned payload (or raises), so the source can
    be exercised with zero network. Mirrors only the get_json surface used."""

    def __init__(self, response=None, raise_exc=None):
        self.response = response
        self.raise_exc = raise_exc
        self.calls: list[tuple[str, dict]] = []

    def get_json(self, base_url, params):
        self.calls.append((base_url, params))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


# A fixed far-future target so the response's valid_date is fully under our
# control and the test never depends on the wall clock.
TARGET = dt.date(2099, 1, 2)
PAYLOAD = {
    "city_name": "Test", "units": "M",
    "data": [
        {"valid_date": "2099-01-01", "max_temp": 9.0, "min_temp": 2.0},
        {"valid_date": "2099-01-02", "max_temp": 11.5, "min_temp": 4.25},
        {"valid_date": "2099-01-03", "max_temp": 8.0, "min_temp": 1.0},
    ],
}


class TestWeatherbitForecast(unittest.TestCase):

    def test_no_key_returns_none_without_calling_network(self):
        fake = _FakeHTTP(response=PAYLOAD)
        src = Sources(http=fake)
        with mock.patch.dict(os.environ, {"WEATHERBIT_API_KEY": ""}, clear=False):
            self.assertIsNone(src.fetch_weatherbit_forecast(_place(), TARGET))
        self.assertEqual(fake.calls, [])      # silent skip: no request attempted

    def test_parses_matching_day(self):
        fake = _FakeHTTP(response=PAYLOAD)
        src = Sources(http=fake)
        with mock.patch.dict(os.environ, {"WEATHERBIT_API_KEY": "secret"}, clear=False):
            out = src.fetch_weatherbit_forecast(_place(), TARGET)
        self.assertEqual(out, (11.5, 4.25))

    def test_key_rides_in_params_not_in_url(self):
        fake = _FakeHTTP(response=PAYLOAD)
        src = Sources(http=fake)
        with mock.patch.dict(os.environ, {"WEATHERBIT_API_KEY": "secret"}, clear=False):
            src.fetch_weatherbit_forecast(_place(), TARGET)
        self.assertEqual(len(fake.calls), 1)
        base_url, params = fake.calls[0]
        # The base_url is the bare endpoint — no key embedded anywhere in it.
        self.assertEqual(base_url, WEATHERBIT_FORECAST_URL)
        self.assertNotIn("secret", base_url)
        # The key is supplied as a parameter (SafeHTTPClient never logs params).
        self.assertEqual(params.get("key"), "secret")
        self.assertEqual(params.get("units"), "M")

    def test_missing_target_day_returns_none(self):
        payload = {"data": [{"valid_date": "2099-01-01",
                             "max_temp": 9.0, "min_temp": 2.0}]}
        src = Sources(http=_FakeHTTP(response=payload))
        with mock.patch.dict(os.environ, {"WEATHERBIT_API_KEY": "secret"}, clear=False):
            self.assertIsNone(src.fetch_weatherbit_forecast(_place(), TARGET))

    def test_garbage_field_returns_none_not_fabricated(self):
        payload = {"data": [{"valid_date": "2099-01-02",
                             "max_temp": "n/a", "min_temp": 4.0}]}
        src = Sources(http=_FakeHTTP(response=payload))
        with mock.patch.dict(os.environ, {"WEATHERBIT_API_KEY": "secret"}, clear=False):
            self.assertIsNone(src.fetch_weatherbit_forecast(_place(), TARGET))

    def test_transport_error_degrades_to_none(self):
        fake = _FakeHTTP(raise_exc=SecurityError("api.weatherbit.io rate-limited"))
        src = Sources(http=fake)
        with mock.patch.dict(os.environ, {"WEATHERBIT_API_KEY": "secret"}, clear=False):
            self.assertIsNone(src.fetch_weatherbit_forecast(_place(), TARGET))

    def test_non_list_data_returns_none(self):
        src = Sources(http=_FakeHTTP(response={"error": "Invalid API Key"}))
        with mock.patch.dict(os.environ, {"WEATHERBIT_API_KEY": "secret"}, clear=False):
            self.assertIsNone(src.fetch_weatherbit_forecast(_place(), TARGET))


if __name__ == "__main__":
    unittest.main()
