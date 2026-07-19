"""Strict network sandbox for the weather council.

Every outbound request goes through SafeHTTPClient, which enforces a tight
security band so the agent cannot be turned into an exfiltration or SSRF
vector even if a downstream string were ever attacker-influenced:

  * HTTPS only — no plaintext, no file:// or other schemes.
  * Host allowlist — only the Open-Meteo endpoints, the Meteostat bulk
    station archive (the sanctioned station-observation truth source), the
    Iowa Environmental Mesonet ASOS archive (raw airport METAR — the source the
    settlement record itself derives from), and the Polymarket Gamma API
    (read-only public market prices, for verdict-vs-market comparison only),
    nothing else.
  * SSRF guard — the host must resolve exclusively to public IPs; any
    private/loopback/link-local/reserved address aborts the request.
    DNS-rebinding TOCTOU: the check resolves once and the TLS connection
    resolves again; pinning the IP would close the gap but is accepted risk
    given the read-only, no-credential, major-public-provider allowlist.
  * Response cap — compressed bodies over MAX_BYTES are rejected before being
    read into memory; gzip payloads are additionally bounded on the
    *decompressed* size (MAX_DECOMPRESSED) so a zip bomb cannot exhaust memory.
  * Timeouts — every socket has a hard deadline.
  * Rate cap — a per-run ceiling on total requests.
  * No code execution — responses are parsed as JSON/CSV text only; never eval'd.

Input that originates from the user (the city name) is validated separately
by validate_city before it is ever placed in a query string.
"""

from __future__ import annotations

__all__ = [
    'SecurityError', 'RateLimitError', 'validate_city', 'SafeHTTPClient'
]

import ipaddress
import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

# The only hosts this program is ever allowed to contact.
ALLOWED_HOSTS = frozenset({
    "geocoding-api.open-meteo.com",
    "api.open-meteo.com",
    "historical-forecast-api.open-meteo.com",
    "archive-api.open-meteo.com",
    "ensemble-api.open-meteo.com",
    # Meteostat bulk archive: real station daily observations (the truth a
    # record/market settles on) with decades of history. Keyless, HTTPS, gzip.
    "bulk.meteostat.net",
    # Iowa Environmental Mesonet ASOS archive: raw airport METAR observations
    # in their native reporting unit — the feed Weather Underground (and thus
    # prediction-market settlement) ultimately reads. Keyless, HTTPS, CSV.
    "mesonet.agron.iastate.edu",
    # Polymarket Gamma Markets API: public, keyless market metadata and prices.
    # READ-ONLY use only — ingested to compare the model verdict against the
    # market's implied probability. No order placement or funds ever touch this.
    "gamma-api.polymarket.com",
    # Polymarket CLOB market-data API (clob.polymarket.com): the PUBLIC, keyless
    # order-book endpoints — /book, /midpoint, POST /books — that expose live bid/ask
    # levels and sizes per token. READ-ONLY, ingested only to ARCHIVE the book at the
    # same instant as the price snapshot, so executable depth-walk P&L can be measured
    # against the theoretical mid-price. It is a market-DATA host: no authenticated
    # trading endpoint is ever called, no order is placed, no key or signature is sent,
    # and no funds ever touch this. (Trading would use a different signed API surface
    # that is deliberately NOT allowlisted.)
    "clob.polymarket.com",
    # Hong Kong Observatory official open data (data.weather.gov.hk): keyless,
    # HTTPS, CSV/JSON daily climate records straight from the Observatory. The
    # Meteostat archive for the HKO station ends in 1992, far too old to measure
    # a *current* settlement-vs-airport offset; this is the only source of the
    # recent HKO daily record, used solely to make that offset modern. Read-only.
    "data.weather.gov.hk",
    # Hong Kong Observatory official Daily Extract (www.hko.gov.hk): the same
    # Observatory, served as the per-month "Daily Extract of Meteorological
    # Observations" whose "Absolute Daily Maximum Air Temperature" column IS the
    # figure the Hong Kong market settles on. Keyless, HTTPS, JSON. It publishes
    # the prior day within ~a day — weeks fresher than the monthly open-data
    # climate API above — so it supplies the most recent settled days. Read-only.
    "www.hko.gov.hk",
    # Japan Meteorological Agency `bosai` typhoon feed (www.jma.go.jp): keyless,
    # HTTPS, JSON. The same machine-readable active-TC list and per-storm forecast
    # tracks that drive the official JMA typhoon site. Read-only, consumed solely
    # by the tropical-cyclone halt gate (weather_council/tc_gate.py) to ABSTAIN on
    # the Hong Kong verdict when a TC's 5-day forecast cone threatens HK — a risk
    # control, never a forecast input. No funds or writes ever touch this.
    "www.jma.go.jp",
    # The Weather Company / Wunderground backend (api.weather.com): the EXACT
    # source the Polymarket city markets name as their resolution oracle ("highest
    # temperature recorded ... at the {airport} Station ... from Wunderground").
    # Verified to differ from the IEM METAR at °F/°C boundaries: Wunderground stores
    # the station record in WHOLE °F and the contract converts that to whole °C, so
    # e.g. a true 30.4°C reads 30°C on IEM (whole-°C METAR) but 87°F→30.6→31°C on
    # Wunderground — a one-bucket gap exactly where it matters. So this is the
    # settlement-grade ANCHOR for those markets; IEM stays a cross-reference. Keyless
    # to the site's public web API key (the same key the wunderground.com history
    # page uses), carried in request PARAMS, read-only.
    "api.weather.com",
    # Weatherbit daily forecast API: a non-Open-Meteo forecaster. It exposes NO
    # free archive of its PAST forecasts, so — unlike the council members — its
    # skill cannot be backtested instantly; it is added as a RECOMMEND-ONLY
    # tracked forecaster (logged and scored prospectively, never voted into the
    # live blend until it earns history). Keyed: the API key rides in the request
    # PARAMS (never a logged URL) and is read from WEATHERBIT_API_KEY at runtime.
    "api.weatherbit.io",
    # Kalshi PUBLIC market-data API (api.elections.kalshi.com): keyless, HTTPS,
    # JSON. USER-APPROVED 2026-07-13 as S0 of the registered Kalshi weather-market
    # expansion (ledger/preregistered/kalshi_expansion.md — read it before touching
    # this host). READ-ONLY market data (series/events/markets/orderbooks) for the
    # KXHIGH*/KXLOW* daily-temperature families, ingested only to ARCHIVE
    # point-in-time ladders exactly as the Polymarket hosts above are. NO trading
    # endpoint is ever called, no key, no signature, no account, no funds — the
    # authenticated trading surface is deliberately NOT allowlisted, and adding it
    # would require its own registration and explicit user instruction.
    "api.elections.kalshi.com",
    # Kalshi public contract-terms documents (kalshi-public-docs.s3.amazonaws.com):
    # keyless, HTTPS, PDF. Same S0 approval. Read-only, consumed solely to PIN each
    # market's settlement station/rules at seam-registration time (S1) — the
    # station-identity lesson (NYC settles on Central Park KNYC, not an airport)
    # makes each contract's terms the only trustworthy source.
    "kalshi-public-docs.s3.amazonaws.com",
    # Polymarket public trade-history API (data-api.polymarket.com): keyless, HTTPS,
    # JSON. USER-APPROVED 2026-07-14 for the Polymarket trade-tape kill test
    # (ledger/preregistered/polymarket_tape_kill_test.md — read it before touching this
    # host). READ-ONLY executed-trade history by market condition id (side/price/size/
    # timestamp/outcome), verified public pre-approval. Same posture as the other market
    # hosts: no order placement, no key, no signature, no funds — ever.
    "data-api.polymarket.com",
})

