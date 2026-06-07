"""Conditional predictive-spread calibration — a recommend-only ML layer.

What the council ships today
----------------------------
The council dresses its point verdict with ONE empirical residual cloud
(Validation.residuals_*): every held-out day is given the *same* spread,
regardless of how uncertain that particular day actually was. That is a
homoscedastic predictive distribution. But forecast error is rarely constant: on
days the member models scatter widely (a fast-moving front, a sea-breeze
toss-up) the error is genuinely larger than on days they agree tightly. A single
cloud is too wide on the easy days and too narrow on the hard ones.

What this module measures
-------------------------
The standard fix is a *conditional* (heteroscedastic) predictive distribution:
scale the spread by a per-day uncertainty covariate. The covariate here is the
**member dispersion** — the spread of the bias-corrected member forecasts on that
day, the leak-free historical analog of the live ensemble spread. For each
held-out day we re-dress the prior residual cloud so its width tracks today's
dispersion (standardise prior residuals by their own day's dispersion, then
re-scale by today's), and score it with the SAME proper rule (CRPS) on the SAME
held-out days as the incumbent cloud.

Discipline (why this is signal, not noise)
------------------------------------------
  * Leak-free: every day is scored using STRICTLY-earlier (residual, dispersion)
    pairs only — identical walk-forward to Council._validate.
  * Proper rule: CRPS, so a "sharper" distribution only wins if it is also
    calibrated; over-confidence is punished.
  * Gated: the conditional model is *recommended* only when it beats the
    incumbent by more than the standard error of the paired per-day CRPS
    difference (a real improvement past the noise floor), AND the covariate
    actually tracks error (positive dispersion↔|error| correlation). Otherwise
    this reports "no recommendation" — the homoscedastic cloud stands.
  * Recommend-only: this NEVER changes the verdict the council serves. It emits a
    finding for human review, exactly like the daily self-improvement check.

Stdlib only (math, statistics); reuses scoring.crps_sample.
"""

from __future__ import annotations

__all__ = [
    'CalibrationEval', 'conditional_spread_eval'
]

import math
import statistics
from dataclasses import dataclass

from .scoring import crps_sample

# A day's predictive distribution needs a minimum prior cloud before its CRPS
# means anything — the same floor Council._validate uses for the incumbent cloud.
WARMUP = 10
# Dispersions at or below this (°C) are treated as "no usable spread signal" and
# fall back to the incumbent cloud for that day, rather than dividing by ~0.
DISP_EPS = 1e-3
# Minimum scored days before any recommendation is even considered — small
# samples make the paired difference too noisy to act on.
MIN_SCORED = 20
# Improvement must clear this many standard errors of the paired per-day CRPS
# difference to be called real (≈ a 2-sigma paired test). Past the noise floor.
Z_THRESHOLD = 2.0
# The covariate must actually track error this much (Pearson |residual| vs
# dispersion) for conditioning on it to be principled, not a lucky fit.
MIN_DISP_CORR = 0.10


@dataclass(frozen=True)
class CalibrationEval:
    """The head-to-head between the incumbent (single residual cloud) and the
    conditional (dispersion-scaled) predictive distribution, with the full
    statistics behind the recommend/decline decision."""
    n_scored: int
    crps_incumbent: float          # mean CRPS of the homoscedastic cloud
    crps_conditional: float        # mean CRPS of the dispersion-scaled cloud
    improvement: float             # crps_incumbent − crps_conditional (>0 = better)
    improvement_pct: float         # improvement / crps_incumbent
    improvement_se: float          # SE of the paired per-day CRPS difference
    z: float                       # improvement / improvement_se
    disp_corr: float               # Pearson(|residual|, dispersion) over all pairs
    recommend: bool

    def summary(self) -> str:
        verb = "RECOMMEND" if self.recommend else "no recommendation"
        return (
            f"conditional predictive spread (scale by member dispersion): "
            f"CRPS {self.crps_conditional:.3f} vs incumbent {self.crps_incumbent:.3f} "
            f"({self.improvement_pct * 100:+.1f}%, {self.z:+.1f}σ, "
            f"disp↔|err| r={self.disp_corr:+.2f}, n={self.n_scored}) -> {verb}"
        )


def _pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    if sxx <= 0 or syy <= 0:
        return 0.0
    return sxy / math.sqrt(sxx * syy)


