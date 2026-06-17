"""Reproducible, network-free tests for the SafeHTTPClient sandbox (security.py).

These assert the safety band the rest of the project leans on: HTTPS-only,
host allowlist, SSRF guard (host must resolve to public IPs only), compressed
and decompressed size caps, the per-run request budget, JSON/array/text shape
validation, the redirect re-validation guard, the city-name validator, and the
retry/backoff helper. NOTHING here touches the network — every test either
exercises a check that fires before any socket call, or stubs out DNS / the
opener / the fetch with controlled fakes.

Stdlib unittest only — matching the project's no-dependency rule. Run with:
    PYTHONPATH=. python3 -m unittest discover -s tests
"""

from __future__ import annotations

import gzip
import unittest
from unittest import mock

from weather_council import security
from weather_council.security import (
    SafeHTTPClient,
    SecurityError,
    RateLimitError,
    validate_city,
    _validate_url,
    _assert_public_host,
    _retry_after_seconds,
    _GuardedRedirectHandler,
    ALLOWED_HOSTS,
    MAX_REQUESTS_PER_RUN,
)

# An arbitrary allowlisted HTTPS URL reused by tests that stub DNS away.
_ALLOWED_URL = "https://api.open-meteo.com/v1/forecast"


def _addrinfo(ip: str):
    """One getaddrinfo-style tuple resolving to `ip` (family inferred)."""
    fam = security.socket.AF_INET6 if ":" in ip else security.socket.AF_INET
    return [(fam, security.socket.SOCK_STREAM, security.socket.IPPROTO_TCP,
             "", (ip, 443))]


class _FakeResponse:
    """Context-manager stand-in for an opened HTTP response."""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n: int) -> bytes:
        return self._body[:n]


class _FakeOpener:
    """Replaces client._opener so _fetch never hits the network."""

    def __init__(self, body: bytes):
        self._body = body

    def open(self, req, timeout=None):
        return _FakeResponse(self._body)


# --------------------------------------------------------------------------- #
#  validate_city — the one piece of user-controlled input                       #
# --------------------------------------------------------------------------- #
class TestValidateCity(unittest.TestCase):
    def test_plain_name_passes_and_is_stripped(self):
        self.assertEqual(validate_city("  London  "), "London")

    def test_accented_and_separators_pass(self):
        for name in ["São Paulo", "Saint-Denis", "Washington, D.C.", "N'Djamena"]:
            self.assertEqual(validate_city(name), name)

    def test_empty_or_whitespace_rejected(self):
        for bad in ["", "   ", None]:
            with self.assertRaises(SecurityError):
                validate_city(bad)  # type: ignore[arg-type]

    def test_overlong_rejected(self):
        with self.assertRaises(SecurityError):
            validate_city("a" * 81)

    def test_injection_characters_rejected(self):
        # query-string / path / control characters must never reach a URL
        for bad in ["London&q=1", "city/../etc", "a\nb", "drop;table", "x?y", "<script>"]:
            with self.assertRaises(SecurityError):
                validate_city(bad)


# --------------------------------------------------------------------------- #
#  _validate_url — HTTPS-only + allowlist (both fire BEFORE any DNS)            #
# --------------------------------------------------------------------------- #
class TestValidateUrl(unittest.TestCase):
    def test_non_https_scheme_rejected_before_dns(self):
        # http/file/ftp/data must all fail; patch DNS to a tripwire to prove the
        # scheme check short-circuits before any resolution happens.
        with mock.patch.object(security.socket, "getaddrinfo",
                               side_effect=AssertionError("DNS must not run")):
            for bad in ["http://api.open-meteo.com/x",
                        "file:///etc/passwd",
                        "ftp://api.open-meteo.com/x",
                        "data:text/plain,hi"]:
                with self.assertRaises(SecurityError):
                    _validate_url(bad)

    def test_off_allowlist_host_rejected_before_dns(self):
        with mock.patch.object(security.socket, "getaddrinfo",
                               side_effect=AssertionError("DNS must not run")):
            for bad in ["https://evil.example.com/x",
                        "https://api.open-meteo.com.evil.com/x",
                        "https://localhost/x"]:
                with self.assertRaises(SecurityError):
                    _validate_url(bad)

    def test_allowlisted_host_returns_hostname(self):
        with mock.patch.object(security.socket, "getaddrinfo",
                               return_value=_addrinfo("93.184.216.34")):
            self.assertEqual(_validate_url(_ALLOWED_URL), "api.open-meteo.com")

    def test_every_allowed_host_is_https_reachable_in_policy(self):
        # Sanity: each allowlisted host passes scheme+allowlist with public DNS.
        with mock.patch.object(security.socket, "getaddrinfo",
                               return_value=_addrinfo("93.184.216.34")):
            for host in ALLOWED_HOSTS:
                self.assertEqual(_validate_url(f"https://{host}/p"), host)


