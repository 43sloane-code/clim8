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
    'CalibrationEval', 'conditional_spread_eval',
    'CoverageEval', 'coverage_calibration_eval',
    'coverage_calibration_eval_grouped',
]

import math
import statistics
from dataclasses import dataclass

from .scoring import crps_sample, interval_coverage, quantile

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
# Coverage calibration needs a WELL-ESTIMATED band before a day's normalized
# interval score means anything: the empirical 0.10/0.90 quantiles of a tiny cloud
# under-state the spread, which would inflate every score and bias the learned
# inflation factor high. Only dress/score/learn from days whose prior cloud has at
# least this many residuals — larger than the conditional layer's WARMUP because
# tail quantiles converge slower than a CRPS mean.
BAND_FLOOR = 30


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


# --------------------------------------------------------------------------- #
# Constant-factor coverage calibration (a SECOND recommend-only layer).
#
# The conditional layer above asks "should the spread vary day-to-day?" and, on
# this council's data, declines (member dispersion doesn't track error well
# enough). A separate, simpler question remains: is the ONE served cloud the
# right *width* on average? The diagnostics say its SHAPE is right (PIT and the
# rank histogram read flat/uniform) but its 80% band can systematically under- or
# over-cover by a few points — classic ensemble under-dispersion that no shape
# test catches.
#
# The fix is a single multiplicative inflation factor c on the residual cloud,
# learned the only honest way: from the council's own REALIZED out-of-sample
# coverage. For each held-out day we record how far the outcome fell relative to
# that day's predicted band (its normalized interval score, computed from
# STRICTLY-earlier residuals); the factor that would have covered exactly `target`
# of those realized scores is the `target`-quantile of them. That is split
# conformal calibration (Vovk; Lei et al. 2018; Romano et al. 2019) run online —
# it corrects BOTH finite-sample quantile bias AND a genuine scale deficit,
# unlike an in-sample conformal bump which only sees the former.
#
# Same discipline as the conditional layer: leak-free (day i uses only scores from
# days < i), scored with the SAME proper rule (CRPS) on the SAME held-out days,
# gated past the paired-difference noise floor, and RECOMMEND-ONLY — it never
# widens the cloud the council actually serves. c is clamped to >= 1.0: this layer
# only ever proposes *widening* (fixing over-confidence), never sharpening a band
# on the strength of a few lucky days.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CoverageEval:
    """Head-to-head between the served cloud and a constant-factor widened cloud,
    with the realized coverage and the statistics behind the recommend/decline."""
    n_scored: int
    target: float                  # nominal central-interval level (0.80)
    coverage_incumbent: float      # realized held-out coverage of the served cloud
    coverage_calibrated: float     # realized held-out coverage after widening
    under_sigma: float             # (target − coverage_incumbent)/SE; >0 ⇒ under-covers
    mean_factor: float             # mean inflation c applied across scored days
    final_factor: float            # c learned from ALL scores — the live candidate
    crps_incumbent: float
    crps_calibrated: float
    improvement: float             # crps_incumbent − crps_calibrated (>0 = better)
    improvement_pct: float
    improvement_se: float          # SE of the paired per-day CRPS difference
    z: float                       # improvement / improvement_se
    recommend: bool

    def summary(self) -> str:
        verb = "RECOMMEND" if self.recommend else "no recommendation"
        return (
            f"constant coverage calibration (widen cloud ×{self.final_factor:.3f}): "
            f"coverage {self.coverage_incumbent*100:.1f}%→{self.coverage_calibrated*100:.1f}% "
            f"(nominal {self.target*100:.0f}%, {self.under_sigma:+.1f}σ under), "
            f"CRPS {self.crps_calibrated:.3f} vs {self.crps_incumbent:.3f} "
            f"({self.improvement_pct*100:+.1f}%, {self.z:+.1f}σ, n={self.n_scored}) -> {verb}"
        )


def _oos_score(center: float, lo: float, hi: float, r: float) -> float | None:
    """The normalized interval score of outcome `r` against a predictive band with
    centre `center` and quantile edges `lo`/`hi`. It is the factor by which that
    band's relevant half would have to be scaled to just touch `r`: r is inside the
    c-widened band iff this score <= c. Per-side, so an asymmetric residual cloud
    is handled exactly. None when the relevant half-width is degenerate."""
    if r >= center:
        d = hi - center
        return (r - center) / d if d > 0 else None
    d = center - lo
    return (center - r) / d if d > 0 else None


