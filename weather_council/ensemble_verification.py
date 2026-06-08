"""Ensemble calibration verification — rank histogram + PIT, recommend-only.

Why this module exists
----------------------
`spread_skill.py` asks whether member dispersion *tracks* the blend's error
(the right SHAPE across regimes). `calibration.py` asks whether *scaling* the
spread by that dispersion pays off on held-out CRPS. Both are about the spread's
behaviour as a covariate. Neither answers the two questions the operational
ensemble-verification literature treats as the bedrock companion checks — the
ones plotted in every EPS / SFNO-ensemble / GenCast paper alongside the
spread–skill diagram:

  1. Is the panel's raw dispersion the right *size*?  (the **rank histogram** /
     Talagrand diagram; Anderson 1996, Hamill 2001.) If the eight members are
     systematically too tight, the verifying observation falls *outside* their
     range far more than 2/(m+1) of the time, and the rank histogram is
     U-shaped — under-dispersion, the classic deterministic-panel failure. If
     they are too wide, it is dome-shaped. A tilt is a panel bias.

  2. Is the *served* predictive distribution calibrated?  (the **PIT
     histogram**; Dawid 1984, Gneiting et al. 2007.) The council does NOT sell
     the raw member spread — it sells the empirical held-out residual cloud that
     compare.py resamples into market-bucket probabilities. The Probability
     Integral Transform of each held-out outcome through its *strictly-earlier*
     cloud should be Uniform(0,1) if that distribution is honest. Clustering at
     the edges means the bucket probabilities are over-confident; a tilt means
     they are biased.

Together with spread_skill these close the loop: (1) typically shows the raw
panel is under-dispersed — which is *precisely why* the council serves the wider
residual cloud rather than the member spread — and (2) verifies that the cloud
it serves instead is calibrated. That is the synergistic, self-consistent story,
measured rather than asserted.

The shared mathematics
----------------------
A rank histogram (after normalising ranks to [0,1] to absorb a varying member
count) and a PIT histogram are the SAME object: a sample on [0,1] that should be
uniform. So both flow through ONE tested core, `uniformity_eval`, which bins the
sample, runs a chi-square test of uniformity (via its large-sample normal
approximation — no scipy), and — crucially — DECOMPOSES the deviation from
uniform onto two orthogonal shapes:

  * a CONVEX component (U vs dome) -> dispersion error (too tight / too wide);
  * a LINEAR component (tilt)      -> a bias.

Reporting which orthogonal component carries the deviation turns "not uniform"
into an actionable diagnosis. The wrappers attach the domain meaning (panel
dispersion for ranks; served-distribution calibration for PIT).

Pure diagnostic. Changes NO verdict number — like spread_skill / calibration /
the daily health check, it emits a finding for human review. Stdlib only.
"""

from __future__ import annotations

__all__ = [
    'UniformityDiagnostic', 'RankHistogram', 'PITCalibration',
    'uniformity_eval', 'rank_histogram_eval', 'pit_calibration_eval',
]

import math
import random
import statistics
from dataclasses import dataclass

# Ten bins is the usual rank/PIT-histogram resolution: fine enough to see a U or
# a dome, coarse enough that each bin keeps a usable count.
DEFAULT_BINS = 10
# Minimum EXPECTED count per bin before the chi-square approximation is trusted,
# hence the minimum sample (bins * this). Below it: no verdict — never diagnose
# calibration on a handful of days.
MIN_PER_BIN = 5
# chi-square uniformity is rejected when its normal-approximation z-score,
# z = (chi2 - dof) / sqrt(2*dof), clears this (~2 sigma). Matches the project's
# 2-sigma gating style (spread_skill's consistency gate, calibration's z-test).
REJECT_Z = 2.0


@dataclass(frozen=True)
class UniformityDiagnostic:
    """Geometry of a [0,1] sample vs the uniform it should be. Domain-agnostic:
    fed by both the rank histogram and the PIT histogram."""
    n: int
    bins: tuple[int, ...]        # observed counts per bin
    expected: float              # n / nbins  (flat expectation)
    chi2: float
    dof: int
    reduced_chi2: float          # chi2 / dof  (1.0 == perfectly flat in expectation)
    z: float                     # (chi2 - dof)/sqrt(2 dof): normal-approx significance
    uniform: bool                # cannot reject uniform at REJECT_Z
    edge_ratio: float            # (first+last bin)/(2*expected); >1 U-shape, <1 dome
    convex_coef: float           # signed convex (U +/dome -) amplitude, in expected units
    tilt_coef: float             # signed linear (tilt) amplitude, in expected units
    chi2_convex: float           # chi2 carried by the convex (dispersion) component
    chi2_tilt: float             # chi2 carried by the linear (bias) component
    shape: str                   # 'flat' | 'u' | 'dome' | 'tilt-up' | 'tilt-down'


