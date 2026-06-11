"""Candidate 43 — healthcheck v2: a CALIBRATION gate, not a compile gate.

The v1 health check answered "do the scripts run?" A pipeline can compile and
still be miscalibrated — selling bucket probabilities that are systematically
over- or under-confident. PIT flatness on rolling held-out data is the standard
precondition for trusting a probabilistic forecast (Dawid 1984; Gneiting et al.
2007), and it was computed (`daily_healthcheck._walk_forward` already returns the
leak-free `pits`) but never GATED on.

This module turns those PIT values into a three-tier verdict per station:

  * GREEN  — compiles, AND the rolling held-out PIT passes a flatness test, AND
             the verification log has no gap > `MAX_GAP_DAYS`.
  * AMBER  — compiles, but PIT fails on a SMALL sample (< `SMALL_N` obs). Report,
             do not block: small-sample PIT is noisy, and the fix at that size is
             a parametric recalibration (beta/Platt), never isotonic.
  * RED    — PIT fails on ≥ `SMALL_N` obs, OR a log gap, OR a compile failure.
             RED is a hard block: the prediction layer must emit
             "REFUSED: calibration" instead of bucket probabilities.

Flatness test: Pearson chi-square of the PIT histogram against the uniform
expectation, with a no-scipy regularised-incomplete-gamma p-value. Bröcker & Smith
(2007) consistency bars (the per-bin range a calibrated forecast's histogram should
stay within) are provided for the saved PIT plot via the exact Binomial quantiles.

Stdlib only (math). No numpy/scipy.
"""

from __future__ import annotations

__all__ = [
    "MAX_GAP_DAYS", "SMALL_N", "DEFAULT_BINS", "ALPHA",
    "chisq_sf", "pit_histogram", "pit_flatness_test",
    "consistency_bars", "log_gaps", "calibration_tier",
]

import datetime as dt
import math

MAX_GAP_DAYS = 2          # a verification-log gap larger than this fails GREEN
SMALL_N = 200             # below this many PIT obs, a failure is AMBER not RED
DEFAULT_BINS = 10
ALPHA = 0.05              # flatness-test significance and consistency-bar coverage


# ----------------------------------------------------------------------------- #
# No-scipy chi-square survival function via the regularised incomplete gamma Q.
# Numerical Recipes gser/gcf; double precision, converges in a few dozen terms.
# ----------------------------------------------------------------------------- #
def _gser(a: float, x: float) -> float:
    """Lower regularised incomplete gamma P(a, x) by series (good for x < a+1)."""
    if x <= 0.0:
        return 0.0
    ap = a
    total = 1.0 / a
    delta = total
    for _ in range(500):
        ap += 1.0
        delta *= x / ap
        total += delta
        if abs(delta) < abs(total) * 1e-12:
            break
    return total * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gcf(a: float, x: float) -> float:
    """Upper regularised incomplete gamma Q(a, x) by continued fraction (x ≥ a+1)."""
    tiny = 1e-30
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def chisq_sf(x2: float, df: int) -> float:
    """Survival function P(Χ² > x2) for `df` degrees of freedom = Q(df/2, x2/2)."""
    if x2 <= 0.0:
        return 1.0
    a, x = df / 2.0, x2 / 2.0
    return 1.0 - _gser(a, x) if x < a + 1.0 else _gcf(a, x)


# ----------------------------------------------------------------------------- #
# PIT histogram, flatness test, consistency bars.
# ----------------------------------------------------------------------------- #
def pit_histogram(pits: list[float], bins: int = DEFAULT_BINS) -> list[int]:
    """Counts of PIT values falling in each of `bins` equal-width [0,1] bins."""
    counts = [0] * bins
    for p in pits:
        b = min(bins - 1, max(0, int(p * bins)))
        counts[b] += 1
    return counts


def pit_flatness_test(pits: list[float], bins: int = DEFAULT_BINS) -> dict:
    """Pearson chi-square of the PIT histogram vs the uniform expectation N/bins.
    Returns {chi2, df, pvalue, n, bins}. A small p-value rejects calibration
    (the histogram is not flat: U-shape = under-dispersion, ∩-shape = over-)."""
    n = len(pits)
    if n == 0:
        return {"chi2": None, "df": bins - 1, "pvalue": None, "n": 0, "bins": bins}
    obs = pit_histogram(pits, bins)
    exp = n / bins
    chi2 = sum((o - exp) ** 2 / exp for o in obs)
    df = bins - 1
    return {"chi2": chi2, "df": df, "pvalue": chisq_sf(chi2, df), "n": n, "bins": bins}


def _binom_cdf(k: int, n: int, p: float) -> float:
    """Exact Binomial CDF P(X ≤ k); fine for the n,bins here (no scipy)."""
    return sum(math.comb(n, j) * p ** j * (1.0 - p) ** (n - j) for j in range(0, k + 1))


def consistency_bars(n: int, bins: int = DEFAULT_BINS, alpha: float = ALPHA) -> tuple[int, int]:
    """Bröcker & Smith (2007) consistency bar for one PIT bin: the central
    (1−alpha) Binomial(n, 1/bins) count range a CALIBRATED forecast's bin should
    stay within. Returns (lo, hi) counts. A bin outside [lo, hi] is 'inconsistent
    with calibration' on the histogram."""
    if n <= 0:
        return (0, 0)
    p = 1.0 / bins
    lo = next((k for k in range(n + 1) if _binom_cdf(k, n, p) >= alpha / 2.0), 0)
    hi = next((k for k in range(n + 1) if _binom_cdf(k, n, p) >= 1.0 - alpha / 2.0), n)
    return (lo, hi)


