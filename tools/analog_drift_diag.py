#!/usr/bin/env python3
"""(b) Does the seasonal-analog PANEL bias drift year-over-year?

WHY THIS QUESTION
-----------------
The council's seasonal-analog correction pools 2022-2025 analog days with EQUAL
year weight and subtracts the resulting bias at full strength.  The 8-member blend
averages away each member's idiosyncratic noise — but it canNOT average away a
COMMON-MODE bias (every member skewed the same way by a warming trend, a model
upgrade, or a station change).  If that common-mode bias DRIFTS across years, then
equal-year pooling mis-corrects the current year, and recency-weighting (or a trend
term) would tighten the verdict's CENTER — which is what moves a whole-degree bucket.
If it does NOT drift beyond sampling noise, or drifts by less than a bucket width,
the trend lever is moot and the full swap is fine.  This tool decides that, cheaply,
on real data, before any modeling is built.

DECISION YARDSTICKS
-------------------
DESCRIPTIVE (how big is the wobble?) — context only, NOT the verdict:
  * sampling noise — the per-year panel-bias SE.  Drift must exceed it to be more
    than a small-sample mirage.
  * bucket width   — a whole-°F market bucket spans 5/9 ≈ 0.556 °C; a whole-°C
    bucket spans 1.0 °C.  Drift must APPROACH this to threaten a BUCKET FLIP.
  The per-year table classifies the range NONE / MOOT / REAL on these.  But with
  only ~4 points the max−min range is upward-biased, and the per-year SEs are
  understated (members within a year share the same weather days, so their errors
  are correlated).  The range ALONE therefore cannot justify a trend term.

DECISIVE (is the wobble exploitable out-of-sample?) — this IS the verdict:
  * walk-forward-by-year — train each member's analog bias on the years STRICTLY
    BEFORE Y, predict Y, blend with the live inverse-variance weights, and pit
    EQUAL-year pooling (the incumbent full swap) against RECENCY-weighted and
    LAST-YEAR-only analog bias on held-out MAE, with a seeded paired-bootstrap CI.
    If the drift is a forecastable trend, recency beats equal out-of-sample; if it
    is sampling noise, it does not.  The range describes; this decides.

So the FINAL verdict per city/variable is:
  EXPLOITABLE      recency/last-year beats equal by ≥ MIN_IMPROVEMENT_C with CI>0
                   -> a recency/trend term is worth building; test it on bucket Brier in (a)
  NOT-EXPLOITABLE  it does not -> equal-year pooling is fine; the precision lever is
                   the residual SHAPE (a), not the analog CENTER (b)

BOUNDARIES: RECOMMEND-ONLY.  Reads data, prints a report.  Never edits code or a
served verdict, never trades, never commits.

Stdlib only.  Usage:
    python3 tools/analog_drift_diag.py [--target YYYY-MM-DD] [City ...]
"""
from __future__ import annotations

import datetime as dt
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.analog_shrink_backtest import ATTRS, _assemble_city   # noqa: E402  (same real-data path)
from weather_council.analog_shrink import (                       # noqa: E402
    MIN_ANALOG, MIN_HELDOUT_DAYS, MIN_IMPROVEMENT_C, MIN_MEMBERS, _paired_bootstrap_ci,
)
from weather_council.council import WEIGHT_POWER, Council          # noqa: E402

F_BUCKET_C = 5.0 / 9.0        # a whole-°F market bucket, in °C  (~0.556)
C_BUCKET_C = 1.0              # a whole-°C market bucket, in °C
MIN_PER_YEAR = 10            # analog pairs a member needs IN a year to contribute that year
MOOT_FRAC = 0.30             # drift below this fraction of an °F bucket is moot for the market
RECENCY_DECAY = 0.5          # per-year exponential weight (half-life 1 yr) for the recency variant


def _ivw(values: list[float], ses: list[float]) -> tuple[float, float]:
    """Inverse-variance (precision) weighted mean and its standard error — the
    minimum-variance estimate of a shared (common-mode) panel bias."""
    prec = [1.0 / (s * s) for s in ses if s > 0 and math.isfinite(s)]
    if len(prec) != len(values) or not prec:
        m = statistics.mean(values) if values else 0.0
        return m, float("inf")
    wsum = sum(prec)
    mean = sum(p * v for p, v in zip(prec, values)) / wsum
    return mean, math.sqrt(1.0 / wsum)


