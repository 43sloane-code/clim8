"""Proper scoring rules for the council's *probabilistic* forecasts.

The council already turns its point verdict into an empirical predictive
distribution — the held-out residual cloud (Validation.residuals_*) that
compare.py resamples into market-bucket probabilities. Once a forecast asserts
probabilities, point error (MAE) and a ±2 °C hit-rate no longer tell you whether
those probabilities are *honest*: a confidently-narrow forecast and a vaguely-wide
one can share an MAE while making completely different probability claims.

The standard fix in the probabilistic-forecasting literature is a **proper
scoring rule** plus an explicit **calibration** check:

  * CRPS — the Continuous Ranked Probability Score (Matheson & Winkler 1976;
    Gneiting & Raftery 2007). Strictly proper: it is optimised in expectation
    only by the true predictive distribution, so it cannot be gamed by over- or
    under-stating confidence. It rewards calibration AND sharpness jointly, is in
    the same unit as the variable (°C here), and degenerates to absolute error
    when the forecast is a single point — so it sits directly beside the existing
    MAE on the same scale.
  * Coverage / PIT — an 80 % central predictive interval should contain the
    outcome ~80 % of the time. Systematic under-coverage is the classic
    ensemble *under-dispersion* (Hamill & Colucci 1997; Gneiting et al. 2007):
    the forecast is over-confident. This is measured, never assumed.

Everything here is non-parametric: the predictive distribution IS the empirical
sample of real held-out residuals. No distribution is assumed and no value is
model-generated — consistent with the project's verifiable-outputs principle.
A Gaussian CRPS closed form is included only as an optional cross-check.

Stdlib only (math); no numpy/scipy.
"""

from __future__ import annotations

__all__ = [
    'crps_sample', 'crps_gaussian', 'quantile', 'interval_coverage', 'pit'
]

import math


def crps_sample(samples: list[float], y: float, *, fair: bool = True) -> float:
    """CRPS of an empirical predictive distribution (the sample `samples`)
    against the observed scalar `y`, via the energy form

        CRPS(F, y) = E|X − y| − ½ E|X − X'|

    estimated from the sample. With `fair=True` the second term uses the
    unbiased 1/(n(n−1)) estimator (Ferro 2014; Zamo & Naveau 2018), which avoids
    the small-sample optimism of the 1/n² plug-in — important here, where the
    held-out sample is only tens of points. Computed in O(n log n) via the sorted
    closed form rather than the O(n²) double sum.

    For n == 1 the spread term vanishes and CRPS reduces to |x − y| (absolute
    error), exactly the deterministic limit — so a point forecast is scored on
    the same scale as the MAE the council already reports.
    """
    n = len(samples)
    if n == 0:
        raise ValueError("crps_sample needs at least one sample")
    if n == 1:
        return abs(samples[0] - y)

    s = sorted(samples)
    # E|X − y|: mean absolute deviation of the sample from the observation.
    mad_y = sum(abs(x - y) for x in s) / n

    # Σ_i Σ_j |x_i − x_j| = 2 Σ_i (2i − n − 1) x_(i)  for 1-indexed sorted x.
    pair_sum = 0.0
    for k, x in enumerate(s):           # k is 0-indexed; i = k + 1
        pair_sum += (2 * (k + 1) - n - 1) * x
    # pair_sum == Σ_{i<j}(x_(j) − x_(i)); the full double sum Σ_iΣ_j|x_i−x_j| is
    # 2*pair_sum, and ½·E|X−X'| = (2*pair_sum)/(2*denom) = pair_sum/denom.
    denom = n * (n - 1) if fair else n * n
    spread = pair_sum / denom           # = ½ E|X−X'| under the chosen estimator
    return mad_y - spread


