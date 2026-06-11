"""Candidate 40 — a residual local-level Kalman bias-drift filter (ADDITIVE).

What it sits on top of
----------------------
The council already removes a per-member bias before blending (ledger #34:
seasonal-analog + recency-weighted bias, see `council.applied_bias_correction`).
The recency channel is an exponential smoother of past residuals with a FIXED
halflife. This module is the principled state-space generalisation of exactly
that smoother — a local-level (random-walk-plus-noise) model whose smoothing
constant is FIT by maximum likelihood rather than guessed:

    b_t = b_{t-1} + eta_t      eta_t ~ N(0, q)      (the bias drifts slowly)
    r_t = b_t     + eps_t      eps_t ~ N(0, s)      (residual = bias + noise)

Here ``r_t`` is the council residual the harness actually leaves behind AFTER the
#34 correction, in the harness's own sign convention ``r = obs - pred`` (realized
minus forecast). So a positive filtered bias means the council ran COLD and the
correction ADDS it back: ``central_corrected = central + b_{t|t-1}`` (note the
sign — the originating spec wrote ``central - b_t`` under the opposite residual
convention; re-derived here against `tools.daily_healthcheck._walk_forward`).
The predictive cloud is widened to the filter's one-step variance
``P_{t|t-1} + s``.

Why it is honestly ONE lever
----------------------------
The live walk-forward dresses each day with the EMPIRICAL residual cloud and
scores it with `crps_sample` (it deliberately never assumes Gaussian residuals).
To compare like with like, the challenger is that SAME empirical cloud, merely
RELOCATED to the Kalman one-step bias and variance-matched to the Kalman one-step
variance — its shape (skew/kurtosis) is preserved. The only thing that changes is
the cloud's first two moments. There is no Gaussian-vs-empirical confound; the
lever is "use the Kalman one-step (mean, variance) instead of the pooled
trailing (mean, variance)", nothing else.

Leak-free discipline
--------------------
The hyperparameters (q, s) are fit by MLE on the TRAINING half only; at each
held-out test day the filter state uses ONLY strictly-earlier residuals. The
train/test split makes the reported CRPS delta genuinely held-out.

Recommend-only: never mutates the served Verdict or run.py. Stdlib only
(math, statistics).
"""

from __future__ import annotations

__all__ = [
    "WARMUP", "CRPS_MIN",
    "kalman_local_level", "fit_qs", "kalman_one_step", "walk_forward_kalman",
]

import math
import statistics

from .scoring import crps_sample

WARMUP = 10          # min strictly-earlier days before a day is held out (matches healthcheck)
CRPS_MIN = 10        # min residual-cloud size before a CRPS is computed (matches healthcheck)
_DIFFUSE_P0 = 1e4    # diffuse prior variance on b_0 (b_0 = 0)


# --------------------------------------------------------------------------- #
# The forward filter.
# --------------------------------------------------------------------------- #
def kalman_local_level(resid, q: float, s: float, *, b0: float = 0.0,
                       P0: float = _DIFFUSE_P0, n_diffuse: int = 1) -> dict:
    """Forward local-level (random-walk + noise) Kalman filter over `resid`.

    Returns per-step the one-step-ahead predicted state (b_pred, P_pred) BEFORE
    seeing r_t, the filtered state (b_filt, P_filt) AFTER, the Kalman gains, and
    the Gaussian one-step log-likelihood EXCLUDING the first `n_diffuse` terms
    (whose predictive variance is dominated by the diffuse prior P0 and carries no
    information about q, s — standard diffuse-likelihood practice).
    """
    b_pred, P_pred, b_filt, P_filt, gains = [], [], [], [], []
    b, P = b0, P0
    loglik = 0.0
    for i, r in enumerate(resid):
        bp = b                       # predict: random walk leaves the mean unchanged
        Pp = P + q
        S = Pp + s                   # innovation (one-step predictive) variance
        nu = r - bp                  # innovation
        if i >= n_diffuse:
            loglik += -0.5 * (math.log(2.0 * math.pi * S) + nu * nu / S)
        K = Pp / S                   # Kalman gain in (0, 1)
        b = bp + K * nu
        P = (1.0 - K) * Pp
        b_pred.append(bp); P_pred.append(Pp)
        b_filt.append(b); P_filt.append(P); gains.append(K)
    return {"b_pred": b_pred, "P_pred": P_pred, "b_filt": b_filt,
            "P_filt": P_filt, "gains": gains, "loglik": loglik}