# --------------------------------------------------------------------------- #
#  _assert_public_host — the SSRF guard                                         #
# --------------------------------------------------------------------------- #
class TestSSRFGuard(unittest.TestCase):
    def test_public_ip_allowed(self):
        with mock.patch.object(security.socket, "getaddrinfo",
                               return_value=_addrinfo("93.184.216.34")):
            _assert_public_host("api.open-meteo.com")   # must not raise

    def test_private_loopback_linklocal_reserved_blocked(self):
        # Classic SSRF targets incl. the cloud-metadata address 169.254.169.254.
        for ip in ["127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1",
                   "169.254.169.254", "::1", "fd00::1", "0.0.0.0"]:
            with mock.patch.object(security.socket, "getaddrinfo",
                                   return_value=_addrinfo(ip)):
                with self.assertRaises(SecurityError):
                    _assert_public_host("attacker.example")

    def test_any_private_address_in_set_blocks(self):
        # If a host resolves to BOTH a public and a private IP, it must still be
        # blocked — partial trust is no trust.
        infos = _addrinfo("93.184.216.34") + _addrinfo("169.254.169.254")
        with mock.patch.object(security.socket, "getaddrinfo", return_value=infos):
            with self.assertRaises(SecurityError):
                _assert_public_host("split-horizon.example")

    def test_unresolvable_host_blocked(self):
        with mock.patch.object(security.socket, "getaddrinfo",
                               side_effect=security.socket.gaierror("no such host")):
            with self.assertRaises(SecurityError):
                _assert_public_host("nope.invalid")

    def test_no_addresses_blocked(self):
        with mock.patch.object(security.socket, "getaddrinfo", return_value=[]):
            with self.assertRaises(SecurityError):
                _assert_public_host("empty.example")


# --------------------------------------------------------------------------- #
#  Request budget, size caps — exercised through _fetch with DNS/opener stubbed #
# --------------------------------------------------------------------------- #
class TestFetchGuards(unittest.TestCase):
    def setUp(self):
        # Neutralise DNS so _validate_url passes on the allowlisted URL; tests
        # that need the network path supply their own _FakeOpener.
        self._dns = mock.patch.object(
            security, "_assert_public_host", lambda host: None)
        self._dns.start()
        self.addCleanup(self._dns.stop)

    def test_request_budget_enforced(self):
        client = SafeHTTPClient()
        client._opener = _FakeOpener(b"ok")
        client._count = MAX_REQUESTS_PER_RUN     # next call is one over the ceiling
        with self.assertRaises(SecurityError):
            client._fetch(_ALLOWED_URL, None, "text/plain")

    def test_oversize_compressed_body_rejected(self):
        with mock.patch.object(security, "MAX_BYTES", 8):
            client = SafeHTTPClient()
            client._opener = _FakeOpener(b"x" * 9)   # one byte past the cap
            with self.assertRaises(SecurityError):
                client._fetch(_ALLOWED_URL, None, "text/plain")

    def test_body_at_cap_is_allowed(self):
        with mock.patch.object(security, "MAX_BYTES", 8):
            client = SafeHTTPClient()
            client._opener = _FakeOpener(b"x" * 8)   # exactly at the cap
            _host, body = client._fetch(_ALLOWED_URL, None, "text/plain")
            self.assertEqual(body, b"x" * 8)

    def test_count_increments_per_call(self):
        client = SafeHTTPClient()
        client._opener = _FakeOpener(b"ok")
        self.assertEqual(client.requests_made, 0)
        client._fetch(_ALLOWED_URL, None, "text/plain")
        client._fetch(_ALLOWED_URL, None, "text/plain")
        self.assertEqual(client.requests_made, 2)


