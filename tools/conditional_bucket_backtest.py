#!/usr/bin/env python3
"""(a) Does CONDITIONAL (dispersion-scaled) residual spread beat the POOLED cloud
on whole-degree BUCKET BRIER — the metric the market actually settles on?

WHY THIS QUESTION, AND WHY IT IS NOT ALREADY ANSWERED
-----------------------------------------------------
compare.py turns the point verdict into per-bucket probabilities by resampling ONE
empirical residual cloud (Validation.residuals_*): every held-out day is dressed
with the SAME spread (homoscedastic).  weather_council/calibration.py already tests
the standard fix — a CONDITIONAL (heteroscedastic) cloud whose width tracks that
day's member dispersion — and the council surfaces it as a recommend-only finding.
BUT it scores that conditional cloud on **CRPS**: a continuous, sub-degree proper
score.  The market does not pay for sub-degree sharpness; it settles on a WHOLE
DEGREE bucket.  CRPS and bucket Brier diverge:
  * sharpening the cloud in the MIDDLE of a bucket improves CRPS but moves NO
    bucket mass -> zero Brier gain;
  * sharpening it near a bucket EDGE moves mass across the boundary -> a Brier gain
    (or loss) far larger than the CRPS change.
So "conditional helps on CRPS" does NOT imply "conditional helps the market", and
vice-versa.  This tool scores the IDENTICAL conditional cloud calibration.py builds
(it imports _conditional_cloud, so there is no second model to argue about) on
realized whole-degree bucket Brier (it imports edge._brier, the same scorer C7
uses), over the SAME leak-free walk-forward the council serves.  It then prints the
CRPS verdict beside the bucket verdict so the contrast is explicit.

METHOD (leak-free, mirrors Council._validate exactly)
-----------------------------------------------------
For each city it builds the SERVED panel (geocode -> _resolve_truth -> member
.analyze -> _apply_seasonal_analog) and replays the rolling-origin walk-forward:
for every held-out day, learn each member's bias+weight from STRICTLY-earlier days
(council._blend_on_date), and record (signed residual, member dispersion, blend,
observed).  Then, in that same order, for each day past the warmup:
  * POOLED  cloud = the prior residuals (the live incumbent);
  * CONDITIONAL cloud = calibration._conditional_cloud(prior, disp_today)
    (re-dressed to this day's dispersion; falls back to POOLED when dispersion is
    unusable, exactly as the CRPS eval does);
  * map each cloud through verdict+e to whole-degree buckets (whole-°C AND whole-°F
    grids), score realized bucket Brier for each, and pair the difference.
The conditional model is RECOMMENDED for a grain only if it beats pooled by a
positive margin whose seeded paired-bootstrap CI excludes 0 AND the dispersion
covariate actually tracks error (Pearson |resid|<->disp >= MIN_DISP_CORR) — the
same discipline calibration.py applies, just on the market metric.

BOUNDARIES: RECOMMEND-ONLY.  Reads data, prints a report.  Never edits code, a
served verdict, a tuned constant; never trades, never commits.

Stdlib only.  Usage:
    python3 tools/conditional_bucket_backtest.py [--target YYYY-MM-DD] [City ...]
"""
from __future__ import annotations

import datetime as dt
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from weather_council.agents import MIN_SAMPLES                       # noqa: E402
from weather_council.analog_shrink import _paired_bootstrap_ci       # noqa: E402
from weather_council.calibration import (                            # noqa: E402
    DISP_EPS, MIN_DISP_CORR, WARMUP, _conditional_cloud, _pearson,
    conditional_spread_eval,
)
from weather_council.council import Council                          # noqa: E402
from weather_council.edge import _brier                              # noqa: E402

DEFAULT_BASKET = ["London", "Hong Kong"]
WINDOW = 120                      # matches the served verdict / daily_healthcheck window
MIN_SCORED = 20                   # mirror calibration.MIN_SCORED: too-thin samples don't vote
MIN_BRIER_IMP = 1e-3              # a tiny floor; the BINDING gate is the bootstrap CI > 0


def _round_half_up(x: float) -> int:
    """Round half away from zero (deterministic), the assumed whole-degree
    settlement convention (compare.rounding_near_bucket)."""
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))


def _bucket_c(value_c: float) -> int:
    """Whole-°C settlement bucket."""
    return _round_half_up(value_c)


def _bucket_f(value_c: float) -> int:
    """Whole-°F settlement bucket: convert °C->°F, then round to the nearest whole °F."""
    return _round_half_up(value_c * 9.0 / 5.0 + 32.0)