class _CovAcc:
    """Mutable per-day accumulator shared across one or more residual streams.
    Each scored day appends to these lists / counters; the finalizer reads them."""
    __slots__ = ("inc_hits", "cal_hits", "inc_crps", "cal_crps", "diffs", "factors")

    def __init__(self) -> None:
        self.inc_hits = 0
        self.cal_hits = 0
        self.inc_crps: list[float] = []
        self.cal_crps: list[float] = []
        self.diffs: list[float] = []
        self.factors: list[float] = []


def _score_stream(
    residuals: list[float],
    acc: _CovAcc,
    cal_scores: list[float],
    *,
    band_floor: int,
    warmup: int,
    target: float,
    lo_q: float,
    hi_q: float,
) -> None:
    """Walk ONE ordered residual stream — e.g. the held-out HIGH residuals, or the
    LOW residuals — scoring each day past `band_floor` against its OWN strictly-
    earlier prior cloud. That prior cloud IS the per-attribute distribution the
    council serves (compare_high is dressed with residuals_high, compare_low with
    residuals_low), so coverage/CRPS here measure the served object, not a pooled
    mixture of two differently-scaled clouds. Each day's inflation factor is the
    `target`-quantile of `cal_scores` (the realized OOS interval scores known from
    strictly-earlier days), and the normalized scores are scale-free, so a caller
    may pool one `cal_scores` list across attributes while keeping each attribute's
    prior cloud separate. Per-day contributions are appended to the shared `acc`."""
    hist: list[float] = []
    for r in residuals:
        prior = hist
        if len(prior) >= band_floor:
            center = statistics.median(prior)
            lo = quantile(prior, lo_q)
            hi = quantile(prior, hi_q)
            if hi - lo > 0:
                c = (max(1.0, quantile(cal_scores, target))
                     if len(cal_scores) >= warmup else 1.0)
                inflated = [center + c * (x - center) for x in prior]
                cov_inc, _ = interval_coverage(prior, r, lo_q, hi_q)
                cov_cal, _ = interval_coverage(inflated, r, lo_q, hi_q)
                acc.inc_hits += 1 if cov_inc else 0
                acc.cal_hits += 1 if cov_cal else 0
                ci = crps_sample(prior, r)
                cc = crps_sample(inflated, r)
                acc.inc_crps.append(ci)
                acc.cal_crps.append(cc)
                acc.diffs.append(ci - cc)
                acc.factors.append(c)
                s = _oos_score(center, lo, hi, r)
                if s is not None:
                    cal_scores.append(s)
        hist.append(r)


def _finalize_coverage(
    acc: _CovAcc,
    cal_scores: list[float],
    *,
    target: float,
    min_scored: int,
    warmup: int,
    z_threshold: float,
) -> CoverageEval | None:
    """Turn the pooled per-day contributions into the recommend/decline verdict.
    `cal_scores` here is an end-of-sample pool used only for the reported candidate
    factor — it drives no per-day decision, so pooling it across attributes leaks
    nothing."""
    n = len(acc.diffs)
    if n < min_scored:
        return None
    cov_inc = acc.inc_hits / n
    cov_cal = acc.cal_hits / n
    crps_inc = statistics.mean(acc.inc_crps)
    crps_cal = statistics.mean(acc.cal_crps)
    improvement = crps_inc - crps_cal
    sd = statistics.pstdev(acc.diffs) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n else float("inf")
    z = improvement / se if se > 0 else 0.0
    cov_se = math.sqrt(target * (1.0 - target) / n)
    under_sigma = (target - cov_inc) / cov_se if cov_se > 0 else 0.0
    final_factor = (max(1.0, quantile(cal_scores, target))
                    if len(cal_scores) >= warmup else 1.0)
    closes_gap = abs(target - cov_cal) <= abs(target - cov_inc)
    recommend = (
        improvement > 0
        and z >= z_threshold
        and closes_gap
        and final_factor > 1.0
    )
    return CoverageEval(
        n_scored=n,
        target=target,
        coverage_incumbent=round(cov_inc, 4),
        coverage_calibrated=round(cov_cal, 4),
        under_sigma=round(under_sigma, 2),
        mean_factor=round(statistics.mean(acc.factors), 4),
        final_factor=round(final_factor, 4),
        crps_incumbent=round(crps_inc, 4),
        crps_calibrated=round(crps_cal, 4),
        improvement=round(improvement, 4),
        improvement_pct=round(improvement / crps_inc, 4) if crps_inc > 0 else 0.0,
        improvement_se=round(se, 4),
        z=round(z, 2),
        recommend=recommend,
    )


