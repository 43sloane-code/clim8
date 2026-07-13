#!/usr/bin/env python3
"""Weather-market calibration — win rate by price on OUR settled snapshot ladders.

METHOD IMPORTED from Jon-Becker/prediction-market-analysis (win_rate_by_price), with the
two fixes that repo lacks and one honesty addition:
  FIX 1 — clustering: their analyses treat each trade as an independent Bernoulli; all
    trades in one market share ONE resolution, so their CIs are pseudo-replicated. Here
    the inference unit is the MARKET-DAY (seeded cluster bootstrap over (place, target)).
  FIX 2 — era split: pooled-history claims hide drift; we report chronological halves.
  ADDITION — the tradable curve: win rate and ROI at the RECORDED best_ask (what a taker
    pays), beside the de-vigged mid curve (what analysts plot). The gap between the two
    curves IS the cost model their headline numbers omit.

Data: verdicts.db market_snapshots (point-in-time ladders, 5 cities, all temperature
markets). DESCRIPTIVE REPORT ONLY — read-only, seeded, no served number, no strategy
claim; complements (never amends) the frozen post-peak-lag pre-registrations.
Run: PYTHONPATH=. python3 reports/weather_market_calibration.py
"""
from __future__ import annotations

import json
import math
import random
import sqlite3
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "verdicts.db")
BINS = [(0.0, .05), (.05, .15), (.15, .30), (.30, .50), (.50, .70), (.70, .85),
        (.85, .90), (.90, .95), (.95, .995)]
B = 2000
SEED = 0


def rhu(x): return math.floor(x + 0.5)


def settle_won(bucket, pm_label, realized_c, grain):
    if pm_label and bucket.get("label"):
        return 1.0 if bucket["label"].strip() == pm_label.strip() else 0.0
    if realized_c is None:
        return None
    v = rhu(realized_c * 9 / 5 + 32) if grain == "F" else rhu(realized_c)
    lo = bucket.get("lo") if bucket.get("lo") is not None else -1e9
    hi = bucket.get("hi") if bucket.get("hi") is not None else 1e9
    return 1.0 if lo <= v <= hi else 0.0


def main():
    import sys
    place_filter = sys.argv[1] if len(sys.argv) > 1 else None   # e.g. "Singapore"
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT place, target_date, buckets_json, pm_resolved_label, realized_high, grain "
        "FROM market_snapshots WHERE pm_resolved_label IS NOT NULL OR realized_high IS NOT NULL "
        "ORDER BY target_date").fetchall()
    con.close()
    if place_filter:
        rows = [r for r in rows if r[0].lower().startswith(place_filter.lower())]
        print(f"CITY SLICE: {place_filter} ({len(rows)} settled snapshots)")

    # pair = (cluster_key, mid_price, ask, won)
    pairs = []
    for place, tgt, bj, pm, rh, gr in rows:
        for b in json.loads(bj):
            won = settle_won(b, pm, rh, gr or "C")
            mid = b.get("market_prob")
            ask = b.get("best_ask")
            if won is None or not isinstance(mid, (int, float)):
                continue
            pairs.append(((place, tgt), float(mid),
                          float(ask) if isinstance(ask, (int, float)) and 0 < ask < 1 else None,
                          won))
    clusters = sorted({p[0] for p in pairs})
    by_cluster = {c: [p for p in pairs if p[0] == c] for c in clusters}
    print(f"pairs {len(pairs)} | with ask {sum(1 for p in pairs if p[2] is not None)} | "
          f"market-day clusters {len(clusters)}")

    def curve(sample_pairs):
        out = {}
        for lo, hi in BINS:
            g = [p for p in sample_pairs if lo <= p[1] < hi]
            ga = [p for p in sample_pairs if p[2] is not None and lo <= p[2] < hi]
            out[(lo, hi)] = {
                "n": len(g), "win": (sum(p[3] for p in g) / len(g)) if g else None,
                "mid": (sum(p[1] for p in g) / len(g)) if g else None,
                "n_ask": len(ga),
                "win_ask": (sum(p[3] for p in ga) / len(ga)) if ga else None,
                "ask": (sum(p[2] for p in ga) / len(ga)) if ga else None,
                "roi_ask": (sum(((1 - p[2]) / p[2]) if p[3] else -1.0 for p in ga) / len(ga))
                            if ga else None,
            }
        return out

    base = curve(pairs)
    # seeded cluster bootstrap (resample market-days) for win-rate and ROI CIs
    rng = random.Random(SEED)
    boots = {k: {"win": [], "roi": []} for k in base}
    for _ in range(B):
        samp = []
        for _ in clusters:
            samp.extend(by_cluster[rng.choice(clusters)])
        cv = curve(samp)
        for k in base:
            if cv[k]["win"] is not None:
                boots[k]["win"].append(cv[k]["win"])
            if cv[k]["roi_ask"] is not None:
                boots[k]["roi"].append(cv[k]["roi_ask"])

    def ci(xs):
        if len(xs) < 50:
            return (float("nan"), float("nan"))
        xs = sorted(xs)
        return xs[int(.025 * len(xs))], xs[int(.975 * len(xs)) - 1]

    print(f"\n{'price bin':>12} {'n':>5} {'mid':>6} {'win':>6} {'win 95% CI':>16}  "
          f"{'n_ask':>5} {'ask':>6} {'roi@ask':>8} {'roi 95% CI':>18}")
    for k in base:
        b_ = base[k]
        wlo, whi = ci(boots[k]["win"])
        rlo, rhi = ci(boots[k]["roi"])
        print(f"{k[0]:>5.2f}-{k[1]:<6.2f} {b_['n']:>5} "
              f"{b_['mid'] if b_['mid'] is not None else float('nan'):>6.3f} "
              f"{b_['win'] if b_['win'] is not None else float('nan'):>6.3f} "
              f"[{wlo:>6.3f},{whi:>6.3f}] {b_['n_ask']:>6} "
              f"{b_['ask'] if b_['ask'] is not None else float('nan'):>6.3f} "
              f"{b_['roi_ask'] if b_['roi_ask'] is not None else float('nan'):>8.3f} "
              f"[{rlo:>7.3f},{rhi:>7.3f}]")

    # era split (FIX 2)
    dates = sorted({c[1] for c in clusters})
    mid_d = dates[len(dates) // 2]
    for name, cond in (("H1", lambda c: c[1] < mid_d), ("H2", lambda c: c[1] >= mid_d)):
        sub = [p for p in pairs if cond(p[0])]
        cv = curve(sub)
        fav = cv[(.85, .90)], cv[(.90, .95)], cv[(.95, .995)]
        print(f"\n{name} (targets {'<' if name=='H1' else '>='} {mid_d}) favorite bins "
              f".85-.995: " + " | ".join(
                  f"[{lo:.2f},{hi:.2f}) n={c['n']} win {c['win'] if c['win'] is not None else float('nan'):.3f} "
                  f"roi@ask {c['roi_ask'] if c['roi_ask'] is not None else float('nan'):+.3f}"
                  for (lo, hi), c in zip([(.85,.90),(.90,.95),(.95,.995)], fav)))
    print("\nDESCRIPTIVE ONLY: clustered by market-day (the fix the source repo lacks); "
          "no strategy claim; the frozen post-peak preregs remain the only trade tests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
