#!/usr/bin/env python3
"""Polymarket trade-tape KILL test v2 — polymarket_tape_kill_test_v2.md. FROZEN; one
attempt. v1's abort diagnosed wrong-event slug resolution; v2 verifies every event
(end-date window + bucket-unit grain) before trusting it. Fresh cache. Run until verdict:
PYTHONPATH=. python3 reports/backtest_polymarket_tape_v2.py
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

CACHE = os.path.join(ROOT, "reports", "streams", "pm_tape_cache_v2.jsonl")
GAMMA = "https://gamma-api.polymarket.com"
DAPI = "https://data-api.polymarket.com"
FETCH_CAP = 50
CITIES = {"Singapore": ("Asia/Singapore", 15, "°C"),
          "London": ("Europe/London", 16, "°C"),
          "Karachi": ("Asia/Karachi", 15, "°C"),
          "Jeddah": ("Asia/Riyadh", 15, "°C"),
          "San Francisco": ("America/Los_Angeles", 15, "°F")}
KILL_EPS, MIN_SCORED = 0.01, 40


def rhu(x): return math.floor(x + 0.5)


def slugify(title):
    return re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", title.lower()).strip())


def get_list(c, url, params):
    _h, body = c._fetch(url, params, "application/json")
    return json.loads(body.decode("utf-8"))


def verified_event(c, title, target: dt.date, unit: str):
    """Try slug variants; accept only an event whose endDate is within [target, target+3d]
    AND whose bucket labels carry the city's settlement unit. Returns (event, note)."""
    base = slugify(title)
    for slug in (base, f"{base}-{target.year}"):
        evs = get_list(c, f"{GAMMA}/events", {"slug": slug})
        if not evs:
            continue
        e = evs[0]
        end = (e.get("endDate") or "")[:10]
        try:
            ok_date = target <= dt.date.fromisoformat(end) <= target + dt.timedelta(days=3)
        except ValueError:
            ok_date = False
        labels = [m.get("groupItemTitle") or "" for m in e.get("markets", [])]
        ok_unit = any(unit in lb for lb in labels)
        if ok_date and ok_unit:
            return e, f"slug={slug}"
        note = f"rejected slug={slug} end={end} unit_ok={ok_unit}"
    else:
        note = "no_event"
    return None, note