GRAINS = (("whole-°C", _bucket_c), ("whole-°F", _bucket_f))


def _assemble_votes(council: Council, city: str, target: dt.date):
    """Build the SERVED panel for a city, member-isolated under throttle.
    Returns (truth, observed, votes) or raises on a hard failure (no geocode/truth)."""
    place = council.sources.geocode(city)
    fp, observed, w_start, w_end, truth = council._resolve_truth(place, target, WINDOW)
    votes = []
    for m in council.members:
        try:
            votes.append(m.analyze(fp, target, w_start, w_end, observed))
        except Exception:                 # per-member isolation — a timeout != a dead city
            continue
    if votes:
        # Mirror deliberate's order: out-of-season analog re-bias before validation.
        try:
            council._apply_seasonal_analog(votes, fp, target, w_start, truth)
        except Exception:
            pass
    return truth, observed, votes


def _walk_forward_records(council: Council, votes, observed) -> list[tuple[float, float, float, float]]:
    """Replay Council._validate's rolling-origin walk-forward, collecting ordered
    (signed_residual, dispersion, blend, observed) — high then low per day, the SAME
    order calib_pairs are appended in, so the conditional cloud's 'prior' matches."""
    dates = sorted(observed.keys())
    if len(dates) < 15:
        return []
    warmup = MIN_SAMPLES
    test = dates[warmup:]
    if len(test) < 5:
        return []
    records: list[tuple[float, float, float, float]] = []
    for i, d in enumerate(test):
        obs = observed.get(d)
        if obs is None:
            continue
        train = set(dates[:warmup + i])
        for _attr, idx in (("high", 0), ("low", 1)):
            res = council._blend_on_date(votes, _attr, d, train)
            if res is None:
                continue
            blend, _naive, disp, _members = res
            obs_v = obs[idx]
            records.append((obs_v - blend, disp, blend, obs_v))
    return records


def _cloud_probs(blend: float, cloud: list[float], bfn) -> dict[int, float]:
    """Verdict+residual resampling -> per-bucket probability, exactly as compare_high
    builds model_prob (bucket(blend + e) over the cloud)."""
    counts: dict[int, int] = {}
    for e in cloud:
        b = bfn(blend + e)
        counts[b] = counts.get(b, 0) + 1
    n = len(cloud)
    return {b: c / n for b, c in counts.items()} if n else {}


def _score_grain(records, bfn) -> dict | None:
    """Paired pooled-vs-conditional bucket Brier over the walk-forward, one grain."""
    diffs, bp_pool, bp_cond = [], [], []
    used_cond = 0
    for i in range(len(records)):
        prior = records[:i]
        if len(prior) < WARMUP:
            continue
        _r_i, disp_i, blend_i, obs_i = records[i]
        pooled_cloud = [r for r, _, _, _ in prior]
        cond_cloud = _conditional_cloud([(r, d) for r, d, _, _ in prior], disp_i)
        if cond_cloud is None:
            cond_cloud = pooled_cloud           # honest fallback: no usable disp -> no change
        else:
            used_cond += 1
        realized = bfn(obs_i)
        p_pool = _cloud_probs(blend_i, pooled_cloud, bfn)
        p_cond = _cloud_probs(blend_i, cond_cloud, bfn)
        labels = sorted(set(p_pool) | set(p_cond) | {realized})
        b_pool = _brier(p_pool, labels, realized)
        b_cond = _brier(p_cond, labels, realized)
        bp_pool.append(b_pool)
        bp_cond.append(b_cond)
        diffs.append(b_pool - b_cond)           # >0 means CONDITIONAL is better
    n = len(diffs)
    if n < MIN_SCORED:
        return None
    lo, hi = _paired_bootstrap_ci(diffs)
    return {
        "n": n, "used_cond": used_cond,
        "brier_pooled": statistics.mean(bp_pool),
        "brier_cond": statistics.mean(bp_cond),
        "imp": statistics.mean(diffs), "ci": (lo, hi),
    }


