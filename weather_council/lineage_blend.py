"""Candidate 41 — inverse-CRPS LINEAGE blend (additive, recommend-only).

The question
-----------
The council is one forecast lineage. Two cheap reference lineages can be rebuilt
leak-free from the SAME logged (date, point, realized) stream — the very pair the
convergence layer already scores against the headline each day:

  * council      — the logged forecast `point[t]`;
  * persistence  — yesterday's observation `realized[t-1]`;
  * climatology  — the trailing mean of strictly-earlier observations.

This module asks whether BLENDING them by trailing inverse-CRPS skill beats the
best single lineage out-of-sample. Each lineage's weight is w_i ∝ 1/CRPS_i over a
trailing window; the blended predictive is the weighted MIXTURE of the three
lineages' empirical residual clouds (in observation space), whose variance is
exactly the spec's within + between decomposition:

    Var(mixture) = Σ wᵢ·σ_i²  +  Σ wᵢ·(μ_i − μ̄)²        (within)   (between)

— the between-lineage spread is captured for free because the mixture pools clouds
centred at the different lineage means. No Gaussian assumption: the same empirical
`crps_sample` the live path uses scores every lineage and the blend, so the
"blend vs best single lineage" comparison is one clean lever.

Honest scope (grounded against the real repo)
---------------------------------------------
There is only ONE independently-logged NWP lineage (the council); persistence and
climatology are reconstructed deterministically from the realized series (they
carry no independent model information beyond the obs). So this is a council-vs-
references blend, NOT a blend of independent NWP pipelines — stated plainly, never
oversold. The expectation is that the council dominates and the blend cannot beat
it; that is a successful, informative run.

Guard: < `MIN_WEIGHT_DAYS` scored trailing days ⇒ equal weights + an
UNDERPOWERED-WEIGHTS flag (inverse-CRPS weights are noise on a thin window).

Leak-free: every forecast, residual cloud, per-day CRPS, and trailing weight uses
strictly-earlier data only. Recommend-only; never mutates the served Verdict.
Stdlib only (math, statistics). Reuses scoring.crps_sample.
"""

from __future__ import annotations

__all__ = [
    "LINEAGES", "WARMUP", "CRPS_MIN", "WEIGHT_WINDOW", "MIN_WEIGHT_DAYS",
    "build_lineages", "inverse_crps_weights", "blend_moments",
    "mixture_sample", "walk_forward_blend",
]

import math
import statistics

from .scoring import crps_sample

LINEAGES = ("council", "persistence", "climatology")
WARMUP = 10              # min strictly-earlier days before a day is scored
CRPS_MIN = 10            # min residual-cloud size before a CRPS is computed
WEIGHT_WINDOW = 60       # trailing window for the inverse-CRPS weights
MIN_WEIGHT_DAYS = 30     # < this many scored days in the window ⇒ equal weights + flag
_CRPS_FLOOR = 1e-3       # floor on a lineage CRPS so 1/CRPS can't blow up
_MIX_M = 240             # quantile resolution of the weighted mixture sample


def build_lineages(rows) -> dict:
    """From a sorted (date, point, realized) stream, build the three leak-free
    lineages. Returns per-lineage parallel lists `forecast[t]` and `resid[t]`
    (resid = realized − forecast; None where the lineage is undefined, e.g.
    persistence/climatology on day 0), plus `realized` and `dates`."""
    rows = sorted(rows)
    dates = [d for d, _, _ in rows]
    point = [p for _, p, _ in rows]
    realized = [r for _, _, r in rows]
    n = len(rows)

    fc = {k: [None] * n for k in LINEAGES}
    res = {k: [None] * n for k in LINEAGES}
    run_sum = 0.0
    for t in range(n):
        # council: the logged forecast.
        fc["council"][t] = point[t]
        res["council"][t] = realized[t] - point[t]
        # persistence: yesterday's observation (known at day t).
        if t >= 1:
            fc["persistence"][t] = realized[t - 1]
            res["persistence"][t] = realized[t] - realized[t - 1]
        # climatology: trailing mean of strictly-earlier observations.
        if t >= 1:
            clim = run_sum / t
            fc["climatology"][t] = clim
            res["climatology"][t] = realized[t] - clim
        run_sum += realized[t]
    return {"dates": dates, "realized": realized, "forecast": fc, "resid": res, "n": n}


