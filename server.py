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
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from run import (_build_comparison, verdict_to_dict,
                 _settlement_reference, _anchor_cross_reference)
from weather_council.council import Council
from weather_council.edge import report_lines as edge_report_lines, score_snapshots
from weather_council.loop import Experiment, gate_deploy
from weather_council.security import RateLimitError, SecurityError
from weather_council.sources import Sources, place_today
from weather_council.storage import (fetch_settled_snapshots,
                                     settle_market_snapshots, verify)

HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8765"))
HERE = Path(__file__).resolve().parent
INDEX = HERE / "index.html"
STATUS = HERE / "reports" / "healthcheck_status.json"
STALE_HOURS = 36.0           # > ~1.5 daily cycles ⇒ the scheduled check missed a run
MAX_LEAD = 15
MIN_WINDOW, MAX_WINDOW = 15, 365

# Serialize verdict runs: each one issues dozens of outbound requests and can
# exhaust the keyless WU request budget if overlapping. Queue rather than fail:
# a saturated semaphore returns 503 with retryable=true so the UI can poll.
_VERDICT_LOCK = threading.Semaphore(1)


def _run_verdict(city: str, date_s: str, window_s: str, with_market: bool = False) -> dict:
    sources = Sources()
    place = sources.geocode(city)            # also validates the city name

    # City-local "today" — the same anchor run.py uses. Fixes the UTC-1 host bug
    # where a Singapore-evening request computed tomorrow from the wrong date.
    today = place_today(place)
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


def _run_verify() -> dict:
    """Score logged verdicts whose day has settled against the SAME anchored truth
    they were issued on — the web-app twin of `run.py --verify`. State-changing
    (fills realized columns) and network-bound, so it's a POST. Returns the
    per-verdict settlement notes; an empty list means nothing is ready yet."""
    lines = verify()
    return {"lines": lines, "count": len(lines)}


def _run_edge() -> dict:
    """Settle logged market snapshots against their anchor station, then score the
    C7 council-vs-market realized-outcome edge — the web-app twin of `run.py
    --edge`. Read/recommend-only: it grades, it never trades. State-changing
    (settles rows) and network-bound, so it's a POST."""
    settled = settle_market_snapshots()
    report = score_snapshots(fetch_settled_snapshots())
    return {"settled": settled, "report": edge_report_lines(report)}


def _load_status() -> dict:
    """System-state feed for the UI: the daily health check's machine-readable
    status PLUS the loop's deploy gate, run live over the health check's own
    realized-edge determination.

    Read fresh on every request — the file is rewritten by the scheduled health
    check, so the feed is genuinely live, not cached. The loop action is COMPUTED
    here by ``gate_deploy`` (never a hard-coded string): it consumes the health
    check's persisted ``c7_validated`` and a ``human_signoff`` the server NEVER
    asserts, so the gate reports the real reason the council stays recommend-only.
    No autonomous path can flip this to LIVE.
    """
    try:
        hc = json.loads(STATUS.read_text())
    except OSError:
        return {"available": False,
                "reason": "no health check has run yet "
                          "(reports/healthcheck_status.json absent)"}
    except ValueError as exc:
        return {"available": False, "reason": f"status file unreadable: {exc}"}

    try:                                  # staleness from the file's own mtime
        age_h = (dt.datetime.now().timestamp() - STATUS.stat().st_mtime) / 3600.0
    except OSError:
        age_h = None

    c7 = bool(hc.get("c7_validated", False))
    exp = Experiment(id="council-live", hypothesis="(deployed council)",
                     c7_validated=c7, human_signoff=False)
    gate = gate_deploy(exp)
    action = "LIVE" if gate.reason.startswith("LIVE") else "RECOMMEND_ONLY"
    return {
        "available": True,
        "healthcheck": hc,
        "age_hours": (round(age_h, 1) if age_h is not None else None),
        "stale": (age_h is not None and age_h > STALE_HOURS),
        "loop": {
            "action": action,
            "reason": gate.reason,
            "c7_validated": c7,
            "human_signoff": False,
            # The hard boundary, surfaced as a guarantee — all three MUST stay
            # False; the loop's risk gate rejects any experiment that flips them.
            "invariants": {
                "places_trades": exp.places_trades,
                "moves_funds": exp.moves_funds,
                "autonomous_code_edit": exp.autonomous_code_edit,
            },
        },
    }


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

        if parsed.path == "/api/status":
            try:
                self._send(200, json.dumps(_load_status()).encode(),
                           "application/json")
            except Exception as exc:
                self._send(500, json.dumps({"error": str(exc)}).encode(),
                           "application/json")
            return

        if parsed.path == "/api/verdict":
            qs = parse_qs(parsed.query)
            city = (qs.get("city", [""])[0]).strip()
            date_s = qs.get("date", [""])[0].strip()
            window_s = qs.get("window", [""])[0].strip()
            with_market = qs.get("market", [""])[0].strip().lower() in ("1", "true", "yes")

            # Serialize verdict runs: each is network-heavy and hammers the single
            # keyless WU budget. Saturated requests get a retryable 503.
            if not _VERDICT_LOCK.acquire(blocking=False):
                self._send(503, json.dumps(
                    {"error": "another verdict run is in progress",
                     "retryable": True}).encode(), "application/json")
                return
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
            finally:
                _VERDICT_LOCK.release()
            return

        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        # State-changing, recommend-only ledger operations live behind POST so they
        # are never triggered by a page load or a stray GET — only an explicit click.
        routes = {"/api/verify": _run_verify, "/api/edge": _run_edge}
        op = routes.get(urlparse(self.path).path)
        if op is None:
            self._send(404, b"not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)                # drain any body, keep socket clean
        try:
            self._send(200, json.dumps(op()).encode(), "application/json")
        except RateLimitError as exc:
            self._send(503, json.dumps(
                {"error": str(exc), "retryable": True}).encode(), "application/json")
        except SecurityError as exc:
            self._send(400, json.dumps({"error": str(exc)}).encode(), "application/json")
        except Exception as exc:
            self._send(500, json.dumps({"error": str(exc)}).encode(), "application/json")

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