def _member_year_bias(pairs):
    """Group one member's (date, f, o) analog pairs by year -> {year: (bias, se, n)}."""
    by_year: dict[str, list[float]] = {}
    for day, f, o in pairs:
        by_year.setdefault(day[:4], []).append(f - o)
    out = {}
    for y, diffs in by_year.items():
        if len(diffs) < MIN_PER_YEAR:
            continue
        bias = statistics.mean(diffs)
        sd = statistics.pstdev(diffs) if len(diffs) > 1 else 0.0
        se = sd / math.sqrt(len(diffs)) if diffs else float("inf")
        out[y] = (bias, se, len(diffs))
    return out


def _ols_slope(xs: list[float], ys: list[float]) -> tuple[float, float | None]:
    """Unweighted OLS slope (°C per year) and its t-stat (None if < 3 points)."""
    k = len(xs)
    if k < 2:
        return 0.0, None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0, None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    if k < 3:
        return slope, None
    intercept = my - slope * mx
    resid = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
    s2 = sum(r * r for r in resid) / (k - 2)
    se_slope = math.sqrt(s2 / sxx) if sxx > 0 and s2 > 0 else 0.0
    t = slope / se_slope if se_slope > 0 else None
    return slope, t


def _panel_year_series(members_pairs: dict[str, list]) -> list[tuple[str, float, float, int]]:
    """Per-year common-mode panel bias: precision-weighted mean across members of
    each member's per-year bias.  Returns [(year, b_Y, se_Y, n_members), ...] sorted."""
    per_member = {mid: _member_year_bias(p) for mid, p in members_pairs.items()}
    years = sorted({y for d in per_member.values() for y in d})
    series = []
    for y in years:
        vals, ses = [], []
        for d in per_member.values():
            if y in d:
                b, se, _ = d[y]
                vals.append(b)
                ses.append(se)
        if len(vals) < 2:
            continue
        b_y, se_y = _ivw(vals, ses)
        series.append((y, b_y, se_y, len(vals)))
    return series


def _wf_recency(members_pairs: dict[str, list], *, weight_power: int = WEIGHT_POWER,
                decay: float = RECENCY_DECAY):
    """Walk-forward-by-year (leak-free): for each year Y, train each member's analog
    bias on the years STRICTLY BEFORE Y and predict year Y.  Compare three year-
    weightings of the analog bias with the SAME inverse-variance member weights:
      EQUAL   — pool all prior analog days equally (the live full-swap incumbent);
      RECENCY — weight each day by its year's recency (exp decay, most recent = 1);
      LASTYR  — use only the most-recent prior year.
    Returns held-out blended MAEs and the paired-bootstrap improvement of RECENCY and
    LASTYR over EQUAL, or None if too few held-out days.  This is the decisive test:
    if the year-to-year wobble is a forecastable trend, recency beats equal out-of-
    sample; if it is sampling noise, it does not."""
    years = sorted({d[:4] for ps in members_pairs.values() for (d, _, _) in ps})
    err = {k: [] for k in ("equal", "recency", "lastyr")}
    for yi, Y in enumerate(years):
        if yi == 0:
            continue                                   # need ≥1 strictly-prior year
        prior = years[:yi]
        trained: dict[str, dict] = {}
        for mid, ps in members_pairs.items():
            tr = [(d, f, o) for (d, f, o) in ps if d[:4] in prior]
            if len(tr) < MIN_ANALOG:
                continue
            diffs = [f - o for _, f, o in tr]
            b_eq = statistics.mean(diffs)
            mae_eq = statistics.mean(abs(x - b_eq) for x in diffs)   # weight from EQUAL resid (fixed)
            wd = [decay ** (len(prior) - 1 - prior.index(d[:4])) for d, _, _ in tr]
            sw = sum(wd) or 1.0
            b_rec = sum(w * (f - o) for w, (_, f, o) in zip(wd, tr)) / sw
            last = prior[-1]
            dl = [f - o for (d, f, o) in tr if d[:4] == last]
            b_last = statistics.mean(dl) if dl else b_eq
            trained[mid] = {"w": 1.0 / max(mae_eq, 0.1) ** weight_power,
                            "equal": b_eq, "recency": b_rec, "lastyr": b_last}
        if len(trained) < MIN_MEMBERS:
            continue
        held = sorted({d for mid in trained for (d, _, _) in members_pairs[mid] if d[:4] == Y})
        for day in held:
            acc = {k: [0.0, 0.0] for k in err}
            obs = None
            for mid in trained:
                fo = next(((f, o) for (d, f, o) in members_pairs[mid] if d == day), None)
                if fo is None:
                    continue
                f, o = fo
                obs = o
                w = trained[mid]["w"]
                for k in err:
                    acc[k][0] += w * (f - trained[mid][k])
                    acc[k][1] += w
            if obs is None or acc["equal"][1] <= 0:
                continue
            for k in err:
                num, den = acc[k]
                if den > 0:
                    err[k].append(abs(num / den - obs))
    n = len(err["equal"])
    if n < MIN_HELDOUT_DAYS:
        return None
    mae = {k: statistics.mean(err[k]) for k in err}
    d_rec = [e - r for e, r in zip(err["equal"], err["recency"])]
    d_last = [e - r for e, r in zip(err["equal"], err["lastyr"])]
    lo_r, hi_r = _paired_bootstrap_ci(d_rec)
    lo_l, hi_l = _paired_bootstrap_ci(d_last)
    return {
        "n": n, "folds": len(years) - 1,
        "mae_equal": mae["equal"], "mae_recency": mae["recency"], "mae_lastyr": mae["lastyr"],
        "imp_rec": statistics.mean(d_rec), "ci_rec": (lo_r, hi_r),
        "imp_last": statistics.mean(d_last), "ci_last": (lo_l, hi_l),
    }