class TestRecordReplay(unittest.TestCase):
    """Offline reproducibility: a networked run can be RECORDED to disk and then
    REPLAYED with no network, returning byte-identical responses. The
    allowlist/HTTPS invariant still holds on replay, and a missing fixture is a
    loud error, never a silent empty body."""

    def setUp(self):
        import tempfile
        self._dns = mock.patch.object(security, "_assert_public_host", lambda h: None)
        self._dns.start()
        self.addCleanup(self._dns.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def test_record_then_replay_roundtrips_without_network(self):
        # Record via the fake opener (no real network), then replay from disk
        # with an opener that would EXPLODE if touched — proving replay is offline.
        rec = SafeHTTPClient(record_dir=self.dir)
        rec._opener = _FakeOpener(b'{"hello":"world"}')
        out = rec.get_json(_ALLOWED_URL, {"b": "2", "a": "1"})
        self.assertEqual(out, {"hello": "world"})

        rep = SafeHTTPClient(replay_dir=self.dir)
        def _boom(*a, **k):
            raise AssertionError("replay must not hit the network")
        rep._opener = mock.Mock(open=_boom)
        # param order differs from how it was recorded — canonical key still hits
        self.assertEqual(rep.get_json(_ALLOWED_URL, {"a": "1", "b": "2"}),
                         {"hello": "world"})

    def test_missing_fixture_raises(self):
        rep = SafeHTTPClient(replay_dir=self.dir)
        with self.assertRaises(SecurityError):
            rep.get_text(_ALLOWED_URL, {"x": "1"})

    def test_replay_enforces_allowlist_offline(self):
        rep = SafeHTTPClient(replay_dir=self.dir)
        with self.assertRaises(SecurityError):
            rep._fetch("https://evil.example/x", None, "text/plain")
        with self.assertRaises(SecurityError):
            rep._fetch("http://api.open-meteo.com/x", None, "text/plain")

    def test_index_written_for_audit(self):
        import json as _json
        from pathlib import Path
        rec = SafeHTTPClient(record_dir=self.dir)
        rec._opener = _FakeOpener(b"csv,body")
        rec.get_text(_ALLOWED_URL, {"q": "z"})
        idx = _json.loads((Path(self.dir) / "_index.json").read_text())
        self.assertEqual(len(idx), 1)
        (entry,) = idx.values()
        self.assertIn("api.open-meteo.com", entry["url"])
        self.assertEqual(entry["bytes"], len(b"csv,body"))

    def test_canonical_url_is_param_order_independent(self):
        a = security._fixture_key(_ALLOWED_URL, {"a": "1", "b": "2"}, "x")
        b = security._fixture_key(_ALLOWED_URL, {"b": "2", "a": "1"}, "x")
        self.assertEqual(a, b)
        self.assertNotEqual(
            a, security._fixture_key(_ALLOWED_URL, {"a": "1", "b": "2"}, "y"))


class TestPinToday(unittest.TestCase):
    """pin_today makes place_today deterministic so a recorded run replays to the
    same date-parametrised requests on any later calendar day."""

    def test_pin_and_clear(self):
        import datetime as dt
        from weather_council.sources import Place, pin_today, place_today
        p = Place("X", "XX", 0.0, 0.0, "Asia/Hong_Kong")
        try:
            pin_today(dt.date(2026, 6, 17))
            self.assertEqual(place_today(p), dt.date(2026, 6, 17))
        finally:
            pin_today(None)
        # cleared -> live clock again (just assert it's a real date, not the pin)
        self.assertIsInstance(place_today(p), dt.date)


# --------------------------------------------------------------------------- #
#  JSON / array / text / gzip shape + decompression-bomb guard                  #
# --------------------------------------------------------------------------- #
class TestBodyParsing(unittest.TestCase):
    def _client(self, body: bytes) -> SafeHTTPClient:
        client = SafeHTTPClient()
        # Stub _fetch itself: parsing logic is independent of transport.
        client._fetch = lambda url, params, accept: ("api.open-meteo.com", body)
        return client

    def test_get_json_returns_object(self):
        self.assertEqual(self._client(b'{"a": 1}').get_json(_ALLOWED_URL, {}), {"a": 1})

    def test_get_json_rejects_array(self):
        with self.assertRaises(SecurityError):
            self._client(b"[1, 2, 3]").get_json(_ALLOWED_URL, {})

    def test_get_json_rejects_garbage(self):
        with self.assertRaises(SecurityError):
            self._client(b"not json").get_json(_ALLOWED_URL, {})

    def test_get_json_array_returns_list(self):
        self.assertEqual(self._client(b"[1, 2]").get_json_array(_ALLOWED_URL), [1, 2])

    def test_get_json_array_rejects_object(self):
        with self.assertRaises(SecurityError):
            self._client(b'{"a": 1}').get_json_array(_ALLOWED_URL)

    def test_get_text_decodes_utf8(self):
        self.assertEqual(self._client("café\n".encode()).get_text(_ALLOWED_URL), "café\n")

    def test_get_text_rejects_bad_utf8(self):
        with self.assertRaises(SecurityError):
            self._client(b"\xff\xfe\x00bad").get_text(_ALLOWED_URL)

    def test_get_gzip_text_roundtrips(self):
        payload = "date,tmax\n2026-06-07,19.0\n"
        self.assertEqual(
            self._client(gzip.compress(payload.encode())).get_gzip_text(_ALLOWED_URL),
            payload)

    def test_gzip_bomb_rejected_on_decompressed_size(self):
        # Small compressed body that inflates past a (shrunken) decompressed cap.
        big = gzip.compress(b"A" * 5000)
        with mock.patch.object(security, "MAX_DECOMPRESSED", 1000):
            with self.assertRaises(SecurityError):
                self._client(big).get_gzip_text(_ALLOWED_URL)


# --------------------------------------------------------------------------- #
#  Redirect guard — every hop is re-validated                                   #
# --------------------------------------------------------------------------- #
class TestRedirectGuard(unittest.TestCase):
    def test_redirect_to_off_allowlist_or_plaintext_blocked(self):
        handler = _GuardedRedirectHandler()
        for newurl in ["http://api.open-meteo.com/x",       # downgrade to plaintext
                       "https://evil.example.com/x",         # off-allowlist host
                       "https://169.254.169.254/latest"]:    # cloud metadata host
            with mock.patch.object(security.socket, "getaddrinfo",
                                   return_value=_addrinfo("169.254.169.254")):
                with self.assertRaises(SecurityError):
                    handler.redirect_request(
                        req=None, fp=None, code=302, msg="Found",
                        headers={}, newurl=newurl)


# --------------------------------------------------------------------------- #
#  Retry/backoff helper                                                         #
# --------------------------------------------------------------------------- #
class TestRetryAfter(unittest.TestCase):
    def test_numeric_retry_after_honored_and_capped(self):
        self.assertEqual(_retry_after_seconds({"Retry-After": "2"}, 0), 2.0)
        self.assertEqual(_retry_after_seconds({"Retry-After": "999"}, 0),
                         security.BACKOFF_CAP)

    def test_negative_retry_after_floored_to_zero(self):
        self.assertEqual(_retry_after_seconds({"Retry-After": "-5"}, 0), 0.0)

    def test_missing_or_bad_header_falls_back_to_exponential(self):
        self.assertEqual(_retry_after_seconds({}, 0), security.BACKOFF_BASE)
        self.assertEqual(_retry_after_seconds(None, 1), security.BACKOFF_BASE * 2)
        self.assertEqual(_retry_after_seconds({"Retry-After": "soon"}, 0),
                         security.BACKOFF_BASE)

    def test_exponential_backoff_capped(self):
        self.assertEqual(_retry_after_seconds({}, 99), security.BACKOFF_CAP)


class TestErrorHierarchy(unittest.TestCase):
    def test_rate_limit_error_is_a_security_error(self):
        # Callers doing `except SecurityError` must still catch a throttle.
        self.assertTrue(issubclass(RateLimitError, SecurityError))


if __name__ == "__main__":
    unittest.main()
