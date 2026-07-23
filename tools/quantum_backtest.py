"""Walk-forward backtest: does a quantum (fidelity) kernel beat classical
baselines at predicting next-day station high temperature?

This isolates the ONE honest, classically-verifiable quantum-ML claim (see
weather_council/quantum_kernel.py): a product-state fidelity kernel evaluated on
identical features, head-to-head with a classical RBF kernel and two naive
baselines, on the SAME real station truth the council anchors on.

Method
------
* Truth: Hong Kong Observatory open data; London City Airport (EGLC) IEM METAR —
  the exact sensors each market settles on. Both are already disk-cached.
* Task: predict day t's high from a feature vector of the prior days' highs/lows
  plus a day-of-year seasonal term (sin/cos). Strictly causal — every model at
  day t is fit only on days < t (sliding window W).
* Models, identical features for all kernels:
    - persistence    : high_t  =  high_{t-1}
    - seasonal-mean  : mean high of training window
    - RBF-KRR        : kernel ridge, classical Gaussian kernel (median-heuristic gamma)
    - quantum-KRR    : kernel ridge, product-state fidelity kernel
* Scores: MAE (degC) and exact whole-degC bucket hit-rate (round-half-up) — the
  metric the market actually resolves on.
* Significance: paired sign test on per-day |error| differences (quantum vs the
  best classical model), two-sided, no scipy.

The point is not to crown a winner; it is to MEASURE whether the quantum kernel
earns its place. If it does not beat the classical baseline within noise, that is
the finding — reported plainly, the same off-switch every other mechanism gets.
"""
from __future__ import annotations

import datetime as dt
import math
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from weather_council.sources import Sources, _round_half_up
from weather_council import quantum_kernel as qk

WINDOW = 60          # sliding training window (days)
LAGS = 3             # how many prior days feed the feature vector
LAM = 0.5            # ridge regularisation (shared by both KRR models)


def _load_series() -> dict[str, dict[str, float]]:
    """{city -> {date_iso -> high_c}} from each market's real settlement sensor."""
    s = Sources()
    today = dt.date.today()
    out: dict[str, dict[str, float]] = {}
    hk = s.hko_truth_series(today, back_years=4)
    out["Hong Kong (HKO Observatory)"] = {d: hi for d, (hi, lo) in hk.items()}
    ldn = s.iem_overlay_truth_series("EGLC", "Europe/London", today, back_years=2)
    out["London (EGLC City Airport)"] = {d: hi for d, (hi, lo) in ldn.items()}
    return out


def _features(days: list[str], highs: dict[str, float],
              lows: dict[str, float], i: int) -> list[float] | None:
    """Causal feature vector for predicting highs[days[i]]: the prior LAGS days'
    high and low, plus a seasonal sin/cos. None if any lag is missing."""
    if i < LAGS:
        return None
    feat: list[float] = []
    for k in range(1, LAGS + 1):
        d = days[i - k]
        feat.append(highs[d])
        feat.append(lows.get(d, highs[d]))
    doy = dt.date.fromisoformat(days[i]).timetuple().tm_yday
    feat.append(10.0 * math.sin(2 * math.pi * doy / 365.25))
    feat.append(10.0 * math.cos(2 * math.pi * doy / 365.25))
    return feat


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _sign_test_p(diffs: list[float]) -> tuple[int, int, float]:
    """Two-sided sign test on paired diffs (quantum_err - classical_err).
    Returns (n_quantum_better, n_total_nonzero, p). Negative diff => quantum
    wins that day. Exact binomial tail, no scipy."""
    nz = [d for d in diffs if abs(d) > 1e-9]
    n = len(nz)
    wins = sum(1 for d in nz if d < 0)          # quantum strictly better
    if n == 0:
        return 0, 0, 1.0
    k = min(wins, n - wins)
    if n <= 1000:
        # exact two-sided binomial tail at p=0.5
        tail = sum(math.comb(n, j) for j in range(0, k + 1)) * (0.5 ** n)
        return wins, n, min(1.0, 2 * tail)
    # large n: normal approximation with continuity correction (erf, no scipy)
    z = (abs(wins - n / 2.0) - 0.5) / math.sqrt(n / 4.0)
    p = math.erfc(z / math.sqrt(2.0))           # 2*(1-Phi(z)) two-sided
    return wins, n, min(1.0, p)