MAX_BYTES = 8 * 1024 * 1024          # 8 MiB ceiling on a *compressed* body
MAX_DECOMPRESSED = 64 * 1024 * 1024  # 64 MiB ceiling after gunzip (zip-bomb guard)
DEFAULT_TIMEOUT = 30                 # seconds per request
MAX_REQUESTS_PER_RUN = 64            # ceiling on total outbound calls

# Transient upstream conditions worth a bounded retry rather than failing the
# whole member. 429 = the keyless Open-Meteo archive throttling a burst; 502/503/
# 504 = a momentary gateway/upstream hiccup. A retry does NOT consume another unit
# of the per-run request budget (the budget bounds distinct logical calls, not the
# transport's redelivery of one of them) — only wall-clock, which the cap bounds.
RETRYABLE_STATUS = frozenset({429, 502, 503, 504})
MAX_RETRIES = 3                      # attempts AFTER the first try (so ≤4 total)
BACKOFF_BASE = 1.0                   # seconds; doubled each attempt
BACKOFF_CAP = 20.0                   # ceiling on any single backoff sleep

# City names: letters (incl. accented), spaces, and a few separators only.
_CITY_RE = re.compile(r"^[\wÀ-ɏ .,'\-]{1,80}$", re.UNICODE)


class SecurityError(Exception):
    """Raised when a request would violate the sandbox policy."""


class RateLimitError(SecurityError):
    """Raised when an allowlisted host keeps returning a retryable status
    (e.g. HTTP 429) after the bounded retry budget is spent. Subclasses
    SecurityError so every existing ``except SecurityError`` site still fails
    closed, but callers that want to distinguish a *transient throttle* ("try
    again shortly") from genuinely-absent data can catch this specifically."""