def _decide(imp: float, ci_lo: float, disp_corr: float) -> bool:
    """Recommend conditional only if it clears a tiny floor, its bootstrap CI excludes
    0 (the binding gate), AND the covariate genuinely tracks error."""
    return imp >= MIN_BRIER_IMP and ci_lo > 0.0 and disp_corr >= MIN_DISP_CORR


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
    basket = cities or DEFAULT_BASKET

    print("=" * 78)
    print("  (a) conditional (dispersion-scaled) vs pooled residual cloud — BUCKET BRIER")
    print(f"  target {target.isoformat()} | window {WINDOW}d | grains: whole-°C and whole-°F")
    print("  The council already scores this conditional cloud on CRPS; here it is scored")
    print("  on the WHOLE-DEGREE bucket Brier the market settles on. RECOMMEND-ONLY.")
    print("=" * 78)

    council = Council()
    recommended: list[tuple[str, str]] = []
    scored_any = False
    degraded: list[str] = []

    for city in basket:
        print(f"\n## {city}")
        try:
            truth, observed, votes = _assemble_votes(council, city, target)
        except Exception as exc:
            print(f"   [degraded] could not assemble city: {exc}")
            degraded.append(city)
            continue
        if len(votes) < 2:
            print(f"   [degraded] only {len(votes)} member(s) returned under throttle — skip")
            degraded.append(city)
            continue
        records = _walk_forward_records(council, votes, observed)
        if len(records) < MIN_SCORED + WARMUP:
            print(f"   [degraded] only {len(records)} walk-forward days (need "
                  f"≥{MIN_SCORED + WARMUP}) — re-run to warm cache")
            degraded.append(city)
            continue

        gap = truth.get("season_gap_days")
        gap_s = f"{gap}d" if gap is not None else "?"
        disp_corr = _pearson([abs(r) for r, _, _, _ in records],
                             [d for _, d, _, _ in records])
        # The council's OWN CRPS verdict on the SAME conditional cloud, for contrast.
        crps_ev = conditional_spread_eval([(r, d) for r, d, _, _ in records])
        print(f"   truth: {truth.get('kind', '?')} | season gap {gap_s} | "
              f"{len(votes)}/8 members | {len(records)} walk-forward member-days")
        print(f"   covariate: dispersion↔|resid| r = {disp_corr:+.2f} "
              f"(needs ≥{MIN_DISP_CORR:.2f} to be a principled scaler)")
        if crps_ev is not None:
            verb = "RECOMMEND" if crps_ev.recommend else "no-rec"
            print(f"   CRPS (sub-degree, council's existing check): cond {crps_ev.crps_conditional:.3f} "
                  f"vs pooled {crps_ev.crps_incumbent:.3f} "
                  f"({crps_ev.improvement_pct * 100:+.1f}%, {crps_ev.z:+.1f}σ) -> {verb}")
        else:
            print("   CRPS: too few scored days for the council's existing check")

        for gname, bfn in GRAINS:
            sc = _score_grain(records, bfn)
            if sc is None:
                print(f"   [{gname}] < {MIN_SCORED} scored days — no bucket verdict")
                continue
            scored_any = True
            rec = _decide(sc["imp"], sc["ci"][0], disp_corr)
            tag = "RECOMMEND conditional" if rec else "HOLD pooled (incumbent)"
            if rec:
                recommended.append((city, gname))
            lo, hi = sc["ci"]
            print(f"   [{gname}] bucket Brier  pooled {sc['brier_pooled']:.4f} | "
                  f"conditional {sc['brier_cond']:.4f}")
            print(f"        improvement {sc['imp']:+.4f}  90% CI[{lo:+.4f},{hi:+.4f}]  "
                  f"(conditional re-dressed {sc['used_cond']}/{sc['n']} days)")
            print(f"        -> {tag}")

    print("\n" + "=" * 78)
    if degraded:
        print(f"  DEGRADED: {len(degraded)}/{len(basket)} cities unusable "
              f"({', '.join(degraded)}) — throttling; re-run to warm the history cache.")
    if not scored_any:
        print("  RESULT: nothing scored on bucket Brier (network/throttle or too few days).")
        print("  No verdict — re-run to warm the cache.")
        print("=" * 78)
        return 1
    if recommended:
        print("  RESULT: conditional spread improves BUCKET BRIER (CI-significant) in: "
              + ", ".join(f"{c} {g}" for c, g in recommended))
        print("  FOR HUMAN REVIEW: dispersion-scaling the residual cloud sharpens the")
        print("  whole-degree bucket probabilities out-of-sample here — worth wiring into")
        print("  compare.py's bucket build (recommend-only) and confirming on more cities.")
    else:
        print("  RESULT: NO bucket-Brier gain from conditional spread across the basket.")
        print("  Even where it helps sub-degree CRPS, dispersion-scaling does not move enough")
        print("  whole-degree bucket mass to beat the pooled cloud on the metric the market")
        print("  settles on. The pooled residual cloud is fine for bucket probabilities; the")
        print("  CRPS check stays a recommend-only diagnostic, not a market-precision lever.")
    print("  (Recommend-only. No code, constant, verdict, trade, or commit was touched.)")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
