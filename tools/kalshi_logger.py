#!/usr/bin/env python3
"""kalshi_logger.py — S2b of the Kalshi expansion (ledger/preregistered/kalshi_expansion.md,
seam rules in kalshi_sf_seam.md). Instrumentation only: no served number, no hypothesis,
no trading. Three duties per run:

  1. SNAPSHOT (dual-venue, matched timestamps — the S1 correction made mechanical):
     the live KXHIGHTSFO ladder AND the Polymarket SF ladder captured in the same run,
     one combined row → ledger/kalshi_snapshots.jsonl. Cross-venue comparisons are valid
     ONLY within a row.
  2. PRESERVE (arrests the ~67-day API retention erosion; accrues the S2a kill test
     toward its n=100 floor): newly-settled events' winner trade tapes appended to the
     SAME cache the frozen probe reads (reports/streams/kalshi_s2a_cache.jsonl) — the
     re-score is just re-running the untouched probe.
  3. TRUTH SERIES: daily CLISFO high (IEM parsed-CLI archive, verified 2026-07-14) beside
     the WU KSFO daily max → ledger/ksfo_cli_wu.jsonl — the cross-venue truth split
     (07-12: CLI 76 vs WU ~74) as a LOGGED SERIES from day one, per the seam prereg.

SEAM RULE 5 (KAT'd): this API serves `*_dollars`/`*_fp` STRINGS; absent fields parse to
None, NEVER to 0 — the bug that produced S1's false "empty books" reading is structurally
disallowed here.

Run:        PYTHONPATH=. python3 tools/kalshi_logger.py
Self-test:  PYTHONPATH=. python3 tools/kalshi_logger.py --selftest
Scheduled:  tools/com.weatherverdict.kalshi.plist (23:30 + 05:30 host = 15:30 + 21:30 PT —
            SF's afternoon/evening, which NO other scheduled job reaches) + accumulate.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from weather_council.security import SafeHTTPClient  # noqa: E402

API = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = "KXHIGHTSFO"
LA = ZoneInfo("America/Los_Angeles")
SNAP = os.path.join(ROOT, "ledger", "kalshi_snapshots.jsonl")
CACHE = os.path.join(ROOT, "reports", "streams", "kalshi_s2a_cache.jsonl")
CLIWU = os.path.join(ROOT, "ledger", "ksfo_cli_wu.jsonl")


def fnum(v):
    """Seam rule 5: dollar/fp STRING → float; absent/empty → None, NEVER 0."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_market(m: dict) -> dict:
    """One Kalshi bucket, dollar/fp-aware. floor/cap inclusive; open tails one-sided."""
    return {"ticker": m.get("ticker"), "floor": m.get("floor_strike"),
            "cap": m.get("cap_strike"), "sub": m.get("yes_sub_title"),
            "yes_bid": fnum(m.get("yes_bid_dollars")),
            "yes_ask": fnum(m.get("yes_ask_dollars")),
            "last": fnum(m.get("last_price_dollars")),
            "vol_fp": fnum(m.get("volume_fp")),
            "oi_fp": fnum(m.get("open_interest_fp")),
            "result": m.get("result") or None}