def _conditional_cloud(prior: list[tuple[float, float]], disp_today: float):
    """Re-dress the prior residual cloud so its spread tracks `disp_today`.

    Standardise each prior residual by its OWN day's dispersion, then re-scale by
    today's dispersion, keeping the cloud centred where the incumbent centres it
    (the prior residual mean) so this isolates the *spread* effect — not a sneaky
    mean shift. Returns None (caller uses the incumbent cloud) when today's
    dispersion is unusable or too few prior days carry a usable dispersion."""
    if disp_today <= DISP_EPS:
        return None
    usable = [(r, d) for r, d in prior if d > DISP_EPS]
    if len(usable) < WARMUP:
        return None
    center = statistics.mean(r for r, _ in prior)
    return [center + disp_today * ((r - center) / d) for r, d in usable]


def conditional_spread_eval(
    pairs: list[tuple[float, float]],
    *,
    warmup: int = WARMUP,
    min_scored: int = MIN_SCORED,
    z_threshold: float = Z_THRESHOLD,
    min_disp_corr: float = MIN_DISP_CORR,
) -> CalibrationEval | None:
    """Walk-forward CRPS comparison of the incumbent vs conditional predictive
    distribution over ordered (signed_residual, member_dispersion) pairs.

    Leak-free: day i is scored with pairs[:i] only. Returns None when too few
    days can be scored to say anything. The `recommend` flag is True only when the
    conditional model beats the incumbent past the noise floor AND the dispersion
    covariate genuinely tracks error."""
    inc_scores: list[float] = []
    cond_scores: list[float] = []
    diffs: list[float] = []
    hist: list[tuple[float, float]] = []

    for r, disp in pairs:
        prior = hist
        if len(prior) >= warmup:
            cloud = [pr for pr, _ in prior]
            ci = crps_sample(cloud, r)
            scaled = _conditional_cloud(prior, disp)
            cc = crps_sample(scaled, r) if scaled is not None else ci
            inc_scores.append(ci)
            cond_scores.append(cc)
            diffs.append(ci - cc)
        hist.append((r, disp))

    n = len(diffs)
    if n < min_scored:
        return None

    crps_inc = statistics.mean(inc_scores)
    crps_cond = statistics.mean(cond_scores)
    improvement = crps_inc - crps_cond
    sd = statistics.pstdev(diffs) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n else float("inf")
    z = improvement / se if se > 0 else 0.0
    disp_corr = _pearson([abs(r) for r, _ in pairs], [d for _, d in pairs])

    recommend = (
        improvement > 0
        and z >= z_threshold
        and disp_corr >= min_disp_corr
    )
    return CalibrationEval(
        n_scored=n,
        crps_incumbent=round(crps_inc, 4),
        crps_conditional=round(crps_cond, 4),
        improvement=round(improvement, 4),
        improvement_pct=round(improvement / crps_inc, 4) if crps_inc > 0 else 0.0,
        improvement_se=round(se, 4),
        z=round(z, 2),
        disp_corr=round(disp_corr, 3),
        recommend=recommend,
    )


def _self_test() -> None:
    """Oracle: on strongly heteroscedastic data (error scales with dispersion) the
    conditional model MUST win and be recommended; on homoscedastic data (constant
    error, dispersion pure noise) it must NOT be recommended. Proves the gate
    accepts real signal and rejects noise."""
    import random

    rng = random.Random(7)
    # Heteroscedastic: residual sd is proportional to the day's dispersion.
    het = []
    for _ in range(400):
        disp = rng.uniform(0.5, 4.0)
        r = rng.gauss(0.0, disp)          # spread genuinely tracks dispersion
        het.append((r, disp))
    ev = conditional_spread_eval(het)
    assert ev is not None and ev.recommend, f"should recommend on het data: {ev}"
    assert ev.improvement > 0 and ev.z >= Z_THRESHOLD, ev

    # Homoscedastic: constant error sd, dispersion is independent noise.
    rng = random.Random(11)
    hom = [(rng.gauss(0.0, 1.5), rng.uniform(0.5, 4.0)) for _ in range(400)]
    ev2 = conditional_spread_eval(hom)
    assert ev2 is not None and not ev2.recommend, f"must NOT recommend on noise: {ev2}"

    # Too little data -> no verdict at all (never act on a thin sample).
    assert conditional_spread_eval(het[:15]) is None


if __name__ == "__main__":
    _self_test()
    print("calibration self-test PASSED")
