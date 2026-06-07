#!/usr/bin/env python3
"""Local web interface for the weather council.

Serves a single-page UI and a JSON API that runs the backend council on
demand. Bound to localhost only; the outbound side is still governed by the
sandbox in weather_council.security, and each request gets a fresh request
budget. No new dependencies — Python stdlib only.

  GET /                       -> the interface (index.html)
  GET /api/verdict?city=&date=&window=  -> full verdict + evidence as JSON

Run:  python3 server.py   then open http://127.0.0.1:8765
"""

from __future__ import annotations

import datetime as dt
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from run import (_build_comparison, verdict_to_dict,
                 _settlement_reference, _anchor_cross_reference)
from weather_council.council import Council
from weather_council.security import RateLimitError, SecurityError
from weather_council.sources import Sources

HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8765"))
HERE = Path(__file__).resolve().parent
INDEX = HERE / "index.html"
MAX_LEAD = 15
MIN_WINDOW, MAX_WINDOW = 15, 365


def _run_verdict(city: str, date_s: str, window_s: str, with_market: bool = False) -> dict:
    sources = Sources()
    place = sources.geocode(city)            # also validates the city name

    today = dt.date.today()
    if date_s:
        try:
            target = dt.date.fromisoformat(date_s)
        except ValueError:
            raise SecurityError("date must be in YYYY-MM-DD format")
    else:
        target = today + dt.timedelta(days=1)
    lead = (target - today).days
    if not (0 <= lead <= MAX_LEAD):
        raise SecurityError(f"date must be between today and {MAX_LEAD} days ahead")

    try:
        window = int(window_s) if window_s else 60
    except ValueError:
        raise SecurityError("window must be an integer")
    window = max(MIN_WINDOW, min(MAX_WINDOW, window))

    verdict = Council(sources).deliberate(place, target, window)
    comparison = None
    market_note = None
    if with_market:
        comparison, market_note = _build_comparison(sources, verdict, place, target)
    settlement_ref = _settlement_reference(sources, place, target, verdict)
    cross_reference = _anchor_cross_reference(sources, place, target, verdict)
    return verdict_to_dict(verdict, comparison, market_note,
                           settlement_ref, cross_reference)


class Handler(BaseHTTPRequestHandler):
    server_version = "WeatherCouncil/1.0"

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            try:
                self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, b"index.html not found", "text/plain")
            return

        if parsed.path == "/api/verdict":
            qs = parse_qs(parsed.query)
            city = (qs.get("city", [""])[0]).strip()
            date_s = qs.get("date", [""])[0].strip()
            window_s = qs.get("window", [""])[0].strip()
            with_market = qs.get("market", [""])[0].strip().lower() in ("1", "true", "yes")
            try:
                data = _run_verdict(city, date_s, window_s, with_market)
                self._send(200, json.dumps(data).encode(), "application/json")
            except RateLimitError as exc:
                # Transient upstream throttle — distinct from a sandbox/validation
                # rejection, so 503 (retryable) rather than 400 (client error).
                self._send(503, json.dumps(
                    {"error": str(exc), "retryable": True}).encode(),
                    "application/json")
            except SecurityError as exc:
                self._send(400, json.dumps({"error": str(exc)}).encode(), "application/json")
            except Exception as exc:
                self._send(500, json.dumps({"error": str(exc)}).encode(), "application/json")
            return

        self._send(404, b"not found", "text/plain")

    def log_message(self, *_args) -> None:  # keep the console quiet
        pass


def main() -> None:
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Weather Council UI -> http://{HOST}:{PORT}  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
