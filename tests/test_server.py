"""Network-free KATs for the web layer: serialization + city-local default date.

Run with:  PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import datetime as dt
import http.client
import json
import threading
import time
import unittest
from unittest import mock

import server
from weather_council.council import Verdict
from weather_council.sources import Place


PLACE = Place(name="Singapore", country="SG", latitude=1.35, longitude=103.8,
              timezone="Asia/Singapore")


def _minimal_verdict(target: dt.date) -> Verdict:
    """A Verdict stub that only needs a target string to survive _run_verdict."""
    return Verdict(
        place=PLACE,
        target=target.isoformat(),
        high=30.0,
        low=25.0,
        high_spread=1.0,
        low_spread=1.0,
        confidence="climatology",
        confidence_detail={},
        votes=[],
        included_high=[],
        included_low=[],
        weights_high={},
        weights_low={},
        validation=None,
        observation=None,
        ensemble=None,
        interpretation=None,
        diurnal=None,
        records=None,
        representativeness=None,
        truth_source={},
        target_basis="test",
        target_status="forecast",
        qc={},
        requests_made=0,
    )


class TestRunVerdictDefaultTarget(unittest.TestCase):
    """The default target must be city-local today + 1, not host-local today + 1."""

    @mock.patch.object(server, "Council")
    @mock.patch.object(server, "_settlement_reference", return_value=None)
    @mock.patch.object(server, "_anchor_cross_reference", return_value=None)
    @mock.patch.object(server, "verdict_to_dict",
                       side_effect=lambda v, *a, **k: {"target": v.target})
    @mock.patch.object(server, "place_today", return_value=dt.date(2026, 7, 15))
    def test_default_target_is_place_today_plus_one(
        self, mock_place_today, _vd, _acr, _sr, mock_council
    ):
        mock_council.return_value.deliberate.side_effect = (
            lambda place, target, window: _minimal_verdict(target)
        )
        sources = mock.MagicMock()
        sources.geocode.return_value = PLACE
        with mock.patch.object(server, "Sources", return_value=sources):
            data = server._run_verdict("Singapore", "", "60")
        self.assertEqual(data["target"], "2026-07-16")
        mock_place_today.assert_called_once_with(PLACE)
        mock_council.return_value.deliberate.assert_called_once_with(
            PLACE, dt.date(2026, 7, 16), 60)


class TestConcurrencySerialization(unittest.TestCase):
    """Overlapping /api/verdict requests must queue; a saturated request gets 503."""

    def setUp(self):
        # Give each test a pristine semaphore so a failed predecessor can't
        # leave the module lock in a bad state.
        self._old_lock = server._VERDICT_LOCK
        server._VERDICT_LOCK = threading.Semaphore(1)

    def tearDown(self):
        server._VERDICT_LOCK = self._old_lock

    def test_overlapping_verdict_requests_return_retryable_503(self):
        entered = threading.Event()
        slow = {"ok": True}

        def _slow_run(*args, **kwargs):
            entered.set()
            time.sleep(0.4)
            return slow

        with mock.patch.object(server, "_run_verdict", side_effect=_slow_run):
            httpd = server.ThreadingHTTPServer((server.HOST, 0), server.Handler)
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                path = "/api/verdict?city=London"
                errors: list[Exception] = []

                def first_request():
                    try:
                        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                        c.request("GET", path)
                        resp = c.getresponse()
                        resp.read()
                        c.close()
                    except Exception as exc:
                        errors.append(exc)

                first = threading.Thread(target=first_request)
                first.start()
                self.assertTrue(entered.wait(timeout=1.0),
                                "first request never entered _run_verdict")

                c2 = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                c2.request("GET", path)
                resp = c2.getresponse()
                self.assertEqual(resp.status, 503)
                body = json.loads(resp.read().decode())
                c2.close()
                self.assertTrue(body.get("retryable"))
                self.assertIn("in progress", body.get("error", ""))

                first.join(timeout=5)
                self.assertEqual(errors, [])
            finally:
                httpd.shutdown()
                t.join(timeout=2)