def _decompose(counts: list[int], expected: float) -> tuple[float, float, float, float]:
    """Project the per-bin deviation from flat onto two ORTHOGONAL shapes and
    return (convex_coef, tilt_coef, chi2_convex, chi2_tilt).

    Basis (over centred bin index x_i = i - (B-1)/2):
      * LINEAR  L_i = x_i                      -> a tilt (bias)
      * CONVEX  Q_i = x_i^2 - mean(x_i^2)      -> U (positive) vs dome (negative),
                                                  made orthogonal to the constant
    L and Q are mutually orthogonal by symmetry, so each component's contribution
    to chi2 = Σ dev^2/expected is independent: (dev·b)^2 / (b·b) / expected."""
    b = len(counts)
    dev = [c - expected for c in counts]
    xs = [i - (b - 1) / 2.0 for i in range(b)]
    q_raw = [x * x for x in xs]
    qbar = sum(q_raw) / b
    qs = [q - qbar for q in q_raw]

    ll = sum(x * x for x in xs)
    qq = sum(q * q for q in qs)
    dl = sum(d * x for d, x in zip(dev, xs))
    dq = sum(d * q for d, q in zip(dev, qs))

    tilt = dl / ll if ll > 0 else 0.0
    convex = dq / qq if qq > 0 else 0.0
    chi2_tilt = (dl * dl / ll) / expected if ll > 0 and expected > 0 else 0.0
    chi2_convex = (dq * dq / qq) / expected if qq > 0 and expected > 0 else 0.0
    return convex, tilt, chi2_convex, chi2_tilt


def uniformity_eval(
    values: list[float],
    *,
    bins: int = DEFAULT_BINS,
    min_per_bin: int = MIN_PER_BIN,
) -> UniformityDiagnostic | None:
    """Test whether a [0,1] sample is uniform, and if not, name the shape.

    Returns None when there are too few values to fill `bins` bins at the
    minimum expected count, or `bins` < 2. Values outside [0,1] are dropped
    (a defensive guard; ranks and PIT are constructed in-range)."""
    vals = [v for v in values if v is not None and 0.0 <= v <= 1.0]
    n = len(vals)
    if bins < 2 or n < bins * min_per_bin:
        return None

    counts = [0] * bins
    for v in vals:
        k = int(v * bins)
        if k >= bins:                 # v == 1.0 lands in the last bin
            k = bins - 1
        counts[k] += 1

    expected = n / bins
    chi2 = sum((c - expected) ** 2 / expected for c in counts)
    dof = bins - 1
    reduced = chi2 / dof
    z = (chi2 - dof) / math.sqrt(2 * dof)
    uniform = z < REJECT_Z

    edge_ratio = (counts[0] + counts[-1]) / (2 * expected) if expected > 0 else float("inf")
    convex, tilt, chi2_convex, chi2_tilt = _decompose(counts, expected)

    if uniform:
        shape = "flat"
    elif chi2_convex >= chi2_tilt:
        shape = "u" if convex > 0 else "dome"
    else:
        shape = "tilt-up" if tilt > 0 else "tilt-down"

    return UniformityDiagnostic(
        n=n,
        bins=tuple(counts),
        expected=round(expected, 3),
        chi2=round(chi2, 3),
        dof=dof,
        reduced_chi2=round(reduced, 3),
        z=round(z, 2),
        uniform=uniform,
        edge_ratio=round(edge_ratio, 3),
        convex_coef=round(convex, 3),
        tilt_coef=round(tilt, 3),
        chi2_convex=round(chi2_convex, 3),
        chi2_tilt=round(chi2_tilt, 3),
        shape=shape,
    )


# Shape -> (verdict word, plain-language meaning) for the raw member panel.
_RANK_MEANING = {
    "flat": ("CALIBRATED", "the verifying obs is exchangeable with the members — "
                           "the panel's spread is the right size"),
    "u": ("UNDER-DISPERSED", "the obs lands outside the member range too often — the "
                             "raw panel is too tight (over-confident); this is why the "
                             "council serves the wider held-out residual cloud, not the "
                             "member spread"),
    "dome": ("OVER-DISPERSED", "the obs sits mid-pack too often — the raw panel is too "
                               "wide for the realized error"),
    "tilt-up": ("BIASED COLD", "the obs runs above the members — residual bias the "
                               "per-member correction has not fully removed"),
    "tilt-down": ("BIASED WARM", "the obs runs below the members — residual warm bias"),
}

