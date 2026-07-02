"""two_band_backfill.py — the pre-registered Gate 3 + Gate 1 kill-check for a two-bucket band.

Falsifiable pre-registration (see ledger/preregistered/singapore_two_band.md):
  Gate 3 — the council's per-city residual mean must be within ±0.3°C of zero (else a
           quantile band is mis-centered and its width is a lie).
  Gate 1 — a two-bucket band must contain the settled bucket on ≥75% of settled days;
           below 60% (6/10) the two-band is DEAD at current σ → revert to three buckets.

Computes, leave-one-out, per city: residual mean/median/sd/skew, and the containment of
three two-bucket constructions — cool [P-1,P], warm [P,P+1], and the data-driven 15th–85th
quantile band (whose average WIDTH tells you how many buckets honest coverage actually needs).
Read-only; never moves a served number. "Test one, log all": every city is scored.

RESULT ON THE FROZEN RECORD (2026-07-02): Singapore cool-skew 6/12=50% → DEAD (below the 60%
floor). No two-bucket skew clears 75%; the quantile band needs ~3.5 buckets. Residuals are
BIMODAL (warm body: point under-calls; + a deep discrete cold tail on squall days, σ 1.36) —
the cool-skew premise is falsified (misses lean WARM), and a −3°C squall day is unreachable by
any static one-bucket skew. Served band stays THREE buckets.

Run:  PYTHONPATH=. python3 tools/two_band_backfill.py [--city singapore]
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "verdicts.db"
GATE3_TOL = 0.30
GATE1_TARGET = 0.75
GATE1_FLOOR = 0.60


def _b(x):
    return math.floor(x + 0.5)                    # round-half-up whole °C


def _quantile(xs, q):
    xs = sorted(xs)
    i = q * (len(xs) - 1)
    lo = int(i)
    return xs[lo] + (xs[min(lo + 1, len(xs) - 1)] - xs[lo]) * (i - lo)


def load_city_days():
    """{place: [(date, mean_point, actual_high), ...]} from settled verdicts (deduped per day)."""
    con = sqlite3.connect(DB)
    try:
        rows = con.execute(
            "SELECT place, target_date, high, actual_high FROM verdicts "
            "WHERE actual_high IS NOT NULL AND high IS NOT NULL").fetchall()
    finally:
        con.close()
    by = defaultdict(lambda: defaultdict(lambda: [[], None]))
    for p, td, h, a in rows:
        by[p][td][0].append(h)
        by[p][td][1] = a
    out = {}
    for p, days in by.items():
        out[p] = sorted((td, sum(hs) / len(hs), a) for td, (hs, a) in days.items())
    return out


def backfill(days):
    """Return (gate3_mean, sd, skew, containment_dict, rows). LOO."""
    r = [a - pt for _, pt, a in days]
    n = len(r)
    mean, med, sd = statistics.mean(r), statistics.median(r), statistics.pstdev(r)
    skew = "LEFT/cold" if mean < med - 0.05 else ("right/warm" if mean > med + 0.05 else "~sym")
    cool = warm = quant = 0
    widths, rows = [], []
    for i, (td, pt, a) in enumerate(days):
        oth = [r[j] for j in range(n) if j != i]
        P, S = _b(pt), _b(a)
        ch, wh = S in {P - 1, P}, S in {P, P + 1}
        lo, hi = pt + _quantile(oth, 0.15), pt + _quantile(oth, 0.85)
        qb = set(range(_b(lo), _b(hi) + 1))
        qh = S in qb
        cool, warm, quant = cool + ch, warm + wh, quant + qh
        widths.append(len(qb))
        rows.append((td, round(pt, 1), S, a - pt, [P - 1, P], ch, [P, P + 1], wh, sorted(qb), qh))
    return (mean, sd, skew,
            {"cool": (cool, n), "warm": (warm, n), "quant": (quant, n),
             "qwidth": statistics.mean(widths)}, rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default=None, help="print the per-day rows for this city")
    args = ap.parse_args()
    cities = load_city_days()

    print("=== GATE 3 — residual mean (need |mean| ≤ 0.3°C) + shape | GATE 1 — two-band containment (LOO) ===")
    print(f"  {'city':22}{'n':>3}{'mean':>7}{'sd':>6}{'skew':>11}   "
          f"{'cool[P-1,P]':>12}{'warm[P,P+1]':>12}{'quant(w)':>12}  verdict")
    for p in sorted(cities):
        days = cities[p]
        if len(days) < 4:
            continue
        mean, sd, skew, cont, _ = backfill(days)
        cool_h, n = cont["cool"]
        warm_h, _ = cont["warm"]
        q_h, _ = cont["quant"]
        g3 = "g3✓" if abs(mean) <= GATE3_TOL else "g3✗"
        rate = cool_h / n
        v = "DEAD (<60%)" if rate < GATE1_FLOOR else ("PASS" if rate >= GATE1_TARGET else "weak")
        print(f"  {p[:22]:22}{n:>3}{mean:>+7.2f}{sd:>6.2f}{skew:>11}   "
              f"{f'{cool_h}/{n}':>12}{f'{warm_h}/{n}':>12}{f'{q_h}/{n}(w{cont['qwidth']:.1f})':>12}  {g3} {v}")

    if args.city:
        key = next((k for k in cities if args.city.lower() in k.lower()), None)
        if key:
            _, _, _, _, rows = backfill(cities[key])
            print(f"\n=== {key} — per-day rows ===")
            print(f"  {'date':12}{'point':>6}{'settled':>8}{'resid':>7}{'cool':>10}{'warm':>10}{'quantile':>16}")
            for td, pt, S, res, cb, ch, wb, wh, qb, qh in rows:
                tail = "  <- COLD TAIL" if res < -1.3 else ""
                cs = f"{cb} {'Y' if ch else 'n'}"
                ws = f"{wb} {'Y' if wh else 'n'}"
                qs = f"{qb} {'Y' if qh else 'n'}"
                print(f"  {td:12}{pt:>6.1f}{S:>8}{res:>+7.1f}{cs:>12}{ws:>12}{qs:>18}{tail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