# ----------------------------------------------------------------------------- #
# Log-gap detector and the tier decision.
# ----------------------------------------------------------------------------- #
def log_gaps(dates: list[str], max_gap_days: int = MAX_GAP_DAYS) -> list[tuple[str, str, int]]:
    """Gaps in a sorted ISO-date list larger than `max_gap_days`. Returns
    (prev, next, gap_days) triples — an empty list means the log is dense."""
    ds = sorted({dt.date.fromisoformat(d) for d in dates})
    gaps = []
    for a, b in zip(ds, ds[1:]):
        g = (b - a).days
        if g > max_gap_days:
            gaps.append((a.isoformat(), b.isoformat(), g))
    return gaps


def calibration_tier(
    pits: list[float],
    dates: list[str],
    *,
    compiles: bool = True,
    bins: int = DEFAULT_BINS,
    alpha: float = ALPHA,
    small_n: int = SMALL_N,
    max_gap_days: int = MAX_GAP_DAYS,
) -> dict:
    """Combine compile status, PIT flatness, and log density into GREEN/AMBER/RED.

    RED blocks the daily verdict from emitting bucket probabilities (the caller
    should emit 'REFUSED: calibration' instead). AMBER reports a small-sample
    miscalibration without blocking. GREEN is clear to emit.
    """
    flat = pit_flatness_test(pits, bins)
    gaps = log_gaps(dates, max_gap_days) if dates else []
    n = flat["n"]
    pval = flat["pvalue"]
    pit_fails = (pval is not None) and (pval < alpha)

    reasons: list[str] = []
    if not compiles:
        tier = "RED"; reasons.append("compile failure")
    elif gaps:
        tier = "RED"; reasons.append(f"{len(gaps)} verification-log gap(s) > {max_gap_days}d")
    elif pit_fails and n >= small_n:
        tier = "RED"; reasons.append(f"PIT flatness rejected (p={pval:.3g}) on n={n} ≥ {small_n}")
    elif pit_fails:
        tier = "AMBER"; reasons.append(f"PIT flatness rejected (p={pval:.3g}) on small n={n} < {small_n}")
    elif n == 0:
        tier = "AMBER"; reasons.append("no held-out PIT values to test")
    else:
        tier = "GREEN"; reasons.append(f"PIT flat (p={pval:.3g}, n={n}); log dense")

    return {
        "tier": tier,
        "reasons": reasons,
        "blocks_emit": tier == "RED",
        "recalibration": "beta/Platt (parametric; never isotonic < %d obs)" % small_n
                         if tier == "AMBER" else None,
        "flatness": flat,
        "gaps": gaps,
        "histogram": pit_histogram(pits, bins) if pits else [],
        "consistency_bar": consistency_bars(n, bins, alpha) if n else (0, 0),
    }


def _self_test() -> None:
    """Deterministic oracles: chi-square SF matches known values; a uniform PIT
    stream passes (GREEN), a U-shaped (under-dispersed) large stream fails RED, the
    same shape on a small stream is AMBER, and a log gap forces RED."""
    import random

    # 1) chi-square SF sanity: median of Χ²_1 ≈ 0.4549 -> SF ≈ 0.5; SF(0)=1.
    assert abs(chisq_sf(0.4549, 1) - 0.5) < 1e-3, chisq_sf(0.4549, 1)
    assert chisq_sf(0.0, 5) == 1.0
    assert abs(chisq_sf(11.07, 5) - 0.05) < 2e-3, chisq_sf(11.07, 5)   # Χ²_5 95th pct

    rng = random.Random(43)
    # 2) Calibrated (uniform) PIT, large n, dense dates => GREEN.
    pits_u = [rng.random() for _ in range(400)]
    dates = [(dt.date(2025, 1, 1) + dt.timedelta(days=i)).isoformat() for i in range(400)]
    t = calibration_tier(pits_u, dates)
    assert t["tier"] == "GREEN", t

    # 3) Under-dispersed PIT (mass piled at 0 and 1), large n => RED.
    pits_u2 = [(0.0 if rng.random() < 0.5 else 1.0) + rng.uniform(-0.08, 0.08) for _ in range(400)]
    pits_u2 = [min(0.999, max(0.001, p)) for p in pits_u2]
    t2 = calibration_tier(pits_u2, dates)
    assert t2["tier"] == "RED" and t2["blocks_emit"], t2

    # 4) Same miscalibration on a SMALL sample => AMBER, does not block.
    small_dates = dates[:40]
    t3 = calibration_tier(pits_u2[:40], small_dates)
    assert t3["tier"] == "AMBER" and not t3["blocks_emit"], t3

    # 5) A log gap > MAX_GAP_DAYS forces RED even with flat PIT.
    gapped = dates[:50] + [(dt.date(2025, 1, 1) + dt.timedelta(days=60 + i)).isoformat()
                           for i in range(350)]
    t4 = calibration_tier(pits_u, gapped)
    assert t4["tier"] == "RED" and any("gap" in r for r in t4["reasons"]), t4

    # 6) Consistency bars: a calibrated bin of N=400, 10 bins (E=40) brackets 40.
    lo, hi = consistency_bars(400, 10, 0.05)
    assert lo < 40 < hi, (lo, hi)

    print("calibration_gate self-test PASSED "
          "(chi2 SF matches tables; uniform=GREEN; under-dispersed large=RED; "
          "small=AMBER; log-gap=RED; consistency bar brackets E)")


if __name__ == "__main__":
    _self_test()
