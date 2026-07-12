#!/usr/bin/env python3
"""Probe for ledger/preregistered/sf_native_f_headline.md — FROZEN design, one attempt.

Leak-free walk-forward on data/ksfo_hourly_iem.jsonl: quantize the SAME persistence-proxy
residual cloud (a) in whole-°F (the proposed served headline) and (b) in whole-°C with the
mass split uniformly over each °C bucket's °F integers (the most favorable reading of
today's served °C headline). Scores C1 (modal °F-bucket hit), C2 (mean log score, floor
1e-6), C3 (≥80% credible-set coverage in [0.70, 0.90]) on both chronological halves.
Deterministic, stdlib-only, no RNG. Run: PYTHONPATH=. python3 reports/backtest_sf_native_f.py
"""
from __future__ import annotations

import json
import math
import os

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "ksfo_hourly_iem.jsonl")
BACK_DAYS = 160
MIN_RESID = 20
LOG_FLOOR = 1e-6


def c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def rhu(x: float) -> int:
    """Round-half-up (the settlement rule; mirrors sources._round_half_up)."""
    return math.floor(x + 0.5)


def f_bucket(c: float) -> int:
    return rhu(c_to_f(c))


def c_bucket(c: float) -> int:
    return rhu(c)


def f_ints_for_c_bucket(cb: int) -> list[int]:
    """The whole-°F integers whose °C round-half-up lands in bucket cb."""
    lo = math.ceil((cb - 0.5) * 9.0 / 5.0 + 32.0 - 1e-9)
    hi = math.floor(((cb + 0.5) * 9.0 / 5.0 + 32.0) - 1e-9)
    return list(range(lo, hi + 1))


def main() -> int:
    highs: list[tuple[str, float]] = []          # (date, daily high °C), chronological
    with open(DATA) as f:
        for line in f:
            r = json.loads(line)
            obs = r.get("obs") or []
            if obs:
                highs.append((r["date"], max(c for _h, c in obs)))
    highs.sort()

    # Grain-sanity ABORT gate: KSFO reports whole °F natively.
    integral = sum(1 for _d, h in highs if abs(c_to_f(h) - round(c_to_f(h))) <= 0.05)
    frac = integral / len(highs)
    print(f"grain sanity: {integral}/{len(highs)} daily highs integral in °F ({frac:.3f})")
    if frac < 0.95:
        print("ABORT (unscored): data grain broken — see prereg")
        return 2

    days = []                                    # per eligible day: dict of outcomes
    for t in range(1, len(highs)):
        lo = max(1, t - BACK_DAYS)
        resid = [highs[i][1] - highs[i - 1][1] for i in range(lo, t)]
        if len(resid) < MIN_RESID:
            continue
        point = highs[t - 1][1]                  # persistence proxy (°C)
        realized_f = f_bucket(highs[t][1])

        pmf_f: dict[int, int] = {}
        pmf_c: dict[int, int] = {}
        for e in resid:
            pmf_f[f_bucket(point + e)] = pmf_f.get(f_bucket(point + e), 0) + 1
            pmf_c[c_bucket(point + e)] = pmf_c.get(c_bucket(point + e), 0) + 1
        n = len(resid)
        pmf_f_p = {b: k / n for b, k in pmf_f.items()}
        # Baseline: °C mass split uniformly over each bucket's °F integers.
        pmf_cf: dict[int, float] = {}
        for cb, k in pmf_c.items():
            fints = f_ints_for_c_bucket(cb)
            for fi in fints:
                pmf_cf[fi] = pmf_cf.get(fi, 0.0) + (k / n) / len(fints)

        modal_f = max(pmf_f_p, key=lambda b: (pmf_f_p[b], -b))
        modal_cf = max(pmf_cf, key=lambda b: (pmf_cf[b], -b))
        # C3: smallest credible set reaching >=0.80 mass (buckets by descending prob).
        cover_set, mass = set(), 0.0
        for b, p in sorted(pmf_f_p.items(), key=lambda t2: (-t2[1], t2[0])):
            cover_set.add(b)
            mass += p
            if mass >= 0.80 - 1e-12:
                break
        days.append({
            "hit_f": modal_f == realized_f,
            "hit_cf": modal_cf == realized_f,
            "ls_f": math.log(max(pmf_f_p.get(realized_f, 0.0), LOG_FLOOR)),
            "ls_cf": math.log(max(pmf_cf.get(realized_f, 0.0), LOG_FLOOR)),
            "cov": realized_f in cover_set,
        })

    print(f"eligible walk-forward days: {len(days)}")
    half = len(days) // 2
    overall_pass = True
    for name, part in (("H1", days[:half]), ("H2", days[half:])):
        m = len(part)
        hit_f = sum(d["hit_f"] for d in part) / m
        hit_cf = sum(d["hit_cf"] for d in part) / m
        ls_f = sum(d["ls_f"] for d in part) / m
        ls_cf = sum(d["ls_cf"] for d in part) / m
        cov = sum(d["cov"] for d in part) / m
        c1 = hit_f > hit_cf
        c2 = ls_f > ls_cf
        c3 = 0.70 <= cov <= 0.90
        overall_pass &= c1 and c2 and c3
        print(f"{name} (n={m}): modal-hit °F {hit_f:.3f} vs °C-derived {hit_cf:.3f} "
              f"[C1 {'PASS' if c1 else 'FAIL'}] | log-score {ls_f:.3f} vs {ls_cf:.3f} "
              f"[C2 {'PASS' if c2 else 'FAIL'}] | 80%-set coverage {cov:.3f} "
              f"[C3 {'PASS' if c3 else 'FAIL'}]")
    print("VERDICT:", "PASS — all criteria on both halves" if overall_pass
          else "FAIL — dead-ledger entry required (one attempt)")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