def _classify(drift_range: float, mean_se: float) -> tuple[str, str]:
    moot_thresh = MOOT_FRAC * F_BUCKET_C
    if drift_range <= mean_se:
        return "NONE", "within sampling noise — equal-year pooling is fine"
    if drift_range < moot_thresh:
        return "MOOT", (f"real but < {MOOT_FRAC:.0%} of an °F bucket "
                        f"({drift_range:.2f} < {moot_thresh:.2f} °C) — can't flip a bucket")
    return "REAL", (f"drift {drift_range:.2f} °C ≈ {100 * drift_range / F_BUCKET_C:.0f}% of an "
                    f"°F bucket — but the range only DESCRIBES; the walk-forward decides")


def _wf_decide(wf):
    """The verdict gate, mirroring analog_shrink._decide: a recency/last-year variant
    is worth building ONLY if it beats equal-year pooling out-of-sample by at least
    MIN_IMPROVEMENT_C with a paired-bootstrap CI lower bound strictly above 0.
    Returns (exploitable | None, reason).  None means no walk-forward test was possible."""
    if wf is None:
        return None, "too few held-out days for a leak-free walk-forward test"
    best = None
    for label, ik, ck in (("recency", "imp_rec", "ci_rec"), ("last-year", "imp_last", "ci_last")):
        imp, (lo, hi) = wf[ik], wf[ck]
        if imp >= MIN_IMPROVEMENT_C and lo > 0.0 and (best is None or imp > best[1]):
            best = (label, imp, lo, hi)
    if best is None:
        return False, (f"no variant clears the gate (≥{MIN_IMPROVEMENT_C:.2f} °C & CI>0) — "
                       f"year-to-year wobble does not forecast the next year")
    return True, (f"{best[0]} beats equal by {best[1]:+.3f} °C "
                  f"(CI[{best[2]:+.3f},{best[3]:+.3f}]) out-of-sample")


