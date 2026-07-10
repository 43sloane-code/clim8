"""Order-book capture (Phase 4): archive the live CLOB book beside a price snapshot.

The served model-vs-market comparison uses the theoretical MID price. You cannot trade at the
mid — you cross the spread and walk the book — so to ever measure EXECUTABLE P&L (paper_pnl,
Phase 5) we must archive what the order book actually looked like at the instant we snapshotted
the price. This module does exactly that and nothing more:

    for each bucket's YES token -> fetch the live /book (read-only) -> parse -> store depth stats
    + the full ladder into book_snapshots, at the SAME issued_at as the price snapshot.

It is READ-ONLY and additive: it reads public market data and writes only the book_snapshots
archive. It never places an order, sizes a position, prices a trade, or touches a served
probability. Per-token failure isolation: one token's dead/empty book is written as a fetch_ok=0
row (and recorded as a soft failure) so the batch continues and a silent gap is impossible.

SCOPE. Capture runs only for the actively-tracked settlement cities (FOCUS_CITIES). Other cities
still log price snapshots; they just do not get a book archive.

USAGE (capture the book for a city/day, sharing a fresh instant):
    PYTHONPATH=. python3 tools/book_logger.py "London" --lead 1
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from weather_council import storage                       # noqa: E402
from weather_council.clob_book import parse_book, book_stats  # noqa: E402
from weather_council.compare import match_market           # noqa: E402
from weather_council.failures import record_soft_failure  # noqa: E402
from weather_council.market import MarketData              # noqa: E402
from weather_council.sources import Sources               # noqa: E402

# The actively-captured settlement cities (user directive: Karachi, Jeddah, Singapore,
# London EGLC, San Francisco). Matched case-insensitively against the resolved place
# name. HKO is deliberately excluded. Keep this the single source of the capture scope.
# (Jakarta was dropped: Polymarket lists no Jakarta high-temperature market, so there
# is no settlement record to capture depth against.)
FOCUS_CITIES = frozenset({
    "karachi", "jeddah", "singapore", "london", "san francisco",
})


def in_focus(place_name: str) -> bool:
    """Is this city in the order-book capture scope? Case-insensitive substring match
    so "London" matches a resolved "London, United Kingdom"."""
    n = (place_name or "").strip().lower()
    return any(city in n for city in FOCUS_CITIES)


def capture_market_books(md: MarketData, place_label: str, target: str,
                         issued_at: str, buckets) -> dict:
    """Fetch + archive the YES-token book for every bucket of one market, at
    `issued_at`. `buckets` is an iterable of market.MarketBucket (each carries
    token_ids; token_ids[0] is the YES token — the one you buy to take the bucket).
    Per-token isolation: a fetch/parse failure becomes a fetch_ok=0 row, never an
    abort. Returns {"ok": n, "failed": n, "rows": n}. Writes nothing when there are
    no tokenised buckets (returns zeros)."""
    rows: list[dict] = []
    for b in buckets:
        token_ids = getattr(b, "token_ids", ()) or ()
        if not token_ids:
            continue                                   # untokenised bucket — nothing to fetch
        token_id = str(token_ids[0])                   # YES token
        label = getattr(b, "label", None)
        try:
            raw = md.fetch_order_book(token_id)
            if not raw:
                rows.append({"token_id": token_id, "bucket_label": label,
                             "fetch_ok": False, "error": "no book returned"})
                continue
            book = parse_book(raw)
            rows.append({"token_id": token_id, "bucket_label": label, "fetch_ok": True,
                         "stats": book_stats(book), "book_json": json.dumps(raw)})
        except Exception as exc:                       # one token must not kill the batch
            record_soft_failure("book_fetch", exc)
            rows.append({"token_id": token_id, "bucket_label": label,
                         "fetch_ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]})
    if not rows:
        return {"ok": 0, "failed": 0, "rows": 0}
    storage.log_book_snapshots(place_label, target, issued_at, rows)
    ok = sum(1 for r in rows if r["fetch_ok"])
    return {"ok": ok, "failed": len(rows) - ok, "rows": len(rows)}


def capture_for_market(md: MarketData, place_label: str, place_name: str,
                       target: str, market, issued_at: str) -> dict | None:
    """Capture the book for an already-resolved WeatherMarket, IF the city is in
    scope. Returns the capture summary, or None when out of scope / no market /
    no buckets. Read-only; safe to call right after log_market_snapshot with the
    issued_at that call returned."""
    if market is None or not in_focus(place_name):
        return None
    return capture_market_books(md, place_label, target, issued_at, market.buckets)


def capture_for_place(sources: Sources, place, target,
                      issued_at: str | None = None) -> dict | None:
    """Resolve today's high-temperature market for `place` and capture its book.
    Standalone entry point (the CLI and any caller without a market in hand). `target`
    may be a `datetime.date` OR an ISO date string — match_market needs a date while the
    archive stores the ISO string, so normalize once here (passing the string straight
    to match_market silently broke the match and captured nothing). Returns the capture
    summary, or None when out of scope / no matching market. Reuses the caller's Sources
    http (shared request budget)."""
    if not in_focus(place.name):
        return None
    target_date = target if isinstance(target, dt.date) else dt.date.fromisoformat(str(target))
    target_iso = target_date.isoformat()
    issued_at = issued_at or storage.utc_now_iso()
    md = MarketData(http=sources.http)
    market = match_market(md.fetch_temperature_markets(), place.name, target_date)
    return capture_for_market(md, place.label(), place.name, target_iso, market, issued_at)


def main() -> int:
    ap = argparse.ArgumentParser(description="Archive the live CLOB order book for a city/day.")
    ap.add_argument("city")
    ap.add_argument("--lead", type=int, default=1, help="days ahead of the city's today")
    args = ap.parse_args()

    sources = Sources()
    place = sources.geocode(args.city)
    if not in_focus(place.name):
        print(f"'{place.name}' is not in the order-book capture scope "
              f"({', '.join(sorted(FOCUS_CITIES))}); nothing captured.")
        return 0
    target = (dt.date.today() + dt.timedelta(days=args.lead)).isoformat()
    summary = capture_for_place(sources, place, target)
    if summary is None:
        print(f"no matching high-temperature market for {place.name} {target}; nothing captured.")
        return 0
    print(f"captured {place.name} {target}: {summary['ok']} book(s) OK, "
          f"{summary['failed']} failed ({summary['rows']} token rows).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