def coverage_calibration_eval(
    residuals: list[float],
    *,
    band_floor: int = BAND_FLOOR,
    warmup: int = WARMUP,
    min_scored: int = MIN_SCORED,
    target: float = 0.80,
    lo_q: float = 0.10,
    hi_q: float = 0.90,
    z_threshold: float = Z_THRESHOLD,
) -> CoverageEval | None:
    """Walk-forward CRPS + coverage comparison of the served residual cloud vs the
    same cloud widened by a single online-conformal factor, over ONE ordered
    held-out residual stream.

    Leak-free: day i is dressed with residuals[:i], and its inflation factor is the
    `target`-quantile of the realized out-of-sample interval scores from days < i
    only. Days are scored only once their prior cloud reaches `band_floor`, so the
    0.10/0.90 band is well enough estimated that the learned factor isn't biased by
    tiny-sample quantile shrinkage. Returns None when too few days can be scored.
    `recommend` is True only when widening beats the incumbent on CRPS past the
    noise floor AND moves realized coverage toward nominal — never on coverage
    alone (a wider band always covers more; CRPS is what punishes over-widening).

    Single-stream entry point. The council serves a SEPARATE residual cloud per
    attribute (high, low), so the live wiring uses `coverage_calibration_eval_grouped`
    to avoid measuring a fictitious pooled cloud; this signature stays for callers
    with a genuinely homogeneous single stream and for the seeded self-test."""
    acc = _CovAcc()
    cal_scores: list[float] = []
    _score_stream(residuals, acc, cal_scores, band_floor=band_floor,
                  warmup=warmup, target=target, lo_q=lo_q, hi_q=hi_q)
    return _finalize_coverage(acc, cal_scores, target=target,
                              min_scored=min_scored, warmup=warmup,
                              z_threshold=z_threshold)