def _per_day_crps(resid) -> list:
    """Leak-free per-day CRPS series for one lineage: day t scored by its own
    strictly-earlier residual cloud (gated by CRPS_MIN). None where undefined or
    the cloud is too thin. Single forward pass."""
    n = len(resid)
    out = [None] * n
    cloud: list[float] = []
    for t in range(n):
        r = resid[t]
        if r is None:
            continue
        if len(cloud) >= CRPS_MIN:
            out[t] = crps_sample(cloud, r)
        cloud.append(r)
    return out


def inverse_crps_weights(crps_by_lineage) -> tuple[dict, bool]:
    """w_i ∝ 1/CRPS_i from each lineage's trailing-window mean CRPS. Returns
    (weights, underpowered). `crps_by_lineage` maps lineage → list of its scored
    trailing CRPS values (already windowed, Nones removed). A lineage with too few
    scored days, or all lineages thin, falls back to EQUAL weights with the
    underpowered flag set."""
    means = {}
    powered = True
    for k in LINEAGES:
        vals = crps_by_lineage.get(k, [])
        if len(vals) < MIN_WEIGHT_DAYS:
            powered = False
        means[k] = statistics.mean(vals) if vals else None

    avail = [k for k in LINEAGES if means[k] is not None]
    if not avail or not powered:
        w = {k: (1.0 / len(avail) if k in avail else 0.0) for k in LINEAGES} if avail \
            else {k: 1.0 / len(LINEAGES) for k in LINEAGES}
        return w, True

    inv = {k: 1.0 / max(means[k], _CRPS_FLOOR) for k in avail}
    tot = sum(inv.values())
    return {k: inv.get(k, 0.0) / tot for k in LINEAGES}, False


def _quantile_sorted(s: list[float], q: float) -> float:
    """Linear-interpolated quantile of an already-sorted list."""
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] * (1.0 - frac) + s[hi] * frac


def mixture_sample(clouds: dict, weights: dict, *, m: int = _MIX_M) -> list[float]:
    """A deterministic weighted-mixture sample: take ≈ w_i·m evenly-spaced
    quantiles from each lineage's (observation-space) cloud and pool them. The
    pooled sample's spread is the within + between decomposition by construction."""
    pooled: list[float] = []
    for k in LINEAGES:
        cloud = clouds.get(k) or []
        w = weights.get(k, 0.0)
        kk = int(round(w * m))
        if kk <= 0 or not cloud:
            continue
        s = sorted(cloud)
        if kk == 1:
            pooled.append(_quantile_sorted(s, 0.5))
        else:
            for j in range(kk):
                pooled.append(_quantile_sorted(s, (j + 0.5) / kk))
    return pooled


def blend_moments(means: dict, variances: dict, weights: dict) -> dict:
    """The spec's moment decomposition (for transparency/testing): blended mean,
    within-lineage variance Σ wᵢσ_i², between-lineage spread Σ wᵢ(μ_i−μ̄)², and
    their sum. This equals the variance of `mixture_sample` in the limit."""
    mu = sum(weights[k] * means[k] for k in means)
    within = sum(weights[k] * variances[k] for k in means)
    between = sum(weights[k] * (means[k] - mu) ** 2 for k in means)
    return {"mean": mu, "within": within, "between": between, "total": within + between}


