#!/usr/bin/env python3
"""Polymarket trade-tape KILL test — ledger/preregistered/polymarket_tape_kill_test.md.

FROZEN design; one attempt. Chunked fetching (≤50 requests/run) with a resume cache
(reports/streams/pm_tape_cache.jsonl); scores only when the cache covers the universe.
Run repeatedly until a verdict prints: PYTHONPATH=. python3 reports/backtest_polymarket_tape.py
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import sqlite3
import sys
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from weather_council.security import SafeHTTPClient  # noqa: E402

CACHE = os.path.join(ROOT, "reports", "streams", "pm_tape_cache.jsonl")
GAMMA = "https://gamma-api.polymarket.com"
DAPI = "https://data-api.polymarket.com"
FETCH_CAP = 50
ENTRY_H = {"Singapore": 15, "London": 16, "Karachi": 15, "Jeddah": 15, "San Francisco": 15}
TZ = {"Singapore": "Asia/Singapore", "London": "Europe/London", "Karachi": "Asia/Karachi",
      "Jeddah": "Asia/Riyadh", "San Francisco": "America/Los_Angeles"}
KILL_EPS = 0.01
MIN_SCORED = 60


def rhu(x): return math.floor(x + 0.5)


def slugify(title: str) -> str:
    s = re.sub(r"[^\w\s-]", "", title.lower())
    return re.sub(r"\s+", "-", s.strip())


def get_list(c: SafeHTTPClient, url: str, params: dict):
    """gamma returns top-level ARRAYS; SafeHTTPClient insists on objects — fetch raw."""
    host, body = c._fetch(url, params, "application/json")
    return json.loads(body.decode("utf-8"))


def universe() -> list[dict]:
    conn = sqlite3.connect(os.path.join(ROOT, "verdicts.db"))
    rows = conn.execute(
        "SELECT DISTINCT place, target_date, market_title, pm_resolved_label, realized_high "
        "FROM market_snapshots WHERE (pm_resolved_label IS NOT NULL OR realized_high IS NOT NULL) "
        "AND market_title IS NOT NULL ORDER BY target_date").fetchall()
    conn.close()
    out, seen = [], set()
    for place, tgt, title, pm, rh in rows:
        city = place.split(",")[0]
        if (city, tgt) in seen:
            continue
        seen.add((city, tgt))
        out.append({"city": city, "date": tgt, "title": title, "pm": pm, "rh": rh})
    return out


def main() -> int:
    cache = {}
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            for line in f:
                r = json.loads(line)
                cache[r["key"]] = r

    uni = universe()
    c = SafeHTTPClient()
    spent = 0
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "a") as out:
        for u in uni:
            key = f"{u['city']}|{u['date']}"
            if key in cache or spent >= FETCH_CAP:
                continue
            row = {"key": key, **u}
            try:
                evs = get_list(c, f"{GAMMA}/events", {"slug": slugify(u["title"])})
                spent += 1
                if not evs:
                    row["flag"] = "slug_miss"
                else:
                    mkts = evs[0].get("markets", [])
                    winner = None
                    for m in mkts:
                        try:
                            op = json.loads(m.get("outcomePrices") or "[]")
                        except ValueError:
                            op = []
                        if op and float(op[0]) == 1.0:      # outcome[0] == "Yes"
                            winner = m
                            break
                    if winner is None:
                        row["flag"] = "no_winner"
                    else:
                        row["bucket"] = winner.get("groupItemTitle")
                        trades, offset = [], 0
                        for _pg in range(6):
                            if spent >= FETCH_CAP + 5:
                                break
                            t = c.get_json(f"{DAPI}/trades",
                                           {"market": winner.get("conditionId"),
                                            "limit": "500", "offset": str(offset)}) \
                                if False else get_list(c, f"{DAPI}/trades",
                                                       {"market": winner.get("conditionId"),
                                                        "limit": "500", "offset": str(offset)})
                            spent += 1
                            trades += t
                            if len(t) < 500:
                                break
                            offset += 500
                        row["trades"] = [{"ts": x.get("timestamp"),
                                          "p": x.get("price"), "n": x.get("size"),
                                          "o": x.get("outcome")} for x in trades]
            except Exception as exc:
                row["flag"] = f"fetch_error:{str(exc)[:60]}"
            out.write(json.dumps(row) + "\n")
            cache[key] = row

    missing = [u for u in uni if f"{u['city']}|{u['date']}" not in cache]
    if missing:
        print(f"cache incomplete: {len(missing)}/{len(uni)} remain (spent {spent}) — run again")
        return 3

    # ---- SCORE (frozen verdict order) ----
    slug_miss = sum(1 for r in cache.values() if r.get("flag") == "slug_miss")
    resolved = [r for r in cache.values() if "trades" in r]
    scored, empty, mismatch = [], 0, 0
    for r in sorted(resolved, key=lambda z: z["date"]):
        # winner cross-check vs our recorded settle
        ours = r.get("pm") or (f"{rhu(float(r['rh']))}" if r.get("rh") is not None else None)
        if ours and r.get("bucket") and re.sub(r"\D", "", str(r["bucket"])) not in str(ours):
            mismatch += 1
            continue
        tz = ZoneInfo(TZ[r["city"]])
        d = dt.date.fromisoformat(r["date"])
        start = dt.datetime.combine(d, dt.time(ENTRY_H[r["city"]], 0), tzinfo=tz).timestamp()
        end = dt.datetime.combine(d + dt.timedelta(days=1), dt.time(0, 0), tzinfo=tz).timestamp()
        pts = []
        for t in r["trades"]:
            try:
                ts, p, n = float(t["ts"]), float(t["p"]), float(t["n"])
            except (TypeError, ValueError):
                continue
            if start <= ts < end and n > 0:
                yes_p = p if (t.get("o") or "").lower() == "yes" else 1.0 - p
                pts.append((yes_p, n))
        if not pts:
            empty += 1
            continue
        vol = sum(n for _p, n in pts)
        vw = sum(p * n for p, n in pts) / vol
        scored.append({"city": r["city"], "date": r["date"], "vw": vw, "gap": 1.0 - vw,
                       "killable": (1.0 - vw) <= KILL_EPS, "vol": vol})

    n_uni = len(cache)
    print(f"universe {n_uni} | slug-miss {slug_miss} | resolved-with-tape {len(resolved)} "
          f"| winner-mismatch {mismatch} | empty-afternoon {empty} | scored {len(scored)}")

    if n_uni and slug_miss / n_uni > 0.20:
        print(f"VERDICT: ABORT unscored — slug resolution failed on "
              f"{slug_miss/n_uni:.0%} > 20% (universe not resolvable).")
        return 4
    if resolved and empty / max(1, len(resolved) - mismatch) > 0.30:
        print(f"VERDICT: ABORT unscored — empty afternoon tapes on "
              f"{empty}/{len(resolved)-mismatch} resolved days > 30% (history depth "
              f"insufficient OR genuinely untraded — recorded, not killed).")
        return 4
    if len(scored) < MIN_SCORED:
        print(f"VERDICT: ACCRUING — scored {len(scored)} < {MIN_SCORED} floor.")
        return 2
    kill = sum(1 for s in scored if s["killable"]) / len(scored)
    gaps = sorted(s["gap"] for s in scored)
    dec = [gaps[int(q * (len(gaps) - 1))] for q in (.1, .25, .5, .75, .9)]
    half = len(scored) // 2
    print(f"gap deciles 10/25/50/75/90: {[round(x,3) for x in dec]} | mean "
          f"{sum(gaps)/len(gaps):.4f}")
    print(f"era halves: {sum(s['gap'] for s in scored[:half])/half:+.4f} / "
          f"{sum(s['gap'] for s in scored[half:])/(len(scored)-half):+.4f}")
    for city in ENTRY_H:
        g = [s["gap"] for s in scored if s["city"] == city]
        if g:
            print(f"  {city}: n={len(g)} mean gap {sum(g)/len(g):.4f}")
    print(f"killable (gap<= {KILL_EPS}) on {kill:.0%} of {len(scored)} days (bar >=80%)")
    if kill >= 0.80:
        print("VERDICT: KILL — the residue does not exist in the executed record. D25.")
        return 1
    print("VERDICT: SURVIVES (hindsight-winner UPPER BOUND — permission to keep testing, "
          "not tradability; the ask-fill preregs remain the tradability instruments).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
