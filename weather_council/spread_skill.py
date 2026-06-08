"""Spread–skill consistency — a recommend-only ensemble-verification diagnostic.

Why this module exists
----------------------
Operational ensemble systems (ECMWF's EPS; the neural ensembles this project
takes inspiration from — NVIDIA's bred-vector / multi-checkpoint SFNO "Huge
Ensembles", and GenCast / WeatherNext-style diffusion ensembles) all live or die
by ONE property: is the ensemble's *spread* an honest, flow-dependent estimate of
its own error? They engineer that spread deliberately — perturbing initial
conditions along fast-growing (bred) directions and perturbing the model across
checkpoints / noise seeds — so that on hard days the spread is wide and on easy
days it is narrow. The standard verification of that property is the **spread–skill
relationship** (Leutbecher & Palmer 2008; Fortin et al. 2014): across many cases,
predicted spread should rise with, and be proportional to, the RMSE of the mean.

The council already measures CRPS, 80% coverage, sharpness, and the
dispersion↔|error| *correlation* (calibration.py). What it did not have is the
spread–skill **reliability diagram**: binned, the standard human-readable picture
of whether member disagreement is a trustworthy per-day uncertainty signal — the
very signal calibration.py's flow-dependent cloud is built to exploit.

What the council's "ensemble" is, and the scale subtlety
--------------------------------------------------------
The council owns no forward model, so it cannot breed IC vectors or inject latent
noise the way SFNO/GenCast do. Its analog of "perturb the model" is structural and
already present: a panel of independent top-band NWP systems. Its per-day **member
dispersion** (spread of the bias-corrected member forecasts) is the data-derived
analog of raw ensemble spread.

But the council serves an *averaged* blend, whose error is ~√(effective members)
SMALLER than the spread of the individual members. So the raw magnitude of member
dispersion is NOT directly the blend's predictive σ — comparing them naively would
brand the council "over-dispersed" purely as an averaging artifact. This module
therefore removes that single global scale (α, fit by variance matching) and asks
the scale-invariant question that actually matters for flow-dependent calibration:
**after the global scale, does member dispersion track the blend's error with the
right shape across regimes?** 1/α is reported as the effective averaging factor.

What it measures (and what it does NOT do)
------------------------------------------
Over the leak-free walk-forward (signed_residual r, member_dispersion d) pairs
Council._validate already produces — the SAME pairs calibration.py scores — it
bins days by predicted spread and per bin compares α·spread to realized RMSE:

  * spread–skill CONSISTENCY: does realized error rise with predicted spread
    across bins (the flow-dependence the diagrams exist to create)?
  * RELATIVE RELIABILITY: after removing the global scale α, does α·spread match
    RMSE *in every bin* (right shape), or only on average (e.g. dispersion
    saturates on the most volatile days)?
  * a verdict: RELIABLE flow-dependent spread / tracks-but-mis-scaled / FLAT.

Pure diagnostic. Changes NO verdict number — like the daily health check and
calibration.py, it emits a finding for human review, and (when the spread is a
reliable signal) corroborates calibration.py's flow-dependent recommendation.

Stdlib only (math, statistics).
"""

from __future__ import annotations

__all__ = [
    'SpreadSkillBin', 'SpreadSkill', 'spread_skill_eval'
]

import math
import statistics
from dataclasses import dataclass

# Equal-count bins across the predicted-spread range. Five is the usual choice
# for a spread–skill diagram: enough to see the curve, few enough that each bin
# keeps a usable error sample.
DEFAULT_BINS = 5
# Minimum days per bin before a bin's RMSE is trustworthy, and hence the minimum
# total sample (DEFAULT_BINS * this). Below it, return None — never judge spread
# calibration on a handful of days.
MIN_PER_BIN = 8
# After removing the global scale α, per-bin α·spread may differ from per-bin RMSE
# by at most this (mean relative gap) for the spread to be called RELIABLE.
RELIABILITY_BAND = 0.20
# Flow-dependence is judged by the rank correlation between dispersion and |error|
# over ALL pairs (robust at large n), NOT a 5-point bin correlation (whose null SD
# is ~0.5 — pure noise routinely fakes |r|~0.7). The spread must clear a minimum
# effect size AND be statistically positive (≈2σ, ρ·√(n−1) ≥ Z) to count.
MIN_CONSISTENCY = 0.1
CONSISTENCY_Z = 2.0