def walk_forward_blend(rows, *, window: int = WEIGHT_WINDOW) -> dict:
    """Leak-free held-out walk-forward: for each scored day, weight the lineages by
    trailing inverse-CRPS and score the blended mixture, the council alone, and the
    leak-free best-pick (the lineage with the lowest trailing CRPS) against the
    realized value with the SAME empirical CRPS. Returns the per-day CRPS series and
    paired deltas (council − blend, bestpick − blend; >0 ⇒ blend better)."""
    L = build_lineages(rows)
    n = L["n"]
    realized = L["realized"]
    resid = L["resid"]
    fc = L["forecast"]
    dates = L["dates"]

    per_day = {k: _per_day_crps(resid[k]) for k in LINEAGES}

    clouds_running = {k: [] for k in LINEAGES}      # grows leak-free as t advances
    crps_blend, crps_council, crps_best = [], [], []
    deltas_council, deltas_best = [], []
    tdates, weight_log = [], []
    n_underpowered = 0

    for t in range(n):
        # advance running clouds to contain strictly-earlier residuals only AFTER
        # scoring day t — so first use clouds as they stand (res[:t]).
        # trailing windowed CRPS per lineage for the weights (strictly earlier).
        lo = max(0, t - window)
        windowed = {k: [per_day[k][j] for j in range(lo, t) if per_day[k][j] is not None]
                    for k in LINEAGES}
        weights, underpowered = inverse_crps_weights(windowed)

        # build observation-space clouds for day t (μ_i + strictly-earlier residuals)
        clouds = {}
        eligible = True
        for k in LINEAGES:
            if fc[k][t] is None or len(clouds_running[k]) < CRPS_MIN:
                clouds[k] = None
            else:
                clouds[k] = [fc[k][t] + e for e in clouds_running[k]]
        # require council eligible to score the day at all
        if clouds["council"] is None:
            for k in LINEAGES:
                if resid[k][t] is not None:
                    clouds_running[k].append(resid[k][t])
            continue

        # zero out weight on lineages with no usable cloud this day; renormalise.
        usable = {k: weights[k] for k in LINEAGES if clouds.get(k)}
        tot = sum(usable.values())
        if tot <= 0:
            wnorm = {"council": 1.0}
        else:
            wnorm = {k: usable[k] / tot for k in usable}

        y = realized[t]
        cb = crps_sample(mixture_sample({k: clouds[k] for k in wnorm}, wnorm), y)
        cc = crps_sample(clouds["council"], y)
        # leak-free best-pick: lineage with lowest trailing CRPS among the usable.
        pick = min(wnorm, key=lambda k: statistics.mean(windowed[k]) if windowed[k] else math.inf)
        cp = crps_sample(clouds[pick], y)

        crps_blend.append(cb); crps_council.append(cc); crps_best.append(cp)
        deltas_council.append(cc - cb)       # >0 ⇒ blend beats council
        deltas_best.append(cp - cb)          # >0 ⇒ blend beats the best single pick
        tdates.append(dates[t])
        weight_log.append(wnorm)
        n_underpowered += int(underpowered)

        for k in LINEAGES:
            if resid[k][t] is not None:
                clouds_running[k].append(resid[k][t])

    n_test = len(crps_blend)
    return {
        "n_rows": n, "n_test": n_test, "window": window,
        "test_dates": tdates,
        "deltas_council": deltas_council, "deltas_best": deltas_best,
        "mean_crps_blend": statistics.mean(crps_blend) if crps_blend else None,
        "mean_crps_council": statistics.mean(crps_council) if crps_council else None,
        "mean_crps_best": statistics.mean(crps_best) if crps_best else None,
        "weights": weight_log,
        "underpowered_weight_days": n_underpowered,
        "powered": n_test >= 30,
    }


def _blend_two(cloud_x, cloud_y, w_x, w_y) -> list[float]:
    """Two-lineage weighted-mixture sample (a thin oracle wrapper over the same
    quantile-pooling `mixture_sample` uses)."""
    pooled: list[float] = []
    for cloud, wv in ((cloud_x, w_x), (cloud_y, w_y)):
        kk = int(round(wv * _MIX_M))
        if kk <= 0 or not cloud:
            continue
        s = sorted(cloud)
        for j in range(kk):
            pooled.append(_quantile_sorted(s, (j + 0.5) / kk))
    return pooled


