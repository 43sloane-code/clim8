"""Reconcile — the §5 promotion machinery for the corroborated tier's own %.

Frozen design (binding): the corroborated tier serves the RECORDED-bucket % until a
MEASURED confirmation rate clears the §5 promotion gate. The gate, in full:

  * n ≥ 30 reconciled corroborated leads PER CITY (a lead is "confirmed" when the
    settlement record's final max reaches the led value);
  * the served rate is the JEFFREYS 95% LOWER BOUND of the confirmations — never
    the raw rate (a point rate on n=30 overstates the tail);
  * DISJOINT fit/verify: the rate is fit on one chronological half and must hold on
    the verify half (sign-stable, no refit);
  * POOLING compatibility test: cities pool only when their rates are compatible
    (a chi-square style homogeneity check at the frozen alpha);
  * REGRESSION tripwire: a served-tier regression reverts the tier to
    MEASURED-PENDING (recorded-bucket %) automatically.

At go-live the state is MEASURED-PENDING: the ObsLog (Phase 1) only begins accruing
the reconciliation data now, so no gate can be cleared. This module computes the
state from whatever has accrued; it never serves a number itself. Stdlib only.
KAT'd indirectly via tests/test_cur_f_guard.py; self-test below pins the Jeffreys
math against reference values.
"""
from __future__ import annotations

__all__ = ["MIN_N_PER_CITY", "jeffreys_lower_bound", "confirmation_rate",
           "promotion_state"]

import math

MIN_N_PER_CITY = 30
# Frozen promotion bars (§5): verify-half must hold the fit-half's direction, and
# pooled cities must pass the homogeneity check at this alpha.
_FIT_VERIFY_TOL = 0.05
_POOL_ALPHA = 0.05


# --------------------------------------------------------------------- beta math

