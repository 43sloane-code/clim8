#!/usr/bin/env python3
"""S2a probe — Kalshi SF historical KILL test (ledger/preregistered/kalshi_s2a_kill_test.md).

FROZEN design; one attempt. Chunked fetching with a resume cache (pure IO — allowed):
each invocation spends ≤55 of the client's 64-request budget filling
reports/streams/kalshi_s2a_cache.jsonl, then scores ONLY when the cache is complete.
Run repeatedly until it prints a verdict:  PYTHONPATH=. python3 reports/backtest_kalshi_s2a.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from weather_council.security import SafeHTTPClient  # noqa: E402

CACHE = os.path.join(ROOT, "reports", "streams", "kalshi_s2a_cache.jsonl")
API = "https://api.elections.kalshi.com/trade-api/v2"
LA = ZoneInfo("America/Los_Angeles")
FETCH_CAP = 55
_MON = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def ev_date(event_ticker: str) -> dt.date:
    tail = event_ticker.rsplit("-", 1)[1]              # e.g. 26JAN14
    return dt.date(2000 + int(tail[:2]), _MON[tail[2:5]], int(tail[5:]))


def yes_price(t: dict) -> float | None:
    v = t.get("yes_price_dollars")
    if v not in (None, ""):
        return float(v)
    v = t.get("no_price_dollars")
    return 1.0 - float(v) if v not in (None, "") else None


def main() -> int:
    cache = {}
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            for line in f:
                r = json.loads(line)
                cache[r["event"]] = r

    c = SafeHTTPClient()
    spent = 0

    # 1. settled event universe (1 request; re-fetched each run — cheap, deterministic set)
    ev = c.get_json(f"{API}/events",
                    {"series_ticker": "KXHIGHTSFO", "status": "settled", "limit": "200",
                     "with_nested_markets": "true"})
    spent += 1
    events = ev.get("events", [])
    print(f"settled events: {len(events)} | cached: {len(cache)}")

    # 2. fill the cache: winner market + its full trade tape per event.
    # IO REPAIR (no design change): nested markets on OLDER settled events omit `result`,
    # so a missing winner is re-resolved via GET /markets?event_ticker (which carries it);
    # prior `winners=0` flag rows are treated as refetchable, not final.
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "a") as out:
        for e in events:
            et = e.get("event_ticker")
            prior = cache.get(et)
            if (prior is not None and not str(prior.get("flag", "")).startswith("winners=0")) \
                    or spent >= FETCH_CAP:
                continue
            winners = [m for m in (e.get("markets") or []) if m.get("result") == "yes"]
            if len(winners) != 1 and spent < FETCH_CAP:
                mm = c.get_json(f"{API}/markets", {"event_ticker": et})
                spent += 1
                winners = [m for m in mm.get("markets", []) if m.get("result") == "yes"]
            if len(winners) != 1:
                row = {"event": et, "flag": f"winners={len(winners)}-final"}
                out.write(json.dumps(row) + "\n")
                cache[et] = row
                continue
            w = winners[0]
            trades, cursor = [], None
            for _page in range(3):
                if spent >= FETCH_CAP + 3:
                    break
                params = {"ticker": w.get("ticker"), "limit": "1000"}
                if cursor:
                    params["cursor"] = cursor
                t = c.get_json(f"{API}/markets/trades", params)
                spent += 1
                trades += t.get("trades", [])
                cursor = t.get("cursor")
                if not cursor:
                    break
            row = {"event": et, "ticker": w.get("ticker"),
                   "floor": w.get("floor_strike"), "cap": w.get("cap_strike"),
                   "close_time": w.get("close_time"),
                   "trades": [{"ts": x.get("created_time"), "p": yes_price(x),
                               "n": float(x.get("count_fp") or x.get("count") or 0)}
                              for x in trades]}
            out.write(json.dumps(row) + "\n")
            cache[et] = row

    missing = [e.get("event_ticker") for e in events if e.get("event_ticker") not in cache]
    if missing:
        print(f"cache incomplete: {len(missing)} events remain — run again "
              f"(budget spent this run: {spent})")
        return 3

    # 3. SCORE (cache complete) — per the frozen verdict order
    iem = c.get_json("https://mesonet.agron.iastate.edu/json/cli.py",
                     {"station": "KSFO", "year": "2026"})
    cli_high = {r["valid"]: r.get("high") for r in iem.get("results", [])}

    scored, no_trade, flagged = [], [], []
    for et, r in sorted(cache.items()):
        if "flag" in r:
            flagged.append((et, r["flag"]))
            continue
        d = ev_date(et)
        # CLI cross-check: settled bucket must contain the CLI high (strikes inclusive)
        h = cli_high.get(d.isoformat())
        lo = r.get("floor") if r.get("floor") is not None else -999
        hi = r.get("cap") if r.get("cap") is not None else 999
        if h is None or not (lo <= h <= hi):
            flagged.append((et, f"cli_mismatch high={h} bucket=[{lo},{hi}]"))
            continue
        start_utc = dt.datetime.combine(d, dt.time(15, 0), tzinfo=LA).astimezone(dt.timezone.utc)
        pts = [(t["p"], t["n"]) for t in r["trades"] if t["p"] is not None and t["ts"]
               and dt.datetime.fromisoformat(t["ts"].replace("Z", "+00:00")) >= start_utc]
        if not pts:
            no_trade.append(et)
            continue
        vol = sum(n for _p, n in pts)
        vw = sum(p * n for p, n in pts) / vol if vol > 0 else None
        if vw is None:
            no_trade.append(et)
            continue
        gap = 1.0 - vw
        cost = 0.07 * vw * (1.0 - vw)
        scored.append({"event": et, "date": d.isoformat(), "vw": round(vw, 4),
                       "gap": round(gap, 4), "cost": round(cost, 4),
                       "killable": gap <= cost, "vol": round(vol)})

    n_all = len(cache)
    print(f"\nuniverse {n_all} | scored {len(scored)} | no-afternoon-trade "
          f"{len(no_trade)} | flagged {len(flagged)}")
    for et, fl in flagged[:8]:
        print("  flag:", et, fl)

    # frozen verdict order
    if n_all and len(no_trade) / n_all > 0.50:
        print(f"\nVERDICT: EXPANSION DEAD — illiquidity kill "
              f"({len(no_trade)}/{n_all} = {len(no_trade)/n_all:.0%} days with no "
              f"afternoon winner trades > 50%).")
        return 1
    if len(scored) < 100:
        print(f"\nVERDICT: ACCRUING — scored days {len(scored)} < 100 floor. "
              f"No verdict may be stated; forward-logger decision returns to the user.")
        return 2
    kill_rate = sum(1 for s in scored if s["killable"]) / len(scored)
    gaps = sorted(s["gap"] for s in scored)
    dec = [gaps[int(q * (len(gaps) - 1))] for q in (0.1, 0.25, 0.5, 0.75, 0.9)]
    half = len(scored) // 2
    g1 = sum(s["gap"] for s in scored[:half]) / half
    g2 = sum(s["gap"] for s in scored[half:]) / (len(scored) - half)
    print(f"\ngap deciles (10/25/50/75/90): {[round(x,3) for x in dec]}")
    print(f"gap era halves: {g1:+.4f} / {g2:+.4f}")
    print(f"gap<=cost on {kill_rate:.0%} of {len(scored)} scored days (kill bar: >=80%)")
    if kill_rate >= 0.80:
        print("\nVERDICT: EXPANSION DEAD — the book prices the afternoon winner to "
              "within costs; the venue-depth hypothesis is false. Dead-ledger entry.")
        return 1
    print("\nVERDICT: SURVIVES S2a — necessary condition only (hindsight-winner design; "
          "survival is permission to keep testing, NOT tradability). S2b per the expansion.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