def _trailing_compare(stream_x, stream_y) -> dict:
    """Walk two mean-zero residual streams leak-free; each day weight by trailing
    inverse-CRPS and score, against the truth residual 0 (CRPS-vs-0 measures
    predictive sharpness/centring): the adaptive-weight mixture, the EQUAL-weight
    mixture, and the dominant single cloud. Returns mean CRPS for each plus the
    per-day delta (dominant_single − adaptive_blend; >0 ⇒ blend beats the single)."""
    per_x, per_y = _per_day_crps(stream_x), _per_day_crps(stream_y)
    cx, cy = [], []
    adapt, equal, single_x, d_vs_x = [], [], [], []
    for t in range(len(stream_x)):
        lo = max(0, t - WEIGHT_WINDOW)
        wx = [per_x[j] for j in range(lo, t) if per_x[j] is not None]
        wy = [per_y[j] for j in range(lo, t) if per_y[j] is not None]
        if len(cx) >= CRPS_MIN and len(cy) >= CRPS_MIN and wx and wy:
            iw_x = 1.0 / max(statistics.mean(wx), _CRPS_FLOOR)
            iw_y = 1.0 / max(statistics.mean(wy), _CRPS_FLOOR)
            tot = iw_x + iw_y
            ca = crps_sample(_blend_two(cx, cy, iw_x / tot, iw_y / tot), 0.0)
            ce = crps_sample(_blend_two(cx, cy, 0.5, 0.5), 0.0)
            csx = crps_sample(cx, 0.0)
            adapt.append(ca); equal.append(ce); single_x.append(csx)
            d_vs_x.append(csx - ca)         # >0 ⇒ blend beats lineage X
        cx.append(stream_x[t]); cy.append(stream_y[t])
    return {
        "mean_adapt": statistics.mean(adapt), "mean_equal": statistics.mean(equal),
        "mean_single_x": statistics.mean(single_x), "delta_vs_x": d_vs_x,
    }


def _self_test() -> None:
    """Deterministic oracles for exactly what this blender achieves.

    The within+between identity and the underpowered-weights fallback.

    POSITIVE control — inverse-CRPS weighting WORKS: it down-weights a clearly worse
    lineage (direct weight test) and, on a sharp+loose pair, the adaptive-weight
    mixture beats the EQUAL-weight pool (tightening toward the better lineage).

    NEGATIVE control / overfit guard — when one lineage DOMINATES throughout, the
    blend cannot significantly beat that dominant single lineage (soft selection
    lands at, not below, the best single; the blend's value is robustness, not a
    free CRPS win — stated honestly because it predicts the real-data result).
    """
    import random
    from tools.daily_healthcheck import _paired_bootstrap_ci

    # within + between identity holds for blend_moments.
    bm = blend_moments({"council": 0.0, "persistence": 2.0, "climatology": -1.0},
                       {"council": 1.0, "persistence": 1.0, "climatology": 1.0},
                       {"council": 0.5, "persistence": 0.3, "climatology": 0.2})
    assert abs(bm["total"] - (bm["within"] + bm["between"])) < 1e-12, bm
    assert bm["between"] > 0.0, bm

    # underpowered fallback: thin window ⇒ equal weights + flag.
    w, under = inverse_crps_weights({"council": [0.3] * 5, "persistence": [0.4] * 5,
                                     "climatology": [0.5] * 5})
    assert under and abs(sum(w.values()) - 1.0) < 1e-12 and len(set(w.values())) == 1, (w, under)

    # weighting orders by skill: lower trailing CRPS ⇒ higher weight (and powered).
    w2, under2 = inverse_crps_weights({"council": [0.2] * 40, "persistence": [0.5] * 40,
                                       "climatology": [1.0] * 40})
    assert not under2, w2
    assert w2["council"] > w2["persistence"] > w2["climatology"] > 0.0, w2

    rng = random.Random(41)
    n = 360
    dom = [rng.gauss(0.0, 0.4) for _ in range(n)]      # always sharp (dominant)
    bad = [rng.gauss(0.0, 2.0) for _ in range(n)]      # always loose

    # POSITIVE: adaptive weighting beats the naive equal-weight pool.
    cmp = _trailing_compare(dom, bad)
    assert cmp["mean_adapt"] <= cmp["mean_equal"] + 1e-9, (cmp["mean_adapt"], cmp["mean_equal"])

    # NEGATIVE / overfit guard: blend does NOT significantly beat the dominant single.
    pt, lo_, hi_, _ = _paired_bootstrap_ci(cmp["delta_vs_x"])
    assert not (lo_ is not None and lo_ > 0.0), ("blend must NOT beat a dominant lineage", pt, lo_, hi_)

    print("lineage_blend self-test PASSED "
          "(within+between identity; thin window⇒equal weights+flag; inverse-CRPS "
          "orders by skill and beats equal-weight pool; dominant lineage⇒blend cannot "
          "beat it — no manufactured edge)")


if __name__ == "__main__":
    _self_test()