def _phi(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _Phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def crps_gaussian(mu: float, sigma: float, y: float) -> float:
    """Closed-form CRPS for a Gaussian predictive N(mu, sigma²) (Gneiting et al.
    2005). Provided only as a cross-check against the non-parametric estimate; the
    live path uses the empirical distribution so it never imposes Gaussianity on
    temperature residuals, which can be skewed."""
    if sigma <= 0:
        return abs(mu - y)
    w = (y - mu) / sigma
    return sigma * (w * (2.0 * _Phi(w) - 1.0) + 2.0 * _phi(w) - 1.0 / math.sqrt(math.pi))


def quantile(values: list[float], q: float) -> float:
    """Type-7-ish empirical quantile by linear interpolation (matches the
    convention used for the ensemble band), q in [0, 1]."""
    if not values:
        raise ValueError("quantile of empty sample")
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] + frac * (s[hi] - s[lo])


def interval_coverage(samples: list[float], y: float,
                      lo_q: float = 0.10, hi_q: float = 0.90) -> tuple[bool, float]:
    """Does the central (hi_q − lo_q) predictive interval contain `y`?
    Returns (covered, interval_width). Default is the 80 % central interval."""
    lo = quantile(samples, lo_q)
    hi = quantile(samples, hi_q)
    return (lo <= y <= hi), (hi - lo)


def pit(samples: list[float], y: float) -> float:
    """Probability Integral Transform value: the empirical CDF of the predictive
    sample evaluated at the outcome, F(y) ≈ rank/(n+1). A calibrated forecast
    produces PIT values uniform on [0, 1]; clustering near 0/1 signals
    under-dispersion. Returned for optional calibration histograms."""
    n = len(samples)
    below = sum(1 for x in samples if x <= y)
    return below / (n + 1)


def _self_test() -> None:
    """Reproducible correctness oracle for the estimators this module ships.

    Run with `python3 -m weather_council.scoring`. It checks the fast CRPS against
    the O(n²) definition (algebra), against the analytic Gaussian CRPS (semantics
    — that the energy form IS CRPS for a known law), the deterministic point limit,
    PIT uniformity, and interval coverage. No new files, no deps; the closed-form
    cross-check (crps_gaussian) and PIT thus stay exercised by shipped code rather
    than sitting as latent API."""
    import random
    import statistics as st

    rng = random.Random(20240608)

    # 1. Fast sorted form == brute-force double sum (plug-in estimator).
    worst = 0.0
    for _ in range(400):
        m = rng.randint(2, 40)
        s = [rng.gauss(0, 5) for _ in range(m)]
        y = rng.gauss(0, 5)
        mad = sum(abs(x - y) for x in s) / m
        brute = mad - 0.5 * sum(abs(a - b) for a in s for b in s) / (m * m)
        assert abs(crps_sample(s, y, fair=False) - brute) < 1e-9
        worst = max(worst, abs(crps_sample(s, y, fair=False) - brute))

    # 2. Energy form == analytic Gaussian CRPS (validates the SEMANTICS).
    mu, sig, big = 12.0, 3.0, 200_000
    samp = [rng.gauss(mu, sig) for _ in range(big)]
    for y in (6.0, 9.0, 12.0, 15.0, 18.0):
        assert abs(crps_sample(samp, y) - crps_gaussian(mu, sig, y)) < 0.02

    # 3. Deterministic limit: a one-point forecast scores as absolute error.
    assert crps_sample([7.3], 4.1) == abs(7.3 - 4.1)
    assert crps_gaussian(7.3, 0.0, 4.1) == abs(7.3 - 4.1)

    # 4. PIT of a calibrated forecast is ~Uniform(0,1): mean 0.5, sd 1/√12.
    pits = [pit(samp[:2000], rng.gauss(mu, sig)) for _ in range(3000)]
    assert abs(st.mean(pits) - 0.5) < 0.02
    assert abs(st.pstdev(pits) - (1 / 12 ** 0.5)) < 0.02

    # 5. An 80% interval covers ~80% of draws from the same law.
    hits = sum(interval_coverage(samp[:5000], rng.gauss(mu, sig))[0] for _ in range(4000))
    assert 0.76 <= hits / 4000 <= 0.84

    print(f"scoring self-test PASSED "
          f"(crps fast-vs-brute max {worst:.2e}; energy==Gaussian; PIT uniform; coverage ~80%)")


if __name__ == "__main__":
    _self_test()
