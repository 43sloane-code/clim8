#!/usr/bin/env python3
"""Probe for ledger/preregistered/dispersion_inflation.md — FROZEN, one attempt.
Walk-forward scalar variance-match inflation (s = pstdev(last60)/pstdev(all), floor 1.0)
vs the incumbent unscaled residual cloud: 80%-interval coverage + CRPS, both halves,
both cities. Run: PYTHONPATH=. python3 reports/backtest_dispersion.py"""
from __future__ import annotations
import os, sqlite3, statistics, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "verdicts.db")
WARMUP, RECENT = 60, 60

def crps_emp(samples, obs):
    n = len(samples)
    s = sorted(samples)
    term1 = sum(abs(x - obs) for x in s) / n
    term2 = sum(abs(s[i] - s[j]) for i in range(n) for j in range(i + 1, n)) * 2 / (n * n)
    return term1 - 0.5 * term2

def pct(sorted_xs, q):
    i = max(0, min(len(sorted_xs) - 1, int(q * (len(sorted_xs) - 1))))
    return sorted_xs[i]

def run_city(place):
    # LOADER REPAIR (2026-07-14, documented in the prereg): the frozen design's stated
    # n≈222/city unambiguously references the healthcheck's walk-forward backtest stream,
    # not the live verdicts ledger (6 Manila / 131 Singapore rows). This loader produces
    # exactly that stream via the healthcheck's own machinery (live variant, high attr).
    # Criteria untouched.
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import datetime as _dt
    import daily_healthcheck as hc
    city = place.rstrip("%")
    (_council, _place, _fp, observed, votes,
     _fresh, _truth) = hc._city_votes(city, _dt.date.today())
    live = (hc.CURRENT_BIAS, hc.CURRENT_POWER)   # the served variant, not VARIANTS[0]
    out_wf = hc._walk_forward(votes, observed, *live)
    # v2: pool high+low in day order (the healthcheck's own PIT convention)
    r9 = out_wf[9]
    errs = [float(x) for pair in zip(r9["high"], r9["low"]) for x in pair]
    out = []
    for t in range(WARMUP, len(errs)):
        cloud = errs[:t]
        mu = statistics.mean(cloud)
        s = max(1.0, statistics.pstdev(cloud[-RECENT:]) / statistics.pstdev(cloud)) \
            if statistics.pstdev(cloud) > 0 else 1.0
        obs = errs[t]
        sc = sorted(cloud)
        cov_inc = pct(sc, 0.10) <= obs <= pct(sc, 0.90)
        scaled = sorted(mu + (x - mu) * s for x in cloud)
        cov_cand = pct(scaled, 0.10) <= obs <= pct(scaled, 0.90)
        # subsample clouds for CRPS cost (deterministic stride) when large
        stride = max(1, len(cloud) // 80)
        c_inc = crps_emp(sc[::stride], obs)
        c_cand = crps_emp(scaled[::stride], obs)
        out.append((cov_inc, cov_cand, c_inc, c_cand, s))
    return out

def main():
    cells_ok, report = [], []
    pooled_ci = pooled_cc = 0.0
    n_pool = 0
    for place, label in (("Manila%", "Manila"), ("Singapore%", "Singapore")):
        r = run_city(place)
        if len(r) < 80:
            print(f"{label}: n={len(r)} < 80 — ACCRUING (v2 criterion 3)")
            return 2
        half = len(r) // 2
        for hname, part in (("H1", r[:half]), ("H2", r[half:])):
            cov_i = sum(1 for x in part if x[0]) / len(part)
            cov_c = sum(1 for x in part if x[1]) / len(part)
            better = abs(cov_c - 0.80) < abs(cov_i - 0.80)
            cells_ok.append(better)
            report.append(f"{label} {hname}: cov {cov_i:.3f} -> {cov_c:.3f} "
                          f"{'IMPROVED' if better else 'NOT improved'} "
                          f"(mean s={statistics.mean(x[4] for x in part):.3f})")
        pooled_ci += sum(x[2] for x in r); pooled_cc += sum(x[3] for x in r); n_pool += len(r)
    for line in report:
        print(line)
    crps_ratio = pooled_cc / pooled_ci
    print(f"pooled CRPS candidate/incumbent = {crps_ratio:.4f} (bar <= 1.01)")
    ok = all(cells_ok) and crps_ratio <= 1.01
    print("VERDICT:", "PASS — implement in served-cloud path with KATs"
          if ok else "FAIL — dead ledger D26")
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