def fit_qs(resid, *, refine_rounds: int = 3) -> tuple[float, float, float]:
    """MLE of (q, s) for the local-level model by a deterministic coarse-to-fine
    log-grid search (no scipy). Parametrised by the observation variance s and the
    signal-to-noise ratio psi = q/s, both scaled to the data variance, then
    refined by repeatedly halving the log-grid spacing around the incumbent.
    Returns (q, s, loglik)."""
    n = len(resid)
    v = statistics.pvariance(resid) if n >= 2 else 1.0
    v = max(v, 1e-6)

    def ll(q, s):
        return kalman_local_level(resid, q, s)["loglik"]

    # Coarse log grid: s in [0.1v, 1.5v]; psi (=q/s) in [1e-4, 10].
    s_exps = [math.log10(v) + e for e in (-1.0, -0.6, -0.3, 0.0, 0.18)]
    psi_exps = [-4.0, -3.0, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0]
    best = None
    for se in s_exps:
        s = 10.0 ** se
        for pe in psi_exps:
            q = (10.0 ** pe) * s
            cur = ll(q, s)
            if best is None or cur > best[0]:
                best = (cur, math.log10(q), se)        # store log10 q, log10 s

    # Coarse-to-fine refine: shrink the search box around the incumbent.
    _, lq, ls = best
    step = 0.5
    for _ in range(refine_rounds):
        local = best
        for dls in (-step, 0.0, step):
            for dlq in (-step, 0.0, step):
                s = 10.0 ** (ls + dls)
                q = 10.0 ** (lq + dlq)
                cur = ll(q, s)
                if cur > local[0]:
                    local = (cur, lq + dlq, ls + dls)
        best = local
        _, lq, ls = best
        step *= 0.5

    return 10.0 ** best[1], 10.0 ** best[2], best[0]


def kalman_one_step(prior, q: float, s: float) -> tuple[float, float]:
    """One-step-ahead predicted (bias, variance) for the next residual, given ONLY
    the strictly-earlier residuals `prior`. b_{t|t-1} = b_{t-1|t-1} (random walk);
    the predictive variance is P_{t|t-1} + s. Leak-free by construction."""
    if not prior:
        return 0.0, _DIFFUSE_P0
    f = kalman_local_level(prior, q, s)
    b_hat = f["b_filt"][-1]                  # one-step predicted mean
    P_one = f["P_filt"][-1] + q              # one-step predicted state variance
    return b_hat, P_one + s                  # (bias, one-step predictive variance of r_t)


def _kalman_cloud(prior, b_hat: float, v_one: float) -> list[float]:
    """The challenger predictive: the SAME empirical residual cloud relocated to
    the Kalman one-step bias and variance-matched to the one-step variance, with
    its shape (skew/kurtosis) preserved. Reduces to the baseline cloud when the
    filter has nothing to add (b_hat == pooled mean, v_one == pooled variance)."""
    m = statistics.mean(prior)
    vv = statistics.pvariance(prior) if len(prior) >= 2 else 0.0
    scale = math.sqrt(v_one / vv) if vv > 1e-12 else 1.0
    return [b_hat + scale * (e - m) for e in prior]


# --------------------------------------------------------------------------- #
# Leak-free walk-forward: paired baseline-vs-Kalman CRPS on the residual stream.
# --------------------------------------------------------------------------- #
def walk_forward_kalman(rows, *, warmup: int = WARMUP, crps_min: int = CRPS_MIN,
                        train_frac: float = 0.5) -> dict:
    """Replay one (date, point, realized) stream with the harness's leak-free
    residual construction, fitting (q, s) on the TRAINING half and scoring the
    HELD-OUT half. For each test day, score r_t with both the pooled empirical
    cloud (baseline = the live path) and the Kalman-relocated cloud (challenger).

    Returns a dict with the per-day CRPS deltas (baseline − challenger; >0 ⇒
    Kalman better), the fitted (q, s), the steady-state gain and max |bias| for
    the single-day-update sanity check, and the test-window dates.
    """
    rows = sorted(rows)
    resid = [rz - pt for _, pt, rz in rows]              # obs − pred (harness sign)
    dates = [d for d, _, _ in rows]
    n = len(resid)

    test_start = max(warmup, int(round(n * train_frac)))
    train_resid = resid[warmup:test_start]
    out = {
        "n_rows": n, "n_test": 0, "q": None, "s": None,
        "deltas": [], "test_dates": [],
        "mean_crps_base": None, "mean_crps_kalman": None,
        "steady_gain": None, "max_bias": None,
        "powered": False,
    }
    if len(train_resid) < crps_min:
        out["note"] = f"train half {len(train_resid)} < CRPS_MIN {crps_min}"
        return out

    q, s, _ = fit_qs(train_resid)
    out["q"], out["s"] = q, s

    base, kal, deltas, tdates = [], [], [], []
    for t in range(test_start, n):
        prior = resid[:t]
        if len(prior) < crps_min:
            continue
        r_t = resid[t]
        cb = crps_sample(prior, r_t)
        b_hat, v_one = kalman_one_step(prior, q, s)
        ck = crps_sample(_kalman_cloud(prior, b_hat, v_one), r_t)
        base.append(cb); kal.append(ck)
        deltas.append(cb - ck)                            # >0 ⇒ Kalman lower CRPS ⇒ better
        tdates.append(dates[t])

    out["n_test"] = len(deltas)
    out["deltas"] = deltas
    out["test_dates"] = tdates
    out["mean_crps_base"] = statistics.mean(base) if base else None
    out["mean_crps_kalman"] = statistics.mean(kal) if kal else None
    out["powered"] = len(deltas) >= 30

    # Single-day-update sanity: the steady-state Kalman gain IS the fraction of a
    # day's residual the bias estimate moves by. Near 1.0 ⇒ the filter just chases
    # the last residual (overfitting noise); a sane bias-drift filter sits well
    # below that. Also report the largest bias the filter ever asserts.
    full = kalman_local_level(resid, q, s)
    tail = full["gains"][-min(20, len(full["gains"])):] or [0.0]
    out["steady_gain"] = statistics.mean(tail)
    out["max_bias"] = max((abs(b) for b in full["b_filt"]), default=0.0)
    return out