def _backtest_city(name: str, hseries: dict[str, float]) -> None:
    days = sorted(hseries)
    highs = hseries
    lows = hseries   # only highs available here; lows feature reuses high (proxy)
    # Build the full causal dataset of (feature, target) keyed by index.
    rows: list[tuple[int, list[float], float]] = []
    for i in range(len(days)):
        f = _features(days, highs, lows, i)
        if f is None:
            continue
        rows.append((i, f, highs[days[i]]))

    # Accumulators per model.
    abs_err = {"persistence": [], "seasonal": [], "rbf": [], "quantum": []}
    hit = {"persistence": 0, "seasonal": 0, "rbf": 0, "quantum": 0}
    per_day_err = {"rbf": [], "quantum": []}      # aligned, for the sign test
    n_test = 0

    # Walk forward: predict row r using a sliding window of the prior WINDOW rows.
    for r in range(WINDOW, len(rows)):
        train = rows[r - WINDOW:r]
        i_t, f_t, y_t = rows[r]
        Xtr = [f for _, f, _ in train]
        ytr = [y for _, _, y in train]

        # Standardize features on the training window (fair to every model).
        mean, std = qk.standardize_fit(Xtr)
        Ztr = [qk.standardize_apply(x, mean, std) for x in Xtr]
        z_t = qk.standardize_apply(f_t, mean, std)

        # Persistence: previous day's high (lag-1 high is feature index 0, raw).
        pred_persist = f_t[0]
        # Seasonal mean: mean target over the window.
        pred_season = sum(ytr) / len(ytr)

        # RBF-KRR with median-heuristic gamma on standardized features.
        dists = []
        for a in range(0, len(Ztr), 4):           # subsample pairwise dists
            for b in range(a + 1, len(Ztr), 4):
                dists.append(sum((p - q) ** 2 for p, q in zip(Ztr[a], Ztr[b])))
        med = _median(dists) if dists else 1.0
        gamma = 1.0 / (2.0 * med) if med > 1e-9 else 0.5
        rbf = qk.KernelRidge(lambda u, v: qk.rbf_kernel(u, v, gamma), lam=LAM).fit(Ztr, ytr)
        pred_rbf = rbf.predict(z_t)

        # Quantum fidelity KRR: angle-encode the standardized features.
        Atr = [qk.angle_encode(z) for z in Ztr]
        a_t = qk.angle_encode(z_t)
        quant = qk.KernelRidge(qk.fidelity_kernel, lam=LAM).fit(Atr, ytr)
        pred_quant = quant.predict(a_t)

        preds = {"persistence": pred_persist, "seasonal": pred_season,
                 "rbf": pred_rbf, "quantum": pred_quant}
        for m, p in preds.items():
            abs_err[m].append(abs(p - y_t))
            if _round_half_up(p) == _round_half_up(y_t):
                hit[m] += 1
        per_day_err["rbf"].append(abs(pred_rbf - y_t))
        per_day_err["quantum"].append(abs(pred_quant - y_t))
        n_test += 1

    if n_test == 0:
        print(f"\n=== {name} ===\n  insufficient data (n_test=0)")
        return

    print(f"\n=== {name} ===")
    print(f"  test days={n_test}  window={WINDOW}  lags={LAGS}")
    print(f"  {'model':<12}{'MAE(degC)':>12}{'bucket-hit':>12}")
    for m in ("persistence", "seasonal", "rbf", "quantum"):
        mae = sum(abs_err[m]) / n_test
        hr = 100.0 * hit[m] / n_test
        print(f"  {m:<12}{mae:>12.3f}{hr:>11.1f}%")

    # Quantum vs best classical (lower MAE of persistence/seasonal/rbf).
    classical = {m: sum(abs_err[m]) / n_test for m in ("persistence", "seasonal", "rbf")}
    best_c = min(classical, key=classical.get)
    mae_q = sum(abs_err["quantum"]) / n_test
    delta = mae_q - classical[best_c]
    # sign test uses quantum vs rbf (the strongest like-for-like learner).
    diffs = [q - r for q, r in zip(per_day_err["quantum"], per_day_err["rbf"])]
    wins, nnz, p = _sign_test_p(diffs)
    print(f"  best classical: {best_c} (MAE {classical[best_c]:.3f})")
    print(f"  quantum - best_classical MAE: {delta:+.3f} degC "
          f"({'quantum better' if delta < 0 else 'classical better/equal'})")
    print(f"  quantum vs rbf paired sign test: quantum better on {wins}/{nnz} "
          f"non-tied days, two-sided p={p:.3f}")
    verdict = ("EDGE: quantum kernel significantly better" if (delta < 0 and p < 0.05)
               else "NO EDGE: quantum kernel is not a significant improvement "
                    "-> recommend-only / do not adopt")
    print(f"  VERDICT: {verdict}")


def main() -> None:
    qk._self_test()
    series = _load_series()
    for name, hs in series.items():
        if len(hs) < WINDOW + LAGS + 5:
            print(f"\n=== {name} ===\n  too few days ({len(hs)})")
            continue
        _backtest_city(name, hs)
    print("\n(reminder: recommend-only. This never edits the council, "
          "places a trade, or moves funds.)")


if __name__ == "__main__":
    main()