def main(argv: list[str]) -> int:
    target = dt.date.today()
    cities: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--target" and i + 1 < len(argv):
            target = dt.date.fromisoformat(argv[i + 1])
            i += 2
            continue
        cities.append(argv[i])
        i += 1
    basket = cities or ["London", "Hong Kong"]

    print("=" * 74)
    print("  (b) seasonal-analog PANEL bias — year-over-year drift diagnostic")
    print(f"  target {target.isoformat()} | ±21d analog window | "
          f"°F bucket = {F_BUCKET_C:.3f} °C, °C bucket = {C_BUCKET_C:.1f} °C")
    print("  RECOMMEND-ONLY: reads data, prints a report. Edits nothing; trades nothing.")
    print("=" * 74)

    council = Council()
    verdicts = []
    degraded = []

    for city in basket:
        print(f"\n## {city}")
        try:
            kind, n_obs, season_gap, per_member, pairs_by_attr = _assemble_city(
                council, city, target)
        except Exception as exc:
            print(f"   [degraded] could not assemble city: {exc}")
            degraded.append(city)
            continue
        gap_s = f"{season_gap}d" if season_gap is not None else "?"
        print(f"   truth: {kind} | trailing-window season gap: {gap_s}")

        for name, _ in ATTRS:
            mp = pairs_by_attr[name]
            series = _panel_year_series(mp)
            if len(series) < 2:
                print(f"   [{name}] < 2 usable analog years (need ≥{MIN_PER_YEAR} pairs/yr) — skip")
                continue

            # --- DESCRIPTIVE: what does the year-over-year wobble look like? ---
            b_all, _ = _ivw([b for _, b, _, _ in series], [se for _, _, se, _ in series])
            xs = [float(k) for k in range(len(series))]
            ys = [b for _, b, _, _ in series]
            slope, t = _ols_slope(xs, ys)
            drift_range = max(ys) - min(ys)
            mean_se = statistics.mean([se for _, _, se, _ in series])
            recent_gap = ys[-1] - b_all
            desc, desc_why = _classify(drift_range, mean_se)

            print(f"   [{name}] per-year common-mode bias (precision-weighted across members):")
            for y, b_y, se_y, k in series:
                print(f"        {y}: {b_y:+.2f} ± {se_y:.2f} °C  ({k} members)")
            t_s = f"{t:+.1f}" if t is not None else "n/a"
            print(f"        pooled(all-yr) {b_all:+.2f} | range {drift_range:.2f} | "
                  f"mean SE {mean_se:.2f} | slope {slope:+.3f} °C/yr (t={t_s}) | "
                  f"recent−pooled {recent_gap:+.2f}")
            print(f"        descriptive: {desc} — {desc_why}")

            # --- DECISIVE: is the wobble exploitable out-of-sample? ---
            wf = _wf_recency(mp)
            exploit, why = _wf_decide(wf)
            if wf is None:
                print(f"        walk-forward: {why}")
            else:
                print(f"        walk-forward ({wf['n']} held-out days, {wf['folds']} folds, "
                      f"train strictly on prior years):")
                print(f"          EQUAL   MAE {wf['mae_equal']:.3f} °C   (incumbent full swap)")
                print(f"          RECENCY MAE {wf['mae_recency']:.3f} °C   "
                      f"imp {wf['imp_rec']:+.3f}  CI[{wf['ci_rec'][0]:+.3f},{wf['ci_rec'][1]:+.3f}]")
                print(f"          LASTYR  MAE {wf['mae_lastyr']:.3f} °C   "
                      f"imp {wf['imp_last']:+.3f}  CI[{wf['ci_last'][0]:+.3f},{wf['ci_last'][1]:+.3f}]")
            tag = "EXPLOITABLE" if exploit else ("NO-TEST" if exploit is None else "NOT-EXPLOITABLE")
            print(f"        => {tag}: {why}")
            verdicts.append((city, name, tag))

    print("\n" + "=" * 74)
    if degraded:
        print(f"  DEGRADED: {len(degraded)}/{len(basket)} cities unusable "
              f"({', '.join(degraded)}) — throttling; re-run to warm cache.")
    exploit = [v for v in verdicts if v[2] == "EXPLOITABLE"]
    scored = [v for v in verdicts if v[2] in ("EXPLOITABLE", "NOT-EXPLOITABLE")]
    if not scored:
        print("  RESULT: nothing scored out-of-sample (network/throttle, or too few held-out")
        print("  days for a walk-forward). No drift verdict — re-run to warm the history cache.")
        print("=" * 74)
        return 1
    if exploit:
        print("  RESULT: leak-free walk-forward finds EXPLOITABLE analog drift in: "
              + ", ".join(f"{c} {a}" for c, a, _ in exploit))
        print("  FOR HUMAN REVIEW: a recency/trend weighting of the analog years beats equal-")
        print("  year pooling OUT-OF-SAMPLE here — worth building and testing on bucket Brier (a).")
        print("  Nothing was applied; this is a recommendation for review.")
    else:
        print("  RESULT: NO exploitable analog drift across the basket. The year-to-year wobble")
        print("  in the common-mode bias does NOT forecast the next year — recency- and last-")
        print("  year-weighting do not beat equal-year pooling out-of-sample. Equal-year pooling")
        print("  is fine; the precision lever is the residual SHAPE (a), not the analog CENTER (b).")
    print("  (Recommend-only. No code, constant, verdict, trade, or commit was touched.)")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