def _self_test() -> None:
    """Deterministic oracles. A genuinely drifting bias series: the Kalman tracks
    it and BEATS the pooled cloud (point delta > 0). A pure-noise series (the
    negative control): the filter adds nothing and the paired CI INCLUDES zero —
    the overfit guard. Plus filter-recursion sanity (gains in (0,1); constant
    series ⇒ filtered bias → the constant) and a positive (q, s) fit."""
    import random

    # Reuse the harness's own seeded paired bootstrap so the verdict logic matches.
    from tools.daily_healthcheck import _paired_bootstrap_ci

    rng = random.Random(40)

    # 1) Filter recursion sanity: a constant residual c is tracked to c; gains∈(0,1).
    f = kalman_local_level([2.0] * 60, q=0.05, s=1.0)
    assert all(0.0 < k < 1.0 for k in f["gains"]), f["gains"][:3]
    assert abs(f["b_filt"][-1] - 2.0) < 0.2, f["b_filt"][-1]
    f0 = kalman_local_level([0.0] * 60, q=0.05, s=1.0)
    assert abs(f0["b_filt"][-1]) < 1e-6, f0["b_filt"][-1]

    # 2) fit returns strictly positive variances.
    q, s, ll = fit_qs([rng.gauss(0, 1) for _ in range(120)])
    assert q > 0 and s > 0 and math.isfinite(ll), (q, s, ll)

    # 3) POSITIVE control — a slow random-walk bias plus noise. The Kalman tracks
    #    the moving bias; the pooled cloud lags it, so Kalman CRPS is lower.
    b, drift = 0.0, []
    for _ in range(400):
        b += rng.gauss(0, 0.10)                 # the bias random-walks
        drift.append(b + rng.gauss(0, 0.6))     # observed residual = bias + noise
    rows_drift = [(f"2025-{1+i//28:02d}-{1+i%28:02d}", 0.0, x) for i, x in enumerate(drift)]
    res = walk_forward_kalman(rows_drift)
    assert res["n_test"] >= 30, res["n_test"]
    pt, lo, hi, _ = _paired_bootstrap_ci(res["deltas"])
    assert pt > 0, ("drift control should favour Kalman", pt)

    # 4) NEGATIVE control — pure i.i.d. noise, NO drift. The filter must NOT
    #    manufacture an edge: the paired CI lower bound is NOT positive (no
    #    significant improvement), and the effect size is negligible. (On pure
    #    noise the relocation/variance-match adds at most a hair of CRPS — an
    #    honest tiny LOSS, never a spurious win.)
    noise = [rng.gauss(0, 0.8) for _ in range(400)]
    rows_noise = [(f"2025-{1+i//28:02d}-{1+i%28:02d}", 0.0, x) for i, x in enumerate(noise)]
    resn = walk_forward_kalman(rows_noise)
    ptn, lon, hin, _ = _paired_bootstrap_ci(resn["deltas"])
    assert lon is not None and lon <= 0.0, ("noise must not be a significant win", lon, ptn, hin)
    assert abs(ptn) < 0.05, ("noise effect must be negligible", ptn)
    # and the fitted filter should be sluggish on pure noise (small steady gain).
    assert resn["steady_gain"] < 0.6, resn["steady_gain"]

    print("residual_kalman self-test PASSED "
          "(filter tracks constant, gains∈(0,1); fit q,s>0; drift⇒Kalman wins; "
          "i.i.d. noise⇒no significant win, negligible effect, gain sluggish — no manufactured edge)")


if __name__ == "__main__":
    _self_test()