# Shape -> (verdict word, meaning) for the SERVED predictive distribution.
_PIT_MEANING = {
    "flat": ("CALIBRATED", "held-out outcomes fall uniformly through the served cloud — "
                           "the bucket probabilities' spread is honest"),
    "u": ("OVER-CONFIDENT", "outcomes hit the cloud's tails too often — the served "
                            "distribution is too narrow; bucket probabilities overstate "
                            "confidence"),
    "dome": ("UNDER-CONFIDENT", "outcomes cluster in the cloud's centre — the served "
                                "distribution is wider than it needs to be"),
    "tilt-up": ("BIASED COLD", "outcomes sit in the upper tail too often — the served "
                               "distribution centres too cold"),
    "tilt-down": ("BIASED WARM", "outcomes sit in the lower tail too often — the served "
                                 "distribution centres too warm"),
}


@dataclass(frozen=True)
class RankHistogram:
    """Talagrand rank histogram of the raw member panel over the walk-forward.
    Recommend-only: a diagnostic of the panel's dispersion, never a verdict."""
    diag: UniformityDiagnostic
    verdict: str                 # CALIBRATED / UNDER-DISPERSED / OVER-DISPERSED / BIASED *
    meaning: str

    @property
    def n(self) -> int:
        return self.diag.n

    def summary(self) -> str:
        d = self.diag
        return (
            f"rank histogram (is the 8-member panel's spread the right size?): "
            f"{self.verdict} — {d.shape} histogram, reduced chi^2={d.reduced_chi2} "
            f"(z={d.z:+.1f}), edge ratio {d.edge_ratio:.2f}, n={d.n}. {self.meaning}. "
            f"Recommend-only."
        )


@dataclass(frozen=True)
class PITCalibration:
    """PIT histogram of the SERVED predictive distribution (the held-out residual
    cloud compare.py resamples) over the leak-free walk-forward. Recommend-only."""
    diag: UniformityDiagnostic
    verdict: str                 # CALIBRATED / OVER-CONFIDENT / UNDER-CONFIDENT / BIASED *
    meaning: str

    @property
    def n(self) -> int:
        return self.diag.n

    def summary(self) -> str:
        d = self.diag
        return (
            f"PIT calibration (is the served bucket-probability distribution honest?): "
            f"{self.verdict} — {d.shape} histogram, reduced chi^2={d.reduced_chi2} "
            f"(z={d.z:+.1f}), edge ratio {d.edge_ratio:.2f}, n={d.n}. {self.meaning}. "
            f"Recommend-only."
        )


def _normalised_rank(members: list[float], obs: float, rng: random.Random) -> float:
    """Randomized rank of `obs` among `members`, on (0,1).

    rank = #(members strictly below obs) in {0..m}. A calibrated ensemble has
    only m+1 *discrete* ranks, so binning them into a fixed bin count manufactures
    a spurious U/dome from the lumpy m+1-into-B mapping. The standard fix for a
    panel of varying size m (Hamill 2001) is the RANDOMIZED rank
    (rank + V)/(m+1), V~Uniform(0,1): it spreads each discrete rank continuously
    over its (0,1) band, so the histogram is genuinely uniform under calibration
    for ANY m — while real under-dispersion (mass at the edges), over-dispersion
    (mass at the centre) and bias (a tilt) all survive, since V only adds
    within-band uniform jitter. Seeded => reproducible."""
    m = len(members)
    rank = sum(1 for x in members if x < obs)
    return (rank + rng.random()) / (m + 1)


def rank_histogram_eval(
    ensembles: list[tuple[list[float], float]],
    *,
    bins: int = DEFAULT_BINS,
    min_per_bin: int = MIN_PER_BIN,
    min_members: int = 2,
    seed: int = 0,
) -> RankHistogram | None:
    """Rank histogram over (member_forecasts, observed) pairs from the leak-free
    walk-forward. Each member set is bias-corrected from STRICTLY-earlier days,
    so ranking today's obs against them is honest. The randomized rank is seeded
    so the result is reproducible. Returns None when too few usable days exist."""
    rng = random.Random(seed)
    us = [
        _normalised_rank(members, obs, rng)
        for members, obs in ensembles
        if members is not None and len(members) >= min_members
    ]
    diag = uniformity_eval(us, bins=bins, min_per_bin=min_per_bin)
    if diag is None:
        return None
    verdict, meaning = _RANK_MEANING[diag.shape]
    return RankHistogram(diag=diag, verdict=verdict, meaning=meaning)