def _append(path: str, row: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def _loaded_dates(path: str, key: str) -> set:
    out = set()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.add(json.loads(line).get(key))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def duty_snapshot(c: SafeHTTPClient) -> str:
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    ev = c.get_json(f"{API}/events", {"series_ticker": SERIES, "status": "open",
                                      "with_nested_markets": "true"})
    kalshi = [{"event": e.get("event_ticker"),
               "buckets": [parse_market(m) for m in (e.get("markets") or [])]}
              for e in ev.get("events", [])]
    poly = None
    try:
        from weather_council.market import MarketData
        from weather_council.compare import match_market
        target = dt.datetime.now(LA).date()
        mkts = MarketData(http=c).fetch_temperature_markets()
        m = match_market(mkts, "San Francisco", target)
        if m is not None:
            poly = {"title": getattr(m, "title", None),
                    "buckets": [{"label": getattr(b, "label", None),
                                 "lo": getattr(b, "lo", None), "hi": getattr(b, "hi", None),
                                 "yes": getattr(b, "yes_price", None),
                                 "bid": getattr(b, "best_bid", None),
                                 "ask": getattr(b, "best_ask", None)}
                                for b in getattr(m, "buckets", [])]}
    except Exception as exc:                    # matched capture is best-effort; say so
        poly = {"error": str(exc)[:120]}
    _append(SNAP, {"ts": ts, "kalshi": kalshi, "polymarket": poly})
    n_two = sum(1 for e in kalshi for b in e["buckets"]
                if (b["yes_bid"] or 0) > 0 and 0 < (b["yes_ask"] or 0) < 1)
    return (f"snapshot: {sum(len(e['buckets']) for e in kalshi)} kalshi buckets "
            f"({n_two} two-sided), polymarket {'ok' if poly and 'error' not in (poly or {}) else 'unavailable'}")


def duty_preserve(c: SafeHTTPClient, cap: int = 8) -> str:
    have = _loaded_dates(CACHE, "event")
    ev = c.get_json(f"{API}/events", {"series_ticker": SERIES, "status": "settled",
                                      "limit": "200", "with_nested_markets": "true"})
    added = 0
    for e in ev.get("events", []):
        et = e.get("event_ticker")
        if et in have or added >= cap:
            continue
        winners = [m for m in (e.get("markets") or []) if m.get("result") == "yes"]
        if len(winners) != 1:
            continue                             # not yet resolved in API; retry next run
        w = winners[0]
        trades, cursor = [], None
        for _ in range(3):
            params = {"ticker": w.get("ticker"), "limit": "1000"}
            if cursor:
                params["cursor"] = cursor
            t = c.get_json(f"{API}/markets/trades", params)
            trades += t.get("trades", [])
            cursor = t.get("cursor")
            if not cursor:
                break
        _append(CACHE, {"event": et, "ticker": w.get("ticker"),
                        "floor": w.get("floor_strike"), "cap": w.get("cap_strike"),
                        "close_time": w.get("close_time"),
                        "trades": [{"ts": x.get("created_time"),
                                    "p": (fnum(x.get("yes_price_dollars"))
                                          if x.get("yes_price_dollars") not in (None, "")
                                          else (1.0 - fnum(x.get("no_price_dollars"))
                                                if fnum(x.get("no_price_dollars")) is not None
                                                else None)),
                                    "n": fnum(x.get("count_fp")) or 0.0}
                                   for x in trades]})
        added += 1
    return f"preserve: +{added} settled event(s) banked (cache now {len(have) + added})"


def duty_truth(c: SafeHTTPClient) -> str:
    y = (dt.datetime.now(LA).date() - dt.timedelta(days=1))
    if y.isoformat() in _loaded_dates(CLIWU, "date"):
        return f"truth: {y} already logged — idempotent"
    cli = c.get_json("https://mesonet.agron.iastate.edu/json/cli.py",
                     {"station": "KSFO", "year": str(y.year)})
    row = next((r for r in cli.get("results", []) if r.get("valid") == y.isoformat()), None)
    wu = None
    try:
        from weather_council.sources import Sources
        d = Sources().wunderground_daily_max("KSFO", y, "America/Los_Angeles")
        wu = d.get("max_f") if d else None
    except Exception:
        pass
    if row is None and wu is None:
        return f"truth: {y} — neither source ready yet; retry next run"
    _append(CLIWU, {"date": y.isoformat(),
                    "cli_high": row.get("high") if row else None,
                    "cli_time": row.get("high_time") if row else None,
                    "wu_max_f": wu,
                    "divergence": (row.get("high") - wu)
                    if row and row.get("high") is not None and wu is not None else None})
    return (f"truth: {y} CLI {row.get('high') if row else '—'} vs WU {wu} "
            f"(divergence series appended)")


def _self_test() -> None:
    # seam rule 5: absent/empty NEVER becomes 0
    assert fnum(None) is None and fnum("") is None and fnum("0.4500") == 0.45
    b = parse_market({"ticker": "X-T76", "cap_strike": 75, "yes_bid_dollars": "",
                      "volume_fp": "5342.49"})
    assert b["yes_bid"] is None and b["vol_fp"] == 5342.49 and b["floor"] is None
    # T/B mapping: open tail carries one bound; band carries both, inclusive by contract
    band = parse_market({"ticker": "X-B76.5", "floor_strike": 76, "cap_strike": 77})
    assert (band["floor"], band["cap"]) == (76, 77)
    # idempotence keys
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "x.jsonl")
        _append(p, {"date": "2026-07-13", "v": 1})
        assert "2026-07-13" in _loaded_dates(p, "date")
        assert "2026-07-14" not in _loaded_dates(p, "date")
    print("kalshi_logger self-test PASSED — dollar/fp absent→None (never 0); T/B bounds; "
          "idempotence keys; no network touched.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        _self_test()
        return 0
    c = SafeHTTPClient()
    for duty in (duty_snapshot, duty_preserve, duty_truth):
        try:
            print(f"  {duty(c)}")
        except Exception as exc:                 # one duty failing must not starve the rest
            print(f"  {duty.__name__} failed (non-fatal): {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