@dataclass(frozen=True)
class SpreadSkillBin:
    """One predicted-spread bin of the spread–skill diagram."""
    n: int
    mean_spread: float        # mean predicted spread (member dispersion) in bin
    rmse: float               # realized RMSE of the residual in bin
    scaled_ratio: float       # alpha * mean_spread / rmse  (1 == right shape here)


@dataclass(frozen=True)
class SpreadSkill:
    """Spread–skill consistency of the council's member-dispersion signal.
    Recommend-only: a diagnostic of the predicted spread, never a forecast value."""
    n: int
    rmse: float               # global RMSE of the held-out residual (°C)
    mean_spread: float        # global mean predicted spread (°C)
    alpha: float              # variance-matching global scale: sqrt(<r^2>/<d^2>)
    avg_members_factor: float # 1/alpha — how far raw dispersion overstates blend error
    consistency: float        # Spearman(dispersion, |residual|) over all pairs
    reliability_gap: float    # mean |alpha*spread - rmse| / rmse over bins (0 = ideal)
    tracks_error: bool        # does spread rise with error across bins?
    reliable: bool            # tracks AND right shape after de-scaling
    label: str
    bins: tuple[SpreadSkillBin, ...]

    def summary(self) -> str:
        return (
            f"spread–skill (member dispersion as a per-day uncertainty signal): "
            f"{self.label} — consistency r={self.consistency:+.2f}, "
            f"relative-reliability gap {self.reliability_gap * 100:.0f}% "
            f"(global scale 1/α≈{self.avg_members_factor:.1f}× ⇒ raw spread overstates "
            f"blend error by the averaging factor); RMSE {self.rmse:.2f} °C, n={self.n}. "
            f"Recommend-only — does not move the verdict."
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


def _ranks(xs: list[float]) -> list[float]:
    """Average (fractional) ranks, so ties share their mean rank."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0           # 1-based average rank for the tie group
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> float:
    """Spearman rank correlation: Pearson on ranks. Rank-based so it is robust to
    the heavy |z| noise in |residual| and to outliers, and measures monotonic
    dispersion↔|error| dependence rather than a strictly linear one."""
    if len(xs) < 2:
        return 0.0
    return _pearson(_ranks(xs), _ranks(ys))


def _rmse(rs: list[float]) -> float:
    return math.sqrt(sum(r * r for r in rs) / len(rs)) if rs else 0.0


def spread_skill_eval(
    pairs: list[tuple[float, float]],
    *,
    bins: int = DEFAULT_BINS,
    min_per_bin: int = MIN_PER_BIN,
    reliability_band: float = RELIABILITY_BAND,
    min_consistency: float = MIN_CONSISTENCY,
) -> SpreadSkill | None:
    """Spread–skill diagnostic over (signed_residual, member_dispersion) pairs.

    The pairs are the leak-free walk-forward output of Council._validate — each
    residual is a genuine held-out error and each dispersion is that day's member
    spread, so measuring their relationship is honest. Unlike calibration.py this
    needs no train/test split: it is a property measurement (like coverage), not a
    per-day predictive model. The single global scale is removed first so the
    averaging factor (blend error ≈ member dispersion / √m_eff) is not mistaken
    for a calibration defect. Returns None when too few days exist to fill `bins`
    bins of at least `min_per_bin` each, or the spread carries no variance."""
    usable = [(r, d) for r, d in pairs if d is not None and d > 0]
    n = len(usable)
    if bins < 2 or n < bins * min_per_bin:
        return None

    sum_r2 = sum(r * r for r, _ in usable)
    sum_d2 = sum(d * d for _, d in usable)
    if sum_d2 <= 0:
        return None
    alpha = math.sqrt(sum_r2 / sum_d2)        # variance-matching global scale
    if alpha <= 0:
        return None

    # Order by predicted spread and cut into (near) equal-count bins.
    usable.sort(key=lambda rd: rd[1])
    edges = [round(k * n / bins) for k in range(bins + 1)]
    out_bins: list[SpreadSkillBin] = []
    rel_gaps: list[float] = []
    for k in range(bins):
        chunk = usable[edges[k]:edges[k + 1]]
        if len(chunk) < min_per_bin:
            return None
        ms = statistics.mean(d for _, d in chunk)
        rm = _rmse([r for r, _ in chunk])
        scaled = alpha * ms
        out_bins.append(SpreadSkillBin(
            n=len(chunk),
            mean_spread=round(ms, 4),
            rmse=round(rm, 4),
            scaled_ratio=round(scaled / rm, 4) if rm > 0 else float("inf"),
        ))
        if rm > 0:
            rel_gaps.append(abs(scaled - rm) / rm)

    # Flow-dependence over ALL pairs (robust), not the 5-point bin curve.
    consistency = _spearman([d for _, d in usable], [abs(r) for r, _ in usable])
    reliability_gap = statistics.mean(rel_gaps) if rel_gaps else float("inf")
    rmse_all = _rmse([r for r, _ in usable])
    mean_spread_all = statistics.mean(d for _, d in usable)

    tracks = (consistency >= min_consistency
              and consistency * math.sqrt(n - 1) >= CONSISTENCY_Z)
    right_shape = reliability_gap <= reliability_band
    reliable = tracks and right_shape
    if reliable:
        label = "RELIABLE flow-dependent spread"
    elif tracks:
        label = "tracks error but mis-scaled across regimes (shape drift)"
    else:
        label = "FLAT spread–skill — member dispersion does not track error"

    return SpreadSkill(
        n=n,
        rmse=round(rmse_all, 4),
        mean_spread=round(mean_spread_all, 4),
        alpha=round(alpha, 4),
        avg_members_factor=round(1.0 / alpha, 3) if alpha > 0 else float("inf"),
        consistency=round(consistency, 3),
        reliability_gap=round(reliability_gap, 4),
        tracks_error=tracks,
        reliable=reliable,
        label=label,
        bins=tuple(out_bins),
    )


def _self_test() -> None:
    """Reproducible oracle. Synthetic ensembles whose true spread–skill behaviour
    is known by construction; the diagnostic must read each one back:

      1. calibrated, flow-dependent     r ~ N(0, d)        -> RELIABLE, tracks
      2. council-like averaging          r ~ N(0, 0.45 d)   -> RELIABLE (scale
         removed: averaging is NOT a defect), 1/α ≈ 2.2
      3. spread is pure noise            r ~ N(0, c), d ⟂   -> FLAT (no tracking)
      4. saturating spread               r ~ N(0, min(d,2)) -> tracks but mis-scaled

    Cases 1 and 2 differ only by a global averaging scale yet BOTH read RELIABLE —
    proving the scale-invariance that stops the diagnostic from branding an
    averaging council "over-dispersed". Case 4 tracks (positive consistency) yet is
    flagged mis-scaled — proving shape reliability is distinct from correlation.
    """
    import random

    def make(fn, seed, nn=800):
        rng = random.Random(seed)
        out = []
        for _ in range(nn):
            d = rng.uniform(0.5, 4.0)
            out.append((rng.gauss(0.0, max(fn(d, rng), 1e-6)), d))
        return out

    cal = spread_skill_eval(make(lambda d, _: d, 1))
    assert cal is not None and cal.reliable, cal
    assert cal.tracks_error and 0.85 <= cal.alpha <= 1.15, cal

    avg = spread_skill_eval(make(lambda d, _: 0.45 * d, 2))
    assert avg is not None and avg.reliable, avg               # averaging != defect
    assert 2.0 <= avg.avg_members_factor <= 2.5, avg          # 1/α recovers ~1/0.45

    noise = spread_skill_eval(make(lambda d, _: 1.5, 3))      # const σ, d irrelevant
    assert noise is not None and not noise.tracks_error, noise
    assert noise.label.startswith("FLAT"), noise

    sat = spread_skill_eval(make(lambda d, _: min(d, 2.0), 4))
    assert sat is not None and sat.tracks_error and not sat.reliable, sat

    # Too little data -> no verdict (never judge spread on a handful of days).
    assert spread_skill_eval(make(lambda d, _: d, 5)[:20]) is None

    print(f"spread_skill self-test PASSED "
          f"(calibrated α≈{cal.alpha:.2f} RELIABLE; averaging 1/α≈{avg.avg_members_factor:.1f}× "
          f"RELIABLE; noise FLAT r={noise.consistency:+.2f}; "
          f"saturating gap {sat.reliability_gap * 100:.0f}% mis-scaled)")


if __name__ == "__main__":
    _self_test()