def pit_calibration_eval(
    pits: list[float],
    *,
    bins: int = DEFAULT_BINS,
    min_per_bin: int = MIN_PER_BIN,
) -> PITCalibration | None:
    """PIT histogram over leak-free PIT values — each the empirical CDF of the
    served residual cloud (strictly-earlier residuals) evaluated at the held-out
    outcome, exactly the distribution compare.py resamples. Returns None when too
    few values exist."""
    diag = uniformity_eval(pits, bins=bins, min_per_bin=min_per_bin)
    if diag is None:
        return None
    verdict, meaning = _PIT_MEANING[diag.shape]
    return PITCalibration(diag=diag, verdict=verdict, meaning=meaning)


def _self_test() -> None:
    """Reproducible oracle. Synthetic panels whose dispersion is known by
    construction; the rank histogram must read each one back:

      calibrated   members ~ N(0,1), obs ~ N(0,1)      -> flat / CALIBRATED
      too tight    members ~ N(0,0.45), obs ~ N(0,1)   -> U / UNDER-DISPERSED
      too wide     members ~ N(0,2.2), obs ~ N(0,1)    -> dome / OVER-DISPERSED
      cold-biased  members ~ N(0,1), obs ~ N(+1.2,1)   -> tilt-up / BIASED COLD

    Plus the PIT wrapper on direct [0,1] samples, and the too-few-data guard.
    """
    import random

    def panel(member_sd, obs_mu, obs_sd, seed, m=8, days=1200):
        rng = random.Random(seed)
        out = []
        for _ in range(days):
            members = [rng.gauss(0.0, member_sd) for _ in range(m)]
            out.append((members, rng.gauss(obs_mu, obs_sd)))
        return out

    cal = rank_histogram_eval(panel(1.0, 0.0, 1.0, 1))
    assert cal is not None and cal.verdict == "CALIBRATED", cal.summary()
    assert cal.diag.shape == "flat", cal.summary()

    tight = rank_histogram_eval(panel(0.45, 0.0, 1.0, 2))
    assert tight is not None and tight.verdict == "UNDER-DISPERSED", tight.summary()
    assert tight.diag.shape == "u" and tight.diag.edge_ratio > 1.0, tight.summary()

    wide = rank_histogram_eval(panel(2.2, 0.0, 1.0, 3))
    assert wide is not None and wide.verdict == "OVER-DISPERSED", wide.summary()
    assert wide.diag.shape == "dome" and wide.diag.edge_ratio < 1.0, wide.summary()

    cold = rank_histogram_eval(panel(1.0, 1.2, 1.0, 4))
    assert cold is not None and cold.verdict == "BIASED COLD", cold.summary()
    assert cold.diag.shape == "tilt-up", cold.summary()

    # PIT wrapper: a uniform sample is CALIBRATED; an edge-piled sample is
    # OVER-CONFIDENT (served distribution too narrow).
    rng = random.Random(5)
    flat_pits = [rng.random() for _ in range(1500)]
    pcal = pit_calibration_eval(flat_pits)
    assert pcal is not None and pcal.verdict == "CALIBRATED", pcal.summary()

    # U-shaped PIT: push mass to the tails (square-root pulls toward 0, its
    # reflection toward 1) -> OVER-CONFIDENT.
    rng = random.Random(6)
    u_pits = []
    for _ in range(1500):
        u = rng.random()
        u_pits.append(u * u if rng.random() < 0.5 else 1.0 - u * u)
    pu = pit_calibration_eval(u_pits)
    assert pu is not None and pu.verdict == "OVER-CONFIDENT", pu.summary()
    assert pu.diag.shape == "u", pu.summary()

    # Too little data -> no verdict.
    assert rank_histogram_eval(panel(1.0, 0.0, 1.0, 7, days=20)) is None
    assert pit_calibration_eval([rng.random() for _ in range(20)]) is None

    print(
        "ensemble_verification self-test PASSED "
        f"(calibrated flat z={cal.diag.z:+.1f}; tight U edge={tight.diag.edge_ratio:.2f}; "
        f"wide dome edge={wide.diag.edge_ratio:.2f}; cold tilt={cold.diag.tilt_coef:+.2f}; "
        f"PIT uniform CALIBRATED, U OVER-CONFIDENT)"
    )


if __name__ == "__main__":
    _self_test()
