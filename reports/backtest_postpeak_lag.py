#!/usr/bin/env python3
"""Probe for ledger/preregistered/postpeak_lag_trade.md — FROZEN design, one attempt.

Post-peak settlement-lag trade, Singapore: after the peak has demonstrably passed
(SGT >= 15:00, shipped 2-consec declining rule, leak-free from the IEM archive), buy the
running-max bucket at its RECORDED best_ask. One trade per day (first qualifying
snapshot). Win (1-ask)/ask, lose -1. Untradeable (no ask) counted, never skipped
silently. Reports the frozen bar's six criteria + the driver-gap series. Deterministic,
stdlib-only, read-only. Run: PYTHONPATH=. python3 reports/backtest_postpeak_lag.py
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

ARCHIVE = os.path.join(ROOT, "data", "wsss_hourly_iem.jsonl")
DB = os.path.join(ROOT, "verdicts.db")
SGT = ZoneInfo("Asia/Singapore")
ENTRY_HOUR = 15.0
MAX_ASK = 0.97
MIN_N = 20
CERT = {15: 0.9338842975206612, 16: 0.9752066115702479}   # pinned crossover baseline


def rhu(x: float) -> int:
    return math.floor(x + 0.5)


def day_state(obs: list[tuple[float, float]]) -> str | None:
    """The shipped 2-consecutive-reads declining rule (intraday_ceiling._day_state)."""
    if not obs:
        return None
    rm = max(c for _h, c in obs)
    tail = obs[-2:]
    below = [c < rm - 0.3 for _h, c in tail]
    return "declining" if len(below) == 2 and all(below) else "holding"


def main() -> int:
    arch: dict[str, list[tuple[float, float]]] = {}
    with open(ARCHIVE) as f:
        for line in f:
            r = json.loads(line)
            arch[r["date"]] = [(float(h), float(c)) for h, c in r["obs"]]

    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT issued_at, target_date, buckets_json, pm_resolved_label, realized_high "
        "FROM market_snapshots WHERE place LIKE 'Singapore%' AND target_date <= '2026-07-12' "
        "ORDER BY issued_at").fetchall()
    conn.close()

    trades, untradeable, skipped_state = [], 0, 0
    taken_days: set[str] = set()
    for issued, target, bj, pm_label, realized in rows:
        if target in taken_days:
            continue
        try:
            t_utc = dt.datetime.fromisoformat(issued.replace("Z", "+00:00"))
            if t_utc.tzinfo is None:
                t_utc = t_utc.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        t_sgt = t_utc.astimezone(SGT)
        if t_sgt.date().isoformat() != target:
            continue                                        # lead-0 only
        hh = t_sgt.hour + t_sgt.minute / 60.0
        if hh < ENTRY_HOUR:
            continue
        obs = [(h, c) for h, c in arch.get(target, []) if h <= hh]
        if not obs:
            continue
        if day_state(obs) != "declining":
            skipped_state += 1
            continue
        b_rm = rhu(max(c for _h, c in obs))
        # settle bucket
        if realized is not None:
            settle = rhu(float(realized))
        elif pm_label:
            digits = "".join(ch for ch in pm_label if ch.isdigit())
            settle = int(digits) if digits else None
        else:
            continue
        if settle is None:
            continue
        # find the running-max bucket's recorded quote
        buckets = json.loads(bj)
        entry = None
        for b in buckets:
            lo = b.get("lo") if b.get("lo") is not None else -1e9
            hi = b.get("hi") if b.get("hi") is not None else 1e9
            if lo <= b_rm < hi or (b.get("label", "").startswith(str(b_rm))):
                entry = b
                break
        if entry is None:
            continue
        taken_days.add(target)                              # one decision per day
        ask = entry.get("best_ask")
        liq = entry.get("liquidity")
        cert = CERT[15] if hh < 16 else CERT[16]
        if not isinstance(ask, (int, float)) or not (0.0 < ask <= MAX_ASK):
            untradeable += 1
            trades.append({"date": target, "hh": round(hh, 2), "bucket": b_rm,
                           "settle": settle, "ask": None, "gap": None, "liq": liq,
                           "ret": None})
            continue
        win = settle == b_rm
        ret = (1.0 - ask) / ask if win else -1.0
        trades.append({"date": target, "hh": round(hh, 2), "bucket": b_rm,
                       "settle": settle, "ask": round(float(ask), 3),
                       "gap": round(cert - float(ask), 3),
                       "liq": liq, "ret": round(ret, 4)})

    filled = [t for t in trades if t["ret"] is not None]
    n_days = len(trades)
    print(f"decision days (predicate met): {n_days}  |  filled: {len(filled)}  |  "
          f"untradeable (no ask): {untradeable}  |  holding-skips: {skipped_state}")
    for t in trades:
        print(f"  {t['date']} {t['hh']:>5}h  buy {t['bucket']}°C @ "
              f"{t['ask'] if t['ask'] is not None else 'NO-ASK'}  settle {t['settle']}  "
              f"ret {t['ret'] if t['ret'] is not None else '—'}  gap {t['gap']}  "
              f"liq {t['liq']}")

    if n_days < MIN_N:
        print(f"\nVERDICT: ACCRUING — {n_days} decision days < frozen floor {MIN_N}. "
              f"No verdict may be stated (prereg criterion 1).")
        return 2

    rets = [t["ret"] for t in filled]
    asks = [t["ask"] for t in filled]
    half = len(rets) // 2
    m = sum(rets) / len(rets) if rets else float("nan")
    h1 = sum(rets[:half]) / half if half else float("nan")
    h2 = sum(rets[half:]) / (len(rets) - half) if len(rets) - half else float("nan")
    hit = sum(1 for t in filled if t["ret"] > 0) / len(filled) if filled else float("nan")
    liqs = sorted(t["liq"] for t in trades if isinstance(t["liq"], (int, float)))
    med_liq = liqs[len(liqs) // 2] if liqs else 0.0
    untr_rate = untradeable / n_days
    gaps = [t["gap"] for t in filled if t["gap"] is not None]
    gh1 = sum(gaps[:len(gaps)//2]) / max(1, len(gaps)//2)
    gh2 = sum(gaps[len(gaps)//2:]) / max(1, len(gaps) - len(gaps)//2)

    c2 = m > 0
    c3 = h1 > 0 and h2 > 0
    c4 = hit > (sum(asks) / len(asks) if asks else 1.0)
    c5 = untr_rate < 0.50
    c6 = med_liq >= 50.0
    print(f"\nmean net/unit {m:+.3f} | halves {h1:+.3f} / {h2:+.3f} | hit {hit:.0%} "
          f"vs mean ask {sum(asks)/len(asks):.2f} | untradeable {untr_rate:.0%} | "
          f"median liq ${med_liq:,.0f}")
    print(f"driver gap (cert − ask): early {gh1:+.3f} → late {gh2:+.3f} (the kill-watch)")
    ok = all([c2, c3, c4, c5, c6])
    print(f"C2 pooled>0 {'PASS' if c2 else 'FAIL'} | C3 both-halves {'PASS' if c3 else 'FAIL'} | "
          f"C4 hit>ask {'PASS' if c4 else 'FAIL'} | C5 tradeable {'PASS' if c5 else 'FAIL'} | "
          f"C6 capacity {'PASS' if c6 else 'FAIL'}")
    print("VERDICT:", "PASS — forward paper ledger next (never capital from this alone)"
          if ok else "FAIL — dead-ledger D20")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