def _retry_after_seconds(headers, attempt: int) -> float:
    """How long to wait before the next retry. Honors a numeric Retry-After
    header when the host sends one (capped), else exponential backoff."""
    raw = headers.get("Retry-After") if headers else None
    if raw:
        try:
            return min(BACKOFF_CAP, max(0.0, float(raw)))
        except (TypeError, ValueError):
            pass  # HTTP-date form is rare here; fall back to computed backoff
    return min(BACKOFF_CAP, BACKOFF_BASE * (2 ** attempt))


def validate_city(raw: str) -> str:
    """Sanitize and bound the one piece of user-controlled input."""
    name = (raw or "").strip()
    if not name or not _CITY_RE.match(name):
        raise SecurityError(
            "invalid city name: use letters, spaces, and . , ' - only (max 80 chars)"
        )
    return name


def _build_ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _assert_public_host(host: str) -> None:
    """Resolve the host and refuse if any address is non-public (SSRF guard).

    NOTE — accepted DNS-rebinding TOCTOU: this check resolves the hostname once;
    the TLS connection resolves it again at connect time. A hostile allowlisted
    host (or a compromised resolver) could answer public IPs here and a
    private/loopback address when the connection is opened. That gap is accepted
    under this project's threat model: allowlisted hosts are major public
    providers, the client runs read-only against them, no credentials are stored
    or sent beyond the public WU/Weatherbit API keys, and the UI binds
    localhost only. Pinning the IP and connecting with a manual Host/SNI is
    stdlib-possible but brittle; it is not justified here.
    """
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SecurityError(f"cannot resolve host {host!r}: {exc}") from exc
    if not infos:
        raise SecurityError(f"host {host!r} resolved to no addresses")
    for *_, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError as exc:
            # A scoped IPv6 literal ("fe80::1%en0") is unparseable — and link-local
            # is exactly what this guard exists to block. Fail closed, not crash.
            raise SecurityError(
                f"host {host!r} resolved to unparseable address {sockaddr[0]!r} — blocked"
            ) from exc
        if not ip.is_global or ip.is_multicast:
            raise SecurityError(
                f"host {host!r} resolves to non-public address {ip} — blocked"
            )