def main() -> int:
    cache = {}
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            for line in f:
                r = json.loads(line)
                cache[r["key"]] = r

    conn = sqlite3.connect(os.path.join(ROOT, "verdicts.db"))
    rows = conn.execute(
        "SELECT DISTINCT place, target_date, market_title, pm_resolved_label, realized_high "
        "FROM market_snapshots WHERE (pm_resolved_label IS NOT NULL OR realized_high IS NOT NULL) "
        "AND market_title IS NOT NULL ORDER BY target_date").fetchall()
    conn.close()
    uni, seen = [], set()
    for place, tgt, title, pm, rh in rows:
        city = place.split(",")[0]
        if city not in CITIES or (city, tgt) in seen:
            continue
        seen.add((city, tgt))
        uni.append({"city": city, "date": tgt, "title": title, "pm": pm, "rh": rh})

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
                tzname, _eh, unit = CITIES[u["city"]]
                e, note = verified_event(c, u["title"], dt.date.fromisoformat(u["date"]), unit)
                spent += 2
                row["note"] = note
                if e is None:
                    row["flag"] = "resolution_fail"
                else:
                    winner = None
                    for m in e.get("markets", []):
                        try:
                            op = json.loads(m.get("outcomePrices") or "[]")
                        except ValueError:
                            op = []
                        if op and float(op[0]) == 1.0:
                            winner = m
                            break
                    if winner is None:
                        row["flag"] = "no_winner"
                    else:
                        row["bucket"] = winner.get("groupItemTitle")
                        trades, offset = [], 0
                        for _pg in range(4):
                            t = get_list(c, f"{DAPI}/trades",
                                         {"market": winner.get("conditionId"),
                                          "limit": "500", "offset": str(offset)})
                            spent += 1
                            trades += t
                            if len(t) < 500:
                                break
                            offset += 500
                        row["trades"] = [{"ts": x.get("timestamp"), "p": x.get("price"),
                                          "n": x.get("size"), "o": x.get("outcome")}
                                         for x in trades]
            except Exception as exc:
                row["flag"] = f"fetch_error:{str(exc)[:60]}"
            out.write(json.dumps(row) + "\n")
            cache[key] = row

    missing = [u for u in uni if f"{u['city']}|{u['date']}" not in cache]
    if missing:
        print(f"cache incomplete: {len(missing)}/{len(uni)} remain (spent {spent}) — run again")
        return 3

    # ---- SCORE (frozen order) ----
    res_fail = sum(1 for r in cache.values() if r.get("flag") == "resolution_fail")
    resolved = [r for r in cache.values() if "trades" in r]
    scored, empty, mismatch = [], 0, 0
    for r in sorted(resolved, key=lambda z: z["date"]):
        ours = r.get("pm") or (f"{rhu(float(r['rh']))}" if r.get("rh") is not None else None)
        b_digits = re.sub(r"\D", "", str(r.get("bucket") or ""))
        if not ours or not b_digits or b_digits not in re.sub(r"\D", "", str(ours)) \
                and re.sub(r"\D", "", str(ours)) not in b_digits:
            mismatch += 1
            continue
        tzname, eh, _u = CITIES[r["city"]]
        tz = ZoneInfo(tzname)
        d = dt.date.fromisoformat(r["date"])
        start = dt.datetime.combine(d, dt.time(eh, 0), tzinfo=tz).timestamp()
        end = dt.datetime.combine(d + dt.timedelta(days=1), dt.time(0, 0), tzinfo=tz).timestamp()
        pts = []
        for t in r["trades"]:
            try:
                ts, p, n = float(t["ts"]), float(t["p"]), float(t["n"])
            except (TypeError, ValueError):
                continue
            if start <= ts < end and n > 0:
                pts.append((p if (t.get("o") or "").lower() == "yes" else 1.0 - p, n))
        if not pts:
            empty += 1
            continue
        vol = sum(n for _p, n in pts)
        vw = sum(p * n for p, n in pts) / vol
        scored.append({"city": r["city"], "date": r["date"], "gap": 1.0 - vw,
                       "killable": (1.0 - vw) <= KILL_EPS})

    n_uni = len(cache)
    print(f"universe {n_uni} | resolution-fail {res_fail} | resolved {len(resolved)} | "
          f"mismatch {mismatch} | empty {empty} | scored {len(scored)}")
    if n_uni and res_fail / n_uni > 0.20:
        print(f"VERDICT: ABORT unscored — resolution failed {res_fail/n_uni:.0%} > 20%.")
        return 4
    if resolved and empty / max(1, len(resolved) - mismatch) > 0.30:
        print("VERDICT: ABORT unscored — empty afternoon tapes > 30%.")
        return 4
    if len(scored) < MIN_SCORED:
        print(f"VERDICT: ACCRUING — scored {len(scored)} < {MIN_SCORED}.")
        return 2
    kill = sum(1 for s in scored if s["killable"]) / len(scored)
    gaps = sorted(s["gap"] for s in scored)
    dec = [gaps[int(q * (len(gaps) - 1))] for q in (.1, .25, .5, .75, .9)]
    half = len(scored) // 2
    print(f"gap deciles: {[round(x,3) for x in dec]} | mean {sum(gaps)/len(gaps):.4f} | "
          f"halves {sum(s['gap'] for s in scored[:half])/half:+.4f}/"
          f"{sum(s['gap'] for s in scored[half:])/(len(scored)-half):+.4f}")
    for city in CITIES:
        g = [s["gap"] for s in scored if s["city"] == city]
        if g:
            print(f"  {city}: n={len(g)} mean gap {sum(g)/len(g):.4f} "
                  f"killable {sum(1 for s in scored if s['city']==city and s['killable'])}/{len(g)}")
    print(f"killable on {kill:.0%} of {len(scored)} (bar >=80%)")
    if kill >= 0.80:
        print("VERDICT: KILL — D25.")
        return 1
    print("VERDICT: SURVIVES (hindsight-winner UPPER BOUND; ask-fill preregs remain the "
          "tradability instruments).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
