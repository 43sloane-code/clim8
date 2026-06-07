"""Systematic, mathematically-defined, testable verdict — run at any timescale.

Design contract (no human judgement enters the verdict)
-------------------------------------------------------
Given an evenly-aggregated truth series y_1..y_n at some timescale tau, the
verdict is computed by a FIXED rule with no tunable knobs:

  Forecaster   F:  persistence,  yhat_t = y_{t-1}
  Reference    R:  expanding climatology,  r_t = mean(y_1..y_{t-1})
  Loss            absolute error;  e^F_t=|y_t-yhat_t|,  e^R_t=|y_t-r_t|
  Skill score  SS = 1 - MAE_F / MAE_R          (>0  iff F beats climatology)
  Loss diff    d_t = e^F_t - e^R_t             (mean<0 iff F better)
  DM statistic  Diebold-Mariano with a Newey-West (Bartlett) HAC variance,
                lag L = floor(n^{1/3}); DM = dbar / sqrt(HACvar/n) ~ N(0,1).
  p             two-sided normal tail, erfc(|DM|/sqrt2)  (no scipy)

  VERDICT (a pure function of the numbers, with fixed alpha = 0.05):
    UNOBSERVABLE         - the timescale is finer than the data's cadence
    INSUFFICIENT         - fewer than MIN_PERIODS aggregated periods
    SKILL CONFIRMED      - SS>0 and dbar<0 and p<alpha
    ANTI-SKILL           - SS<0 and p<alpha   (F significantly WORSE than clim.)
    ABSTAIN              - otherwise (no significant difference)

Why these choices are honest at every timescale: F and R are both parameter-free,
so a verdict can never be reverse-engineered to look good; the HAC variance keeps
the significance test valid under the serial correlation that aggregated weather
always has; and UNOBSERVABLE is decided by the data's own measured cadence, so a
"per-second" verdict is refused rather than fabricated. Self-tested by a
known-answer pair: a skillful AR(1) series must read SKILL CONFIRMED, white noise
must NOT (persistence cannot beat climatology on i.i.d. data).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

MIN_PERIODS = 24
ALPHA = 0.05


@dataclass
class Verdict:
    timescale: str
    n: int
    mae_f: float
    mae_r: float
    skill: float
    dm: float
    p: float
    observability: float
    verdict: str

    def line(self) -> str:
        return (f"  {self.timescale:<9}{self.n:>7}{self.mae_f:>10.3f}"
                f"{self.mae_r:>10.3f}{self.skill:>+9.3f}{self.dm:>+8.2f}"
                f"{self.p:>8.3f}{self.observability:>8.2f}   {self.verdict}")


# --------------------------------------------------------------------------- #
#  Aggregation                                                                  #
# --------------------------------------------------------------------------- #
def resample(points: list[tuple[float, float]],
             period_s: float) -> tuple[list[float], float]:
    """Bin (epoch_seconds, value) points into fixed windows of `period_s` and
    return (per-bin means in time order, observability). Observability =
    filled_bins / spanned_bins in [0,1]: 1.0 means every period in the span has a
    reading; a tiny value means the timescale is finer than the data's cadence."""
    if not points:
        return [], 0.0
    bins: dict[int, list[float]] = {}
    for t, v in points:
        bins.setdefault(int(t // period_s), []).append(v)
    keys = sorted(bins)
    span = keys[-1] - keys[0] + 1
    series = [sum(bins[k]) / len(bins[k]) for k in keys]
    observability = len(keys) / span if span > 0 else 0.0
    return series, observability


# --------------------------------------------------------------------------- #
#  Verdict math                                                                 #
# --------------------------------------------------------------------------- #
def _newey_west_var(d: list[float], lag: int) -> float:
    n = len(d)
    dbar = sum(d) / n
    dev = [x - dbar for x in d]
    gamma0 = sum(x * x for x in dev) / n
    s = gamma0
    for k in range(1, lag + 1):
        w = 1.0 - k / (lag + 1)
        gk = sum(dev[t] * dev[t - k] for t in range(k, n)) / n
        s += 2.0 * w * gk
    return max(s, 1e-12)


def diebold_mariano(e_f: list[float], e_r: list[float]) -> tuple[float, float]:
    """DM statistic and two-sided p for loss differential d=e_f-e_r, with a
    Newey-West HAC variance (lag floor(n^{1/3})). DM<0 => forecaster better."""
    n = len(e_f)
    d = [a - b for a, b in zip(e_f, e_r)]
    dbar = sum(d) / n
    lag = max(0, int(n ** (1.0 / 3.0)))
    var = _newey_west_var(d, lag) / n
    dm = dbar / math.sqrt(var) if var > 0 else 0.0
    p = math.erfc(abs(dm) / math.sqrt(2.0))      # two-sided N(0,1) tail
    return dm, p


def evaluate(series: list[float], timescale: str = "",
             observability: float = 1.0) -> Verdict:
    """Apply the fixed verdict rule to one aggregated series."""
    n_periods = len(series)
    if observability < 0.5:
        return Verdict(timescale, n_periods, float("nan"), float("nan"),
                       float("nan"), float("nan"), float("nan"),
                       observability, "UNOBSERVABLE")
    if n_periods < MIN_PERIODS:
        return Verdict(timescale, n_periods, float("nan"), float("nan"),
                       float("nan"), float("nan"), float("nan"),
                       observability, "INSUFFICIENT")
    e_f: list[float] = []     # persistence
    e_r: list[float] = []     # expanding climatology
    run_sum = series[0]
    for t in range(1, n_periods):
        clim = run_sum / t
        e_f.append(abs(series[t] - series[t - 1]))
        e_r.append(abs(series[t] - clim))
        run_sum += series[t]
    mae_f = sum(e_f) / len(e_f)
    mae_r = sum(e_r) / len(e_r)
    skill = 1.0 - mae_f / mae_r if mae_r > 0 else 0.0
    dm, p = diebold_mariano(e_f, e_r)
    if skill > 0 and dm < 0 and p < ALPHA:
        v = "SKILL CONFIRMED"
    elif skill < 0 and p < ALPHA:
        v = "ANTI-SKILL"
    else:
        v = "ABSTAIN"
    return Verdict(timescale, n_periods, mae_f, mae_r, skill, dm, p,
                   observability, v)


# --------------------------------------------------------------------------- #
#  Horizon-specialized cascade forecaster (literature-derived improvement)      #
# --------------------------------------------------------------------------- #
#  FuXi (Chen 2023) cascades lead-time-specialized models; Rasp (2021, via FuXi)
#  finds direct lead-time forecasts beat naive iteration; Aardvark (Allen 2025)
#  tunes end-to-end to the local station target. The single-station, stdlib
#  analogue of "specialize by horizon" is the optimal AR(1) shrinkage predictor
#     yhat_t = mu + rho * (y_{t-1} - mu),
#  where mu is the causal climatological mean and rho the causal lag-1
#  autocorrelation AT THAT TIMESCALE. rho->1 at short lead recovers persistence;
#  rho->0 at long lead recovers climatology. It is DERIVED (from the data's own
#  autocorrelation), not tuned, so it adds no free parameter to fit.
@dataclass
class CascadeVerdict:
    timescale: str
    n: int
    mae_persist: float
    mae_clim: float
    mae_cascade: float
    skill_vs_persist: float
    rho: float
    dm: float
    p: float
    observability: float
    verdict: str

    def line(self) -> str:
        return (f"  {self.timescale:<9}{self.n:>7}{self.mae_persist:>10.3f}"
                f"{self.mae_clim:>10.3f}{self.mae_cascade:>10.3f}"
                f"{self.skill_vs_persist:>+9.3f}{self.rho:>7.2f}"
                f"{self.p:>8.3f}   {self.verdict}")


def _autocorr1(xs: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mu = sum(xs) / n
    dev = [x - mu for x in xs]
    denom = sum(d * d for d in dev)
    if denom <= 1e-12:
        return 0.0
    num = sum(dev[t] * dev[t - 1] for t in range(1, n))
    rho = num / denom
    return max(-0.999, min(0.999, rho))


def evaluate_cascade(series: list[float], timescale: str = "",
                     observability: float = 1.0) -> CascadeVerdict:
    """Walk-forward backtest of the AR(1) shrinkage cascade vs pure persistence
    (and climatology), all CAUSAL: at each t, mu and rho are estimated only from
    the prefix y[0:t]. Verdict tests whether the cascade significantly improves on
    persistence (DM, HAC). This is the literature-derived 'specialize by horizon'
    improvement, measured the same honest way every mechanism is."""
    n = len(series)
    if observability < 0.5:
        return CascadeVerdict(timescale, n, *([float("nan")] * 7),
                              observability, "UNOBSERVABLE")
    warmup = MIN_PERIODS
    if n < warmup + MIN_PERIODS:
        return CascadeVerdict(timescale, n, *([float("nan")] * 7),
                              observability, "INSUFFICIENT")
    e_p: list[float] = []
    e_c: list[float] = []
    e_cas: list[float] = []
    rho_last = 0.0
    for t in range(warmup, n):
        prefix = series[:t]
        mu = sum(prefix) / t
        rho_last = _autocorr1(prefix)
        prev = series[t - 1]
        y = series[t]
        e_p.append(abs(y - prev))
        e_c.append(abs(y - mu))
        e_cas.append(abs(y - (mu + rho_last * (prev - mu))))
    m_p = sum(e_p) / len(e_p)
    m_c = sum(e_c) / len(e_c)
    m_cas = sum(e_cas) / len(e_cas)
    skill = 1.0 - m_cas / m_p if m_p > 0 else 0.0
    dm, p = diebold_mariano(e_cas, e_p)
    if skill > 0 and dm < 0 and p < ALPHA:
        v = "CASCADE BETTER"
    elif skill < 0 and p < ALPHA:
        v = "CASCADE WORSE"
    else:
        v = "TIE (no significant gain)"
    return CascadeVerdict(timescale, n - warmup, m_p, m_c, m_cas, skill,
                          rho_last, dm, p, observability, v)


# --------------------------------------------------------------------------- #
#  Known-answer self-test                                                       #
# --------------------------------------------------------------------------- #
def _self_test() -> None:
    rng = random.Random(0)
    # Skillful: AR(1) with phi=0.9 -> strong autocorrelation, persistence must win.
    ar = [0.0]
    for _ in range(600):
        ar.append(0.9 * ar[-1] + rng.gauss(0, 1))
    v_ar = evaluate(ar, "ar1")
    assert v_ar.verdict == "SKILL CONFIRMED", v_ar
    assert v_ar.skill > 0 and v_ar.p < ALPHA

    # White noise: i.i.d. -> persistence cannot beat climatology; must NOT confirm.
    noise = [rng.gauss(0, 1) for _ in range(600)]
    v_n = evaluate(noise, "noise")
    assert v_n.verdict != "SKILL CONFIRMED", v_n

    # Observability gate: a sub-cadence scale is refused, never scored.
    v_u = evaluate([1.0] * 50, "sub", observability=0.01)
    assert v_u.verdict == "UNOBSERVABLE", v_u

    # DM symmetry: identical forecasters -> dbar 0, p ~ 1.
    dm, p = diebold_mariano([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    assert abs(dm) < 1e-9 and p > 0.99

    # Cascade: on white noise the shrinkage predictor (rho~0 -> climatology) must
    # BEAT persistence; on a near-unit-root series (rho~1) it must not be WORSE
    # than persistence (it collapses to persistence).
    c_noise = evaluate_cascade(noise, "noise")
    assert c_noise.verdict == "CASCADE BETTER", c_noise
    unit = [0.0]
    for _ in range(600):
        unit.append(0.97 * unit[-1] + rng.gauss(0, 1))
    c_unit = evaluate_cascade(unit, "unit")
    assert c_unit.verdict != "CASCADE WORSE", c_unit

    print("timescale self-test PASSED "
          "(AR(1)=SKILL CONFIRMED; noise!=confirmed; sub-cadence=UNOBSERVABLE; "
          "DM symmetric; cascade beats persistence on noise, ties at unit-root)")


if __name__ == "__main__":
    _self_test()