def _validate_url(url: str) -> str:
    """Apply the full sandbox URL policy and return the hostname: HTTPS only,
    host on the allowlist, and the host resolves exclusively to public IPs
    (SSRF guard). Applied to the initial request AND to every redirect hop, so a
    3xx from a trusted host cannot bounce the client onto an off-allowlist host
    or a private/loopback/cloud-metadata address."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise SecurityError(f"refusing non-https URL: {url!r}")
    if parts.hostname not in ALLOWED_HOSTS:
        raise SecurityError(f"host not in allowlist: {parts.hostname!r}")
    # The allowlist names a HOST, not a netloc: _fetch/redirects rebuild the URL
    # from the original netloc, so without these checks `host:8443` (arbitrary
    # port on a trusted name) and `user:pass@host` (credential smuggling /
    # request ambiguity) both validate and get fetched.
    try:
        port_ok = parts.port in (None, 443)
    except ValueError:                       # non-numeric port in the netloc
        port_ok = False
    if not port_ok:
        raise SecurityError(f"refusing non-443 port: {url!r}")
    if parts.username is not None or parts.password is not None:
        raise SecurityError(f"refusing userinfo in URL: {url!r}")
    _assert_public_host(parts.hostname)
    return parts.hostname


class _GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-run the full URL policy on every redirect target before following it.

    A bare urlopen silently follows 3xx responses, so a compromised or
    open-redirecting allowlisted host could bounce the client to a private IP
    (e.g. cloud metadata) or an off-allowlist host — escaping the SSRF and
    allowlist guards that only ever saw the initial URL. Validating each hop
    closes that gap; a disallowed redirect raises instead of being followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_url(newurl)        # raises SecurityError on a disallowed hop
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class SafeHTTPClient:
    """A minimal HTTP/JSON client locked to the sandbox policy."""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self._ctx = _build_ssl_context()
        self._timeout = timeout
        self._count = 0
        # A minimal, HTTPS-only opener built by hand rather than the bare
        # urlopen default. We register ONLY the handlers we want:
        #   * HTTPSHandler bound to our verified (certifi) TLS context;
        #   * a redirect handler that re-validates every hop against the
        #     allowlist/SSRF policy;
        #   * the error processor + default error handler so non-2xx and 3xx
        #     responses are routed/raised correctly.
        # Deliberately absent: HTTPHandler (no plaintext), File/FTP/Data handlers
        # (no local-file, ftp, or data: scheme reachable even via a redirect),
        # and any ProxyHandler (HTTP(S)_PROXY env vars cannot reroute traffic
        # past the SSRF check). Anything outside this set fails closed.
        self._opener = urllib.request.OpenerDirector()
        self._opener.add_handler(urllib.request.HTTPSHandler(context=self._ctx))
        self._opener.add_handler(_GuardedRedirectHandler())
        self._opener.add_handler(urllib.request.HTTPErrorProcessor())
        self._opener.add_handler(urllib.request.HTTPDefaultErrorHandler())

    def _fetch(self, base_url: str, params: dict | None, accept: str) -> tuple[str, bytes]:
        """Apply the full sandbox policy and return (hostname, capped body).
        Shared by the JSON and gzip fetchers so the guard can never be bypassed
        by adding a second entrypoint."""
        parts = urllib.parse.urlsplit(base_url)
        host = _validate_url(base_url)   # https + allowlist + SSRF; returns hostname

        self._count += 1
        if self._count > MAX_REQUESTS_PER_RUN:
            raise SecurityError("request budget exceeded for this run")

        query = urllib.parse.urlencode(params or {}, doseq=True)
        url = urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, query, "")
        )
        req = urllib.request.Request(
            url, headers={"User-Agent": "weather-council/1.0", "Accept": accept}
        )
        # Hardened opener (re-validating redirects, no env proxy) rather than a
        # bare urlopen, so every hop stays inside the sandbox policy. Retryable
        # upstream statuses (throttling, transient gateway errors) get a bounded
        # backoff so one member is not starved by a momentary 429 — the failure
        # mode that previously cascaded into "no eligible member" when every
        # member hit the same throttled endpoint at once.
        attempt = 0
        while True:
            try:
                with self._opener.open(req, timeout=self._timeout) as resp:
                    # Read one byte past the cap to detect oversize bodies.
                    body = resp.read(MAX_BYTES + 1)
                break
            except SecurityError:
                raise
            except urllib.error.HTTPError as exc:
                if exc.code in RETRYABLE_STATUS and attempt < MAX_RETRIES:
                    exc.close()      # release the response socket before retrying
                    time.sleep(_retry_after_seconds(exc.headers, attempt))
                    attempt += 1
                    continue
                if exc.code in RETRYABLE_STATUS:
                    raise RateLimitError(
                        f"{host} rate-limited (HTTP {exc.code}) after "
                        f"{attempt} retr{'y' if attempt == 1 else 'ies'}"
                    ) from exc
                raise SecurityError(f"request to {host} failed: {exc}") from exc
            except Exception as exc:
                raise SecurityError(f"request to {host} failed: {exc}") from exc

        if len(body) > MAX_BYTES:
            raise SecurityError("response exceeded size cap — aborting")
        return parts.hostname, body

    def get_json(self, base_url: str, params: dict) -> dict:
        host, body = self._fetch(base_url, params, "application/json")
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise SecurityError(f"response from {host} was not valid JSON") from exc
        if not isinstance(data, dict):
            raise SecurityError("response JSON was not an object")
        return data

    def get_json_array(self, base_url: str, params: dict | None = None) -> list:
        """Like get_json but for endpoints whose top-level body is a JSON array
        (e.g. the Gamma /events feed). Rejects anything that is not a list."""
        host, body = self._fetch(base_url, params, "application/json")
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise SecurityError(f"response from {host} was not valid JSON") from exc
        if not isinstance(data, list):
            raise SecurityError("response JSON was not an array")
        return data

    def get_text(self, base_url: str, params: dict | None = None) -> str:
        """Fetch a plain-text (uncompressed) body and return it decoded. Bounded
        by the same MAX_BYTES cap applied in _fetch (the body is read raw)."""
        host, body = self._fetch(base_url, params, "text/plain")
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SecurityError(f"response from {host} was not valid UTF-8 text") from exc

    def get_gzip_text(self, base_url: str, params: dict | None = None) -> str:
        """Fetch a gzip-compressed body and return its decoded text, bounding the
        *decompressed* size so a small payload cannot expand into a memory bomb."""
        _host, body = self._fetch(base_url, params, "application/gzip")
        out = bytearray()
        dec = zlib.decompressobj(16 + zlib.MAX_WBITS)   # 16 => gzip header
        data = body
        try:
            while data:
                budget = MAX_DECOMPRESSED + 1 - len(out)
                if budget <= 0:
                    raise SecurityError("decompressed response exceeded size cap — aborting")
                out += dec.decompress(data, budget)
                data = dec.unconsumed_tail
            out += dec.flush()
        except zlib.error as exc:
            # A truncated/garbage body must fail closed inside the SecurityError
            # taxonomy like every other transport error, not crash the run raw.
            raise SecurityError(f"gzip body from {_host} was corrupt: {exc}") from exc
        if len(out) > MAX_DECOMPRESSED:
            raise SecurityError("decompressed response exceeded size cap — aborting")
        try:
            return out.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SecurityError("gzip body was not valid UTF-8 text") from exc

    @property
    def requests_made(self) -> int:
        return self._count
