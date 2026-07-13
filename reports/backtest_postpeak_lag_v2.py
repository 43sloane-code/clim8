#!/usr/bin/env python3
"""Probe for ledger/preregistered/postpeak_lag_trade_ldn_jed.md — FROZEN, one attempt/city.

City-parameterized post-peak settlement-lag probe (London EGLC, Jeddah OEJN). The
Singapore script (backtest_postpeak_lag.py) stays untouched per its own registration.
Static IEM archive extended over its end-gap by a live IEM fetch of the same FIXED past
window (stable historical feed; only obs <= issue hour are ever used — leak-free).
Run: PYTHONPATH=. python3 reports/backtest_postpeak_lag_v2.py <london|jeddah>
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import sqlite3
import sys
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MAX_ASK = 0.97
MIN_N = 20
END = dt.date(2026, 7, 12)

CFG = {
    "london": {"place": "London%", "icao": "EGLC", "tz": "Europe/London",
               "archive": "data/eglc_hourly_iem.jsonl", "entry_h": 16.0,
               "cert": 0.9327731092436975, "dead_id": "D21"},
    "jeddah": {"place": "Jeddah%", "icao": "OEJN", "tz": "Asia/Riyadh",
               "archive": "data/oejn_hourly_iem.jsonl", "entry_h": 15.0,
               "cert": None, "dead_id": "D22"},
}


def rhu(x: float) -> int:
    return math.floor(x + 0.5)


def day_state(obs):
    if not obs:
        return None
    rm = max(c for _h, c in obs)
    tail = obs[-2:]
    below = [c < rm - 0.3 for _h, c in tail]
    return "declining" if len(below) == 2 and all(below) else "holding"


def load_archive(path, icao, tz):
    arch = {}
    with open(os.path.join(ROOT, path)) as f:
        for line in f:
            r = json.loads(line)
            arch[r["date"]] = [(float(h), float(c)) for h, c in r["obs"]]
    last = max(arch)
    if last < END.isoformat():                       # extend the fixed past window live
        from weather_council.sources import Sources
        start = dt.date.fromisoformat(last)          # refetch boundary day too (complete it)
        obs = Sources().fetch_metar_observations(icao, start, END + dt.timedelta(days=1), tz)
        ext = {}
        for ts, c in obs:
            hh = int(ts[11:13]) + int(ts[14:16]) / 60.0
            ext.setdefault(ts[:10], []).append((hh, float(c)))
        for d, o in ext.items():
            arch[d] = sorted(o)                      # live version wins for the gap days
        print(f"  (archive extended live {start} → {END}: +{len(ext)} days)")
    return arch


def main() -> int:
    city = sys.argv[1].lower()
    cfg = CFG[city]
    arch = load_archive(cfg["archive"], cfg["icao"], cfg["tz"])
    tz = ZoneInfo(cfg["tz"])

    conn = sqlite3.connect(os.path.join(ROOT, "verdicts.db"))
    rows = conn.execute(
        "SELECT issued_at, target_date, buckets_json, pm_resolved_label, realized_high "
        "FROM market_snapshots WHERE place LIKE ? AND target_date <= '2026-07-12' "
        "ORDER BY issued_at", (cfg["place"],)).fetchall()
    conn.close()

    trades, untradeable, skipped_state = [], 0, 0
    taken = set()
    for issued, target, bj, pm_label, realized in rows:
        if target in taken:
            continue
        t = dt.datetime.fromisoformat(issued.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        tl = t.astimezone(tz)
        if tl.date().isoformat() != target:
            continue
        hh = tl.hour + tl.minute / 60.0
        if hh < cfg["entry_h"]:
            continue
        obs = [(h, c) for h, c in arch.get(target, []) if h <= hh]
        if not obs:
            continue
        if day_state(obs) != "declining":
            skipped_state += 1
            continue
        b_rm = rhu(max(c for _h, c in obs))
        if realized is not None:
            settle = rhu(float(realized))
        elif pm_label:
            digits = "".join(ch for ch in pm_label if ch.isdigit())
            settle = int(digits) if digits else None
        else:
            continue
        if settle is None:
            continue
        entry = None
        for b in json.loads(bj):
            lo = b.get("lo") if b.get("lo") is not None else -1e9
            hi = b.get("hi") if b.get("hi") is not None else 1e9
            if lo <= b_rm < hi or b.get("label", "").startswith(str(b_rm)):
                entry = b
                break
        if entry is None:
            continue
        taken.add(target)
        ask, liq = entry.get("best_ask"), entry.get("liquidity")
        gap = (round(cfg["cert"] - float(ask), 3)
               if cfg["cert"] is not None and isinstance(ask, (int, float)) else None)
        if not isinstance(ask, (int, float)) or not (0.0 < ask <= MAX_ASK):
            untradeable += 1
            trades.append({"date": target, "hh": round(hh, 2), "bucket": b_rm,
                           "settle": settle, "ask": None, "gap": None, "liq": liq,
                           "ret": None})
            continue
        ret = (1.0 - ask) / ask if settle == b_rm else -1.0
        trades.append({"date": target, "hh": round(hh, 2), "bucket": b_rm,
                       "settle": settle, "ask": round(float(ask), 3), "gap": gap,
                       "liq": liq, "ret": round(ret, 4)})

    filled = [t for t in trades if t["ret"] is not None]
    n = len(trades)
    print(f"{city.upper()}: decision days {n} | filled {len(filled)} | "
          f"untradeable {untradeable} | holding-skips {skipped_state}")
    for t in trades:
        print(f"  {t['date']} {t['hh']:>5}h  buy {t['bucket']}°C @ "
              f"{t['ask'] if t['ask'] is not None else 'NO-ASK'}  settle {t['settle']}  "
              f"ret {t['ret'] if t['ret'] is not None else '—'}  gap {t['gap']}  liq {t['liq']}")

    if n < MIN_N:
        print(f"VERDICT: ACCRUING — {n} decision days < frozen floor {MIN_N} "
              f"(prereg criterion 1). No verdict may be stated.")
        return 2

    rets = [t["ret"] for t in filled]
    asks = [t["ask"] for t in filled]
    half = len(rets) // 2
    m = sum(rets) / len(rets) if rets else float("nan")
    h1 = sum(rets[:half]) / half if half else float("nan")
    h2 = sum(rets[half:]) / (len(rets) - half) if rets[half:] else float("nan")
    hit = sum(1 for r in rets if r > 0) / len(rets) if rets else float("nan")
    liqs = sorted(t["liq"] for t in trades if isinstance(t["liq"], (int, float)))
    med_liq = liqs[len(liqs) // 2] if liqs else 0.0
    untr = untradeable / n
    c = [m > 0, h1 > 0 and h2 > 0, bool(asks) and hit > sum(asks) / len(asks),
         untr < 0.50, med_liq >= 50.0]
    print(f"mean {m:+.3f} | halves {h1:+.3f}/{h2:+.3f} | hit {hit:.0%} vs mean ask "
          f"{(sum(asks)/len(asks)) if asks else float('nan'):.2f} | untradeable {untr:.0%} "
          f"| median liq ${med_liq:,.0f}")
    print("criteria C2..C6:", ["PASS" if x else "FAIL" for x in c])
    ok = all(c)
    print("VERDICT:", "PASS — forward paper ledger next" if ok
          else f"FAIL — dead ledger {cfg['dead_id']}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