def coverage_calibration_eval_grouped(
    streams: list[list[float]],
    *,
    band_floor: int = BAND_FLOOR,
    warmup: int = WARMUP,
    min_scored: int = MIN_SCORED,
    target: float = 0.80,
    lo_q: float = 0.10,
    hi_q: float = 0.90,
    z_threshold: float = Z_THRESHOLD,
) -> CoverageEval | None:
    """Per-attribute coverage calibration: the SAME walk-forward as the single-stream
    version, but each stream (e.g. held-out HIGH residuals, then LOW residuals) is
    scored against its OWN prior cloud — exactly the cloud the council serves for that
    attribute — and the per-day outcomes are pooled into one verdict.

    Why this is the right object. The council never serves one cloud over both high
    and low: compare_high resamples residuals_high, compare_low resamples
    residuals_low. High and low residuals carry different bias-correction offsets and
    different spreads, so concatenating them into a single empirical band measures a
    mixture distribution the council never emits — which can fabricate apparent under-
    coverage (the body of one sub-cloud lands in the tails of the pooled band) or mask
    a real deficit by cancellation. Scoring each attribute against its own cloud and
    pooling only the scale-free per-day scores keeps the measurement faithful and
    matches how Validation.coverage_80 / CRPS / PIT are already computed.

    Leak-free per stream: each attribute learns its inflation factor from its OWN
    strictly-earlier realized scores. The pooled `cal_scores` feeds only the reported
    end-of-sample candidate factor, which drives no per-day decision."""
    acc = _CovAcc()
    pooled_scores: list[float] = []
    for stream in streams:
        stream_scores: list[float] = []
        _score_stream(stream, acc, stream_scores, band_floor=band_floor,
                      warmup=warmup, target=target, lo_q=lo_q, hi_q=hi_q)
        pooled_scores.extend(stream_scores)
    return _finalize_coverage(acc, pooled_scores, target=target,
                              min_scored=min_scored, warmup=warmup,
                              z_threshold=z_threshold)


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

    # --- coverage_calibration_eval oracles ------------------------------- #
    # Under-dispersed: residual variance trends UP, so a cloud built from older
    # (narrower) days systematically under-covers the current day. Widening MUST
    # help and be recommended, and it must drag realized coverage up toward 0.80.
    rng = random.Random(3)
    under = [rng.gauss(0.0, 1.0 + 0.006 * i) for i in range(700)]
    cev = coverage_calibration_eval(under)
    assert cev is not None and cev.recommend, f"should recommend on under-dispersed: {cev}"
    assert cev.final_factor > 1.0 and cev.improvement > 0, cev
    assert cev.coverage_calibrated > cev.coverage_incumbent, cev

    # Calibrated: stationary Gaussian. The served cloud already covers ~80% out of
    # sample, so the learned factor sits ~1.0 and widening earns no CRPS edge ->
    # must DECLINE (rejecting a recalibration the data doesn't support).
    rng = random.Random(5)
    cal = [rng.gauss(0.0, 1.5) for _ in range(700)]
    cev2 = coverage_calibration_eval(cal)
    assert cev2 is not None and not cev2.recommend, f"must NOT recommend on calibrated: {cev2}"
    assert 0.76 <= cev2.coverage_incumbent <= 0.84, cev2

    # Thin sample -> no verdict at all.
    assert coverage_calibration_eval(under[:15]) is None

    # --- coverage_calibration_eval_grouped: faithful per-attribute scoring ----- #
    # Two INDIVIDUALLY-calibrated clouds of different centre AND spread — exactly the
    # high/low residual situation. The grouped call scores each stream against its
    # OWN cloud, so its incumbent coverage must EXACTLY equal the n-weighted pool of
    # the two single-stream coverages. Pooling the raw residuals instead measures a
    # mixture distribution the council never serves and reads a materially different
    # number (here the wide mixed band over-covers each sub-cloud, coincidentally
    # near nominal — masking the per-attribute truth). That divergence is the whole
    # reason the grouped entry point exists.
    rng = random.Random(404)
    hi_stream = [rng.gauss(+0.8, 1.1) for _ in range(500)]
    lo_stream = [rng.gauss(-0.6, 0.6) for _ in range(500)]
    g = coverage_calibration_eval_grouped([hi_stream, lo_stream])
    e_hi = coverage_calibration_eval(hi_stream)
    e_lo = coverage_calibration_eval(lo_stream)
    assert g is not None and e_hi is not None and e_lo is not None
    assert not g.recommend, f"calibrated per-attribute must decline: {g}"
    n_hi, n_lo = e_hi.n_scored, e_lo.n_scored
    assert g.n_scored == n_hi + n_lo, (g.n_scored, n_hi, n_lo)
    expected_cov = (e_hi.coverage_incumbent * n_hi
                    + e_lo.coverage_incumbent * n_lo) / (n_hi + n_lo)
    assert abs(g.coverage_incumbent - expected_cov) < 1.5e-3, (g.coverage_incumbent, expected_cov)
    pooled = [x for pair in zip(hi_stream, lo_stream) for x in pair]
    p = coverage_calibration_eval(pooled)
    assert p is not None
    assert abs(p.coverage_incumbent - g.coverage_incumbent) > 5e-3, (p, g)

    # A genuine per-attribute deficit must still surface: if BOTH clouds are built
    # narrow-from-older-days (variance trending up), grouped recommends widening.
    rng = random.Random(405)
    up_hi = [rng.gauss(0.0, 1.0 + 0.006 * i) for i in range(700)]
    up_lo = [rng.gauss(0.0, 0.8 + 0.005 * i) for i in range(700)]
    gu = coverage_calibration_eval_grouped([up_hi, up_lo])
    assert gu is not None and gu.recommend, f"grouped must catch a real deficit: {gu}"
    assert gu.final_factor > 1.0 and gu.improvement > 0, gu

    # Thin streams -> no verdict.
    assert coverage_calibration_eval_grouped([under[:8], under[8:16]]) is None


if __name__ == "__main__":
    _self_test()
    print("calibration self-test PASSED")
