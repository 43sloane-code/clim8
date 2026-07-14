#!/usr/bin/env python3
"""Frozen probe — conditional_dispersion_cloud.md (ONE attempt; criteria frozen there).

Adjudicates the per-day member-dispersion conditional residual cloud on the two
monitored basket cities' healthcheck backtest streams. The candidate is
calibration._conditional_cloud VERBATIM; criterion 1 is the module's own
conditional_spread_eval gate; halves/coverage re-walk the identical loop using the
module's own constants (WARMUP, DISP_EPS) so no reimplementation can drift.

Run once: PYTHONPATH=.:tools python3 reports/backtest_conditional_dispersion.py
"""
from __future__ import annotations

import datetime as dt
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import daily_healthcheck as hc  # noqa: E402
from weather_council.calibration import (  # noqa: E402
    DISP_EPS, WARMUP as CAL_WARMUP, _conditional_cloud, conditional_spread_eval)
from weather_council.scoring import crps_sample, interval_coverage  # noqa: E402

CITIES = ("Manila", "Singapore")
MIN_SCORED_PER_CITY = 60          # frozen criterion 3 floor
COVERAGE_SLACK = 0.01             # frozen criterion 3: ≤ +1.0pt farther from 0.80


def city_pairs(city: str) -> list[tuple[float, float]]:
    """The council's calib_pairs object, rebuilt on the healthcheck stream: pooled
    high+low in day order, r = obs − blend, disp = pstdev(corrected panel)."""
    (_c, _p, _fp, observed, votes, _fresh, _truth) = hc._city_votes(city, dt.date.today())
    dates = sorted(observed)
    pairs: list[tuple[float, float]] = []
    for i, d in enumerate(dates[hc.WARMUP:]):
        obs = observed.get(d)
        if obs is None:
            continue
        train = set(dates[:hc.WARMUP + i])
        for attr, idx in (("high", 0), ("low", 1)):
            pred, members = hc._blend_on_date(
                votes, attr, d, train, hc.CURRENT_BIAS, hc.CURRENT_POWER)
            if pred is None:
                continue
            disp = statistics.pstdev(members) if len(members) > 1 else 0.0
            pairs.append((obs[idx] - pred, disp))
    return pairs


def walk(pairs):
    """Identical walk to conditional_spread_eval, additionally keeping per-day CRPS
    diffs (for the fold gate) and 80% coverage of both clouds (for the guard)."""
    hist: list[tuple[float, float]] = []
    diffs: list[float] = []
    cov_inc: list[int] = []
    cov_cond: list[int] = []
    for r, disp in pairs:
        prior = hist
        if len(prior) >= CAL_WARMUP:
            cloud = [pr for pr, _ in prior]
            scaled = _conditional_cloud(prior, disp)
            ci = crps_sample(cloud, r)
            cc = crps_sample(scaled, r) if scaled is not None else ci
            diffs.append(ci - cc)
            hi, _w = interval_coverage(cloud, r)
            hc2, _w = interval_coverage(scaled if scaled is not None else cloud, r)
            cov_inc.append(1 if hi else 0)
            cov_cond.append(1 if hc2 else 0)
        hist.append((r, disp))
    return diffs, cov_inc, cov_cond


def main() -> int:
    verdict_fail = False
    accruing = False
    for city in CITIES:
        pairs = city_pairs(city)
        ev = conditional_spread_eval(pairs)
        diffs, cov_inc, cov_cond = walk(pairs)
        n = len(diffs)
        print(f"\n{city}: pairs={len(pairs)} scored={n}")
        if n < MIN_SCORED_PER_CITY or ev is None:
            print(f"  ACCRUING — scored {n} < {MIN_SCORED_PER_CITY} floor")
            accruing = True
            continue
        # Criterion 1 — the module's own gate
        print(f"  [1] module gate: {ev.summary()}")
        if not ev.recommend:
            print(f"      -> FAIL criterion 1 (driver kill if r<0.10: "
                  f"disp_corr={ev.disp_corr:+.3f})")
            verdict_fail = True
        # Criterion 2 — fold gate on CRPS improvement
        half = n // 2
        i1, i2 = sum(diffs[:half]) / half, sum(diffs[half:]) / (n - half)
        ok2 = i1 > 0 and i2 > 0
        print(f"  [2] halves improvement: H1 {i1:+.4f} / H2 {i2:+.4f} "
              f"-> {'PASS' if ok2 else 'FAIL'}")
        if not ok2:
            verdict_fail = True
        # Criterion 3 — coverage guard
        ci = sum(cov_inc) / n
        cc = sum(cov_cond) / n
        ok3 = abs(cc - 0.80) <= abs(ci - 0.80) + COVERAGE_SLACK
        print(f"  [3] coverage 80%: incumbent {ci*100:.1f} / conditional {cc*100:.1f} "
              f"-> {'PASS' if ok3 else 'FAIL (worsens by >1pt)'}")
        if not ok3:
            verdict_fail = True

    print()
    if accruing:
        print("VERDICT: ACCRUING — a city is under the frozen floor; no verdict permitted.")
        return 2
    if verdict_fail:
        print("VERDICT: FAIL — dead ledger D28; the recommend-only monitor line remains "
              "the honest treatment.")
        return 1
    print("VERDICT: PASS all frozen criteria — licensed to implement in the serving "
          "path (compare) with KATs; stamp the prereg CERTIFIED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