def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Numerical Recipes)."""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < 1e-300:
        d = 1e-300
    d = 1.0 / d
    h = d
    for m in range(1, 200):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-300:
            d = 1e-300
        c = 1.0 + aa / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-300:
            d = 1e-300
        c = 1.0 + aa / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-14:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                  + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def jeffreys_lower_bound(confirmed: int, n: int, level: float = 0.95) -> float | None:
    """The Jeffreys-prior (Beta(k+0.5, n-k+0.5)) lower credible bound at `level` —
    the §5 serve-able confirmation rate. Bisection on the beta CDF. None when n=0
    (no rate is servable without reconciled data)."""
    if n <= 0 or not (0 <= confirmed <= n):
        return None
    a, b = confirmed + 0.5, n - confirmed + 0.5
    target = 1.0 - level
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if _betai(a, b, mid) < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ------------------------------------------------------------------ §5 the gate

def confirmation_rate(confirmed: int, n: int) -> float | None:
    """Raw reconciled confirmation rate (display/audit only — the SERVED figure is
    always the Jeffreys lower bound)."""
    return (confirmed / n) if n > 0 else None


def _homogeneous(rates: list[tuple[int, int]], alpha: float = _POOL_ALPHA) -> bool:
    """Pooling compatibility: chi-square homogeneity of the per-city confirmation
    rates against the pooled rate. True when compatible (or a single city)."""
    cells = [(k, n) for k, n in rates if n > 0]
    if len(cells) < 2:
        return True
    total_n = sum(n for _, n in cells)
    pooled = sum(k for k, _ in cells) / total_n
    if pooled in (0.0, 1.0):
        return all(k == round(pooled * n) for k, n in cells)
    chi2 = 0.0
    for k, n in cells:
        e = pooled * n
        chi2 += (k - e) ** 2 / e + ((n - k) - (n - e)) ** 2 / (n - e)
    # df = cities-1; the 0.95 quantile of chi2(df) for the small dfs in play.
    df = len(cells) - 1
    crit = {1: 3.841, 2: 5.991, 3: 7.815, 4: 9.488, 5: 11.070}.get(df)
    if crit is None:
        crit = df + 2.0 * math.sqrt(2.0 * df)      # conservative normal approx
    return chi2 <= crit


def promotion_state(per_city: dict[str, dict] | None) -> dict:
    """The §5 gate, evaluated on the reconciled ledger. `per_city` maps city ->
    {confirmed, n, fit: {confirmed, n}, verify: {confirmed, n}, regression: bool}.
    Returns {state, gates, served_rate}: state is "SUPPORTED" only when EVERY frozen
    gate clears — n≥30/city, disjoint fit/verify sign-stability, pooling
    compatibility, no regression tripwire. Anything missing or thin ->
    "MEASURED-PENDING" and the corroborated tier keeps serving the RECORDED-bucket
    % (D1). The served_rate (Jeffreys 95% lower bound) is reported only on
    SUPPORTED; otherwise None."""
    gates = {"n_per_city": False, "fit_verify": False,
             "pooling": False, "regression_tripwire": True}
    cities = {c: s for c, s in (per_city or {}).items() if isinstance(s, dict)}
    if not cities:
        return {"state": "MEASURED-PENDING", "gates": gates, "served_rate": None}

    gates["n_per_city"] = all(int(s.get("n") or 0) >= MIN_N_PER_CITY
                              for s in cities.values())
    fit_ok = True
    for s in cities.values():
        fit, ver = s.get("fit") or {}, s.get("verify") or {}
        fr = confirmation_rate(int(fit.get("confirmed") or 0), int(fit.get("n") or 0))
        vr = confirmation_rate(int(ver.get("confirmed") or 0), int(ver.get("n") or 0))
        if fr is None or vr is None or vr < fr - _FIT_VERIFY_TOL:
            fit_ok = False
            break
    gates["fit_verify"] = fit_ok
    gates["pooling"] = _homogeneous([(int(s.get("confirmed") or 0),
                                      int(s.get("n") or 0))
                                     for s in cities.values()])
    gates["regression_tripwire"] = not any(bool(s.get("regression"))
                                           for s in cities.values())

    if not all(gates.values()):
        return {"state": "MEASURED-PENDING", "gates": gates, "served_rate": None}
    k = sum(int(s.get("confirmed") or 0) for s in cities.values())
    n = sum(int(s.get("n") or 0) for s in cities.values())
    return {"state": "SUPPORTED", "gates": gates,
            "served_rate": jeffreys_lower_bound(k, n)}


def _self_test() -> None:
    # Beta math pinned against exact anchors: I_0.5(2,3) has the closed form 0.6875,
    # and the quantiles obey the symmetry qbeta(p,a,b) = 1 - qbeta(1-p,b,a).
    assert abs(_betai(2, 3, 0.5) - 0.6875) < 1e-12
    assert abs(jeffreys_lower_bound(0, 30) - 6.4990e-05) < 1e-8
    assert abs(jeffreys_lower_bound(30, 30) - 0.938483) < 1e-6
    assert abs(jeffreys_lower_bound(30, 30)
               + jeffreys_lower_bound(0, 30, level=0.05) - 1.0) < 1e-9
    assert abs(jeffreys_lower_bound(25, 30) - 0.700524) < 1e-6
    assert jeffreys_lower_bound(0, 0) is None
    # Gate: thin n -> MEASURED-PENDING; full gate clears -> SUPPORTED with the
    # Jeffreys bound, not the raw rate; a regression tripwire reverts.
    thin = {"ksfo": {"confirmed": 9, "n": 10, "fit": {"confirmed": 4, "n": 5},
                     "verify": {"confirmed": 5, "n": 5}}}
    assert promotion_state(thin)["state"] == "MEASURED-PENDING"
    full = {"ksfo": {"confirmed": 27, "n": 30,
                     "fit": {"confirmed": 14, "n": 15},
                     "verify": {"confirmed": 13, "n": 15}}}
    # fit 14/15 = .933, verify 13/15 = .867: within the frozen fit/verify tolerance
    st = promotion_state(full)
    assert st["state"] == "MEASURED-PENDING"          # .867 < .933 - .05 tolerance
    full = {"ksfo": {"confirmed": 28, "n": 30,
                     "fit": {"confirmed": 14, "n": 15},
                     "verify": {"confirmed": 14, "n": 15}}}
    st = promotion_state(full)
    assert st["state"] == "SUPPORTED"
    assert abs(st["served_rate"] - jeffreys_lower_bound(28, 30)) < 1e-12
    assert st["served_rate"] < 28 / 30                     # lower bound, not raw rate
    trip = dict(full, ksfo=dict(full["ksfo"], regression=True))
    assert promotion_state(trip)["state"] == "MEASURED-PENDING"
    bad_pool = {"a": {"confirmed": 29, "n": 30, "fit": {"confirmed": 14, "n": 15},
                      "verify": {"confirmed": 15, "n": 15}},
                "b": {"confirmed": 15, "n": 30, "fit": {"confirmed": 7, "n": 15},
                      "verify": {"confirmed": 8, "n": 15}}}
    assert promotion_state(bad_pool)["state"] == "MEASURED-PENDING"
    print("reconcile self-test PASSED (Jeffreys bounds match reference; §5 gate "
          "MEASURED-PENDING until n/fit-verify/pooling/no-regression ALL clear)")


if __name__ == "__main__":
    _self_test()
