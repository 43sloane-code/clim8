"""Empirical-Bayes shrinkage of the seasonal-analog bias — a recommend-only layer.

What the council ships today
----------------------------
Out of season (Meteostat archive lag > council.SEASON_MATCH_DAYS), the council
replaces each member's trailing-window bias with one re-learned from same-day-of-
year analog days across prior years (weather_council/seasonal.py), and subtracts
it **at full strength**:  corrected = raw − bias_analog.  The only guard is a hard
floor of seasonal.MIN_ANALOG_SAMPLES (15) paired analog days before a bias is
asserted at all.

The problem this addresses
--------------------------
Fifteen-to-forty daily-temperature errors estimate a member's seasonal bias with
a standard error of roughly  σ/√n ≈ 1.5/√20 ≈ 0.34 °C.  A measured analog bias of,
say, +0.5 °C then carries a t-statistic near 1.5 — not distinguishable from zero —
yet the live code subtracts the whole 0.5 °C.  Hong Kong is the worked case from
this project's own logs: the open *forecast* archive only begins in 2022, and a
±21-day day-of-year window leaves few June days per year, so several members'
analog samples are thin and their full-strength bias swap injects sampling noise
into the blend.  That is a concrete mechanism for an out-of-season verdict reading
hotter or colder than the data actually supports.

What this module does
---------------------
The textbook empirical-Bayes / James–Stein move (Efron & Morris 1975): shrink each
member's noisily-estimated analog bias toward the panel's precision-weighted mean
bias, by the fraction of the between-member spread that is real signal rather than
sampling noise (method of moments: τ̂² = max(0, S²_between − mean SE²)).  A member
whose bias is tightly estimated (large n, small SE) keeps it; a member whose bias
is mostly noise is pulled toward the panel consensus.  Every quantity is derived
from real forecast−observed pairs — nothing is invented.

Discipline (why this is signal, not theatre)
--------------------------------------------
  * Leak-free: the head-to-head backtest (`analog_shrink_eval`) is leave-one-year-
    out over the analog years, so a held-out year's bias is never estimated from
    itself.
  * Same blend: the inverse-variance member weights are held FIXED across the
    full-swap and shrunk variants, so the comparison isolates the *bias treatment*,
    not the weighting.
  * Gated: shrinkage is RECOMMENDED only when it beats the full-strength swap on
    held-out *blended* MAE by more than MIN_IMPROVEMENT_C °C AND a seeded paired
    bootstrap CI of the per-day improvement excludes zero.  A statistically valid
    estimator can still fail to move the *blended* error — and here it does (below).
  * Recommend-only: this NEVER changes the served verdict.  It emits a finding for
    human review, exactly like calibration.py and the daily health check.

Critical verdict (be your own harshest reviewer)
------------------------------------------------
This layer was built, backtested leave-one-year-out, and then HELD — on purpose,
because that is what the evidence says.  Two structural reasons, both reproduced by
the self-test and the real-data harness (tools/analog_shrink_backtest.py):

  1. Redundancy with the blend.  The council never serves a single member; it serves
     an inverse-variance blend.  Shrinking each member's bias toward the panel mean m
     only shifts the blend by  −Σwᵢ(1−λᵢ)(biasᵢ−m)/Σwᵢ , and the blend is itself ≈ a
     weighted mean, so those deviations very nearly cancel.  Worse, the inverse-MAE
     weight and the bias-estimate precision are POSITIVELY coupled: a member with tight
     residuals earns both high weight AND a well-measured bias (λ→1, barely shrunk), so
     the members pooling would correct most are exactly the ones the blend already
     trusts least.  Member-level James–Stein provably cuts bias-MSE (self-test 1a); the
     blend renders that moot.  REAL-DATA leave-one-year-out (London EGLC + Hong Kong
     HKO, 172 held-out analog days/4 yr, tools/analog_shrink_backtest.py): pooled vs
     full-swap blended MAE moved |Δ| ≤ 0.001 °C in all four city×variable cells, with
     mean λ 0.93–0.99 — the estimator barely shrinks because the members' analog biases
     are genuinely well-separated (HK spans −0.47 to −2.35 °C) relative to their SEs, so
     it correctly reads that spread as signal.  Two orders of magnitude under the floor.
     (Note: the full swap itself is NOT idle — it cuts HK high MAE 1.271 → 0.775; it is
     the shrinkage *refinement on top* that the blend renders redundant.)
  2. Wrong tool for the real risk.  The genuine out-of-season failure mode is a
     COMMON-MODE analog bias — every member's 2022-2025 analog window skewed the same
     way against the held-out year (a warming trend, a station move).  The blend does
     NOT average a common-mode error away, but neither can pooling: it shrinks members
     toward their shared-wrong mean, so it leaves a common bias untouched.  The lever
     for that is recency/trend weighting or a wider archive — a different change.

So the honest recommendation is HOLD: do not add this estimator to the served path.
It ships as a reusable, self-testing DIAGNOSTIC that re-runs the comparison on fresh
data and will surface a CONSIDER only if reality ever contradicts the above.

Stdlib only (math, statistics).  Run `python3 -m weather_council.analog_shrink`
for the seeded known-answer self-test.
"""

from __future__ import annotations

__all__ = [
    'AnalogShrinkEval', 'bias_standard_error', 'shrink_to_zero',
    'pool_member_biases', 'analog_shrink_eval',
]

import math
import random
import statistics
from dataclasses import dataclass

# σ ≈ MAD_TO_SD × mean-absolute-deviation-around-the-mean, for a normal sample.
# Used only on the live-wiring convenience path, where a member exposes Skill
# (bias, mae_corrected, n) but not its raw residuals; the backtest below uses the
# exact sample SD instead.
MAD_TO_SD = math.sqrt(math.pi / 2.0)        # ≈ 1.2533

# Mirror the floors the rest of the project already uses, so this layer can never
# disagree with them on when a claim is too thin to make.
MIN_ANALOG = 15           # paired analog days before a member's bias is usable
MIN_MEMBERS = 2           # members that must contribute before pooling means anything
MIN_HELDOUT_DAYS = 20     # scored held-out days before any recommendation (≈ calibration.MIN_SCORED)
MIN_IMPROVEMENT_C = 0.03  # °C the challenger must beat the full swap by (daily_healthcheck noise floor)

# Seeded paired bootstrap over the per-day improvement — reproducible verdict.
BOOT_ITERS = 2000
BOOT_CI = 0.90
BOOT_SEED = 20260608


def bias_standard_error(mae_corrected: float, n: int) -> float:
    """Standard error of a member's mean bias, approximated from the Skill fields
    the council already computes (mae_corrected = mean|residual − bias|, n pairs).
    For a roughly-normal residual sample σ ≈ MAD_TO_SD·mae_corrected, so
    SE(mean) ≈ MAD_TO_SD·mae_corrected/√n.  Returns +inf for n < 1 so a member with
    no history shrinks completely."""
    if n < 1 or mae_corrected < 0:
        return float("inf")
    return MAD_TO_SD * mae_corrected / math.sqrt(n)


def shrink_to_zero(bias: float, se: float) -> tuple[float, float]:
    """Positive-part James–Stein shrinkage of a single bias estimate toward 0:
    λ = max(0, 1 − SE²/bias²), returning (λ·bias, λ).  A bias indistinguishable
    from its own sampling noise (|bias| ≲ SE) collapses to ~0; a sharply-estimated
    bias is kept.  Offered as the panel-mean→0 degenerate of the pooled estimator
    for the backtest's ablation; `pool_member_biases` is the principled multi-
    member version and should be preferred when ≥2 members contribute."""
    if bias == 0.0:
        return 0.0, 0.0
    se2 = max(se * se, 0.0)
    lam = max(0.0, 1.0 - se2 / (bias * bias))
    return bias * lam, lam


def _precision_weighted_mean(biases: list[float], ses: list[float]) -> float:
    """Inverse-variance (precision) weighted mean — the minimum-variance pooled
    estimate of the shared panel bias.  Falls back to the plain mean if every SE
    is degenerate."""
    w = [1.0 / (s * s) for s in ses if s > 0 and math.isfinite(s)]
    if len(w) != len(biases) or not w:
        finite = [b for b in biases]
        return statistics.mean(finite) if finite else 0.0
    wsum = sum(w)
    return sum(wi * b for wi, b in zip(w, biases)) / wsum if wsum > 0 else statistics.mean(biases)


def pool_member_biases(items: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Efron–Morris empirical-Bayes shrinkage of per-member analog biases toward
    the panel's precision-weighted mean.

    `items` is [(bias, se), ...] in member order.  Returns [(pooled_bias, λ), ...]
    in the SAME order, where λ ∈ [0, 1] is the shrinkage weight (1 = keep the
    member's own bias, 0 = fully adopt the panel mean).  The between-member
    variance is de-biased by the mean within-member sampling variance
    (τ̂² = max(0, S²_between − mean SE²)); when the members' biases differ by no
    more than their own noise, τ̂² → 0 and every member collapses to the panel
    consensus.  With fewer than MIN_MEMBERS members there is nothing to borrow
    strength from, so the original biases are returned unshrunk (λ = 1)."""
    K = len(items)
    if K < MIN_MEMBERS:
        return [(b, 1.0) for b, _ in items]
    biases = [b for b, _ in items]
    ses = [max(se, 1e-9) for _, se in items]
    m = _precision_weighted_mean(biases, ses)
    mean_se2 = sum(s * s for s in ses) / K
    var_between = sum((b - m) ** 2 for b in biases) / (K - 1)
    tau2 = max(0.0, var_between - mean_se2)
    out: list[tuple[float, float]] = []
    for b, s in zip(biases, ses):
        denom = tau2 + s * s
        lam = (tau2 / denom) if denom > 0 else 0.0
        out.append((m + lam * (b - m), lam))
    return out


@dataclass(frozen=True)
class AnalogShrinkEval:
    """Leave-one-year-out head-to-head of the seasonal-analog bias treatments on
    held-out blended MAE, with the statistics behind the recommend/decline gate.
    All MAEs are in °C; `improvement_pooled` etc. are full-swap MAE minus the
    challenger's MAE (positive = challenger better)."""
    n_days: int
    n_years: int
    mae_none: float           # subtract nothing (raw forecast blend) — the floor
    mae_full: float           # full-strength analog swap — the INCUMBENT live behavior
    mae_shrink0: float        # per-member shrink toward 0
    mae_pooled: float         # empirical-Bayes pool toward the panel mean (the proposal)
    improvement_pooled: float       # mae_full − mae_pooled
    improvement_pooled_pct: float
    boot_lo: float            # paired-bootstrap CI of the per-day pooled improvement
    boot_hi: float
    mean_lambda: float        # mean pooling λ across folds/members (1 = no shrink)
    recommend: bool

    def summary(self) -> str:
        verb = "RECOMMEND pooling" if self.recommend else "HOLD full-swap"
        return (
            f"analog-bias shrinkage (LOYO, n={self.n_days}d/{self.n_years}y): "
            f"MAE full {self.mae_full:.3f} → pooled {self.mae_pooled:.3f} "
            f"({self.improvement_pooled:+.3f} °C, {self.improvement_pooled_pct * 100:+.1f}%, "
            f"90% CI [{self.boot_lo:+.3f}, {self.boot_hi:+.3f}], mean λ {self.mean_lambda:.2f}; "
            f"none {self.mae_none:.3f}, shrink0 {self.mae_shrink0:.3f}) -> {verb}"
        )


def _paired_bootstrap_ci(deltas: list[float], *, iters: int = BOOT_ITERS,
                         ci: float = BOOT_CI, seed: int = BOOT_SEED) -> tuple[float, float]:
    """Seeded paired bootstrap CI for the MEAN of per-day improvements.  Resamples
    days with replacement; reproducible given the seed (so the verdict is stable)."""
    n = len(deltas)
    if n == 0:
        return 0.0, 0.0
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(iters):
        s = 0.0
        for _ in range(n):
            s += deltas[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    lo_q = (1.0 - ci) / 2.0
    hi_q = 1.0 - lo_q
    lo = means[max(0, int(lo_q * iters) - 1)]
    hi = means[min(iters - 1, int(hi_q * iters))]
    return lo, hi


def _decide(improvement: float, boot_lo: float, min_improvement: float) -> bool:
    """The recommend gate as a pure function (so it can be unit-tested in isolation):
    the challenger is recommended only when it beats the incumbent by at least the
    practical floor AND the seeded paired-bootstrap lower bound on the per-day
    improvement is strictly positive — i.e. the CI excludes zero.  Both the practical
    and the statistical bar must clear; either alone is not enough."""
    return improvement >= min_improvement and boot_lo > 0.0


def analog_shrink_eval(
    member_pairs: dict[str, list[tuple[str, float, float]]],
    *,
    weight_power: int | None = None,
    min_analog: int = MIN_ANALOG,
    min_heldout: int = MIN_HELDOUT_DAYS,
    min_improvement: float = MIN_IMPROVEMENT_C,
) -> AnalogShrinkEval | None:
    """Leave-one-year-out backtest of the seasonal-analog bias treatments.

    `member_pairs` maps member_id -> [(date_iso, forecast, observed), ...], already
    filtered to the analog day-of-year window for ONE variable (high or low) of one
    city.  The observed value is the shared truth, so it agrees across members for a
    given date.

    For each held-out year Y:
      * each member's bias / SE / inverse-variance weight is learned from its analog
        pairs in years ≠ Y only (≥ `min_analog` pairs, else the member sits out the
        fold) — leak-free;
      * four treatments are formed with the SAME weights: NONE (subtract nothing),
        FULL (subtract the member's own bias — the live incumbent), SHRINK0
        (per-member shrink toward 0), POOLED (empirical-Bayes pool toward the panel);
      * every analog day in year Y is blended under each treatment and its absolute
        error recorded, paired across treatments.

    Returns None when fewer than `min_heldout` held-out days can be scored or fewer
    than two analog years exist.  `recommend` is True only when POOLED beats FULL by
    ≥ `min_improvement` °C on blended MAE AND the paired-bootstrap CI of the per-day
    improvement excludes zero.  Recommend-only — it changes no served value."""
    if weight_power is None:                # resolve the live exponent lazily to keep
        from .council import WEIGHT_POWER   # this module importable without the app graph
        weight_power = WEIGHT_POWER

    years = sorted({d[:4] for pairs in member_pairs.values() for (d, _, _) in pairs})
    if len(years) < 2:
        return None

    err = {k: [] for k in ("none", "full", "shrink0", "pooled")}
    lambdas: list[float] = []

    for Y in years:
        trained: dict[str, dict] = {}
        for mid, pairs in member_pairs.items():
            tr = [(f, o) for (d, f, o) in pairs if d[:4] != Y]
            if len(tr) < min_analog:
                continue
            diffs = [f - o for f, o in tr]
            bias = statistics.mean(diffs)
            mae_c = statistics.mean(abs(x - bias) for x in diffs)
            sd = statistics.pstdev(diffs) if len(diffs) > 1 else 0.0
            se = sd / math.sqrt(len(diffs)) if diffs else float("inf")
            trained[mid] = {
                "bias": bias,
                "se": se,
                "w": 1.0 / max(mae_c, 0.1) ** weight_power,
            }
        if len(trained) < MIN_MEMBERS:
            continue

        mids = list(trained.keys())
        pooled = pool_member_biases([(trained[m]["bias"], trained[m]["se"]) for m in mids])
        for m, (pb, lam) in zip(mids, pooled):
            trained[m]["pooled"] = pb
            trained[m]["shrink0"] = shrink_to_zero(trained[m]["bias"], trained[m]["se"])[0]
            lambdas.append(lam)

        held_days = sorted({d for m in mids for (d, _, _) in member_pairs[m] if d[:4] == Y})
        for day in held_days:
            acc = {k: [0.0, 0.0] for k in err}     # treatment -> [weighted_sum, weight_sum]
            obs_val = None
            for m in mids:
                fo = next(((f, o) for (d, f, o) in member_pairs[m] if d == day), None)
                if fo is None:
                    continue
                f, o = fo
                obs_val = o
                w = trained[m]["w"]
                preds = {
                    "none": f,
                    "full": f - trained[m]["bias"],
                    "shrink0": f - trained[m]["shrink0"],
                    "pooled": f - trained[m]["pooled"],
                }
                for k, p in preds.items():
                    acc[k][0] += w * p
                    acc[k][1] += w
            if obs_val is None or acc["full"][1] <= 0:
                continue
            for k in err:
                num, den = acc[k]
                if den > 0:
                    err[k].append(abs(num / den - obs_val))

    n = len(err["full"])
    if n < min_heldout:
        return None

    mae = {k: statistics.mean(err[k]) for k in err}
    d_pool = [ef - ep for ef, ep in zip(err["full"], err["pooled"])]   # >0 = pooled better
    imp = statistics.mean(d_pool)
    lo, hi = _paired_bootstrap_ci(d_pool)
    recommend = _decide(imp, lo, min_improvement)

    return AnalogShrinkEval(
        n_days=n,
        n_years=len(years),
        mae_none=round(mae["none"], 4),
        mae_full=round(mae["full"], 4),
        mae_shrink0=round(mae["shrink0"], 4),
        mae_pooled=round(mae["pooled"], 4),
        improvement_pooled=round(imp, 4),
        improvement_pooled_pct=round(imp / mae["full"], 4) if mae["full"] > 0 else 0.0,
        boot_lo=round(lo, 4),
        boot_hi=round(hi, 4),
        mean_lambda=round(statistics.mean(lambdas), 3) if lambdas else 1.0,
        recommend=recommend,
    )


# --------------------------------------------------------------------------- #
#  Seeded known-answer self-test — the primary correctness oracle (offline).
# --------------------------------------------------------------------------- #
def _self_test() -> None:
    """Prove the estimator does what it claims, on synthetic data with a KNOWN truth.

      1. pool_member_biases (the estimator math):
         (a) when members share a bias and their per-member spread is mostly sampling
             noise, pooling shrinks hard and recovers the truth with LOWER mean-squared
             error than the raw per-member estimates — this is WHY the idea is tempting;
         (b) when members genuinely differ and are well estimated, pooling barely
             shrinks and does no harm (λ→1).
      2. _decide (the gate, as a pure function): fires only when BOTH the practical
         floor and the strictly-positive bootstrap lower bound clear.
      3. analog_shrink_eval (the end-to-end honest verdict): on a realistic thin-sample
         analog panel the inverse-variance blend ALREADY damps idiosyncratic per-member
         bias noise, so pooling moves the *blended* MAE by less than the noise floor and
         the harness correctly says HOLD — no false positive.  (This is the critical
         finding, not a bug: see the module docstring's coupling note.  Member-level
         shrinkage helps; the council never serves a single member, so it does not.)
      4. too little data -> None (never act on a thin sample).
    """
    # 1a. Noisy-shared regime: pooling must shrink and beat the raw estimates. ----
    rng = random.Random(1)
    mse_raw = mse_pooled = 0.0
    lam_sum = 0.0
    trials = 400
    for _ in range(trials):
        true_panel = -1.8
        true = [true_panel + rng.gauss(0.0, 0.15) for _ in range(8)]   # members nearly share it
        items, truths = [], []
        for tb in true:
            n = rng.randint(15, 30)
            se = 1.5 / math.sqrt(n)                       # thin-sample SE ≈ 0.27–0.39 °C
            b_hat = tb + rng.gauss(0.0, se)               # noisy per-member estimate
            items.append((b_hat, se))
            truths.append(tb)
        pooled = pool_member_biases(items)
        for (b_hat, _), (pb, lam), tb in zip(items, pooled, truths):
            mse_raw += (b_hat - tb) ** 2
            mse_pooled += (pb - tb) ** 2
            lam_sum += lam
    assert mse_pooled < mse_raw, f"pooling should cut MSE on shared/noisy biases: {mse_pooled:.3f} !< {mse_raw:.3f}"
    mean_lam = lam_sum / (trials * 8)
    assert mean_lam < 0.6, f"should shrink hard when spread is mostly noise: mean λ {mean_lam:.2f}"

    # 1b. Well-separated regime: pooling must NOT over-shrink (λ→1, no harm). ------
    rng = random.Random(2)
    mse_raw = mse_pooled = lam_sum = 0.0
    for _ in range(trials):
        true = [rng.gauss(0.0, 2.0) for _ in range(8)]    # genuinely different biases
        items, truths = [], []
        for tb in true:
            n = 400
            se = 1.5 / math.sqrt(n)                        # tightly estimated (SE ≈ 0.075)
            items.append((tb + rng.gauss(0.0, se), se))
            truths.append(tb)
        pooled = pool_member_biases(items)
        for (b_hat, _), (pb, lam), tb in zip(items, pooled, truths):
            mse_raw += (b_hat - tb) ** 2
            mse_pooled += (pb - tb) ** 2
            lam_sum += lam
    mean_lam = lam_sum / (trials * 8)
    assert mean_lam > 0.85, f"should barely shrink well-separated biases: mean λ {mean_lam:.2f}"
    assert mse_pooled <= mse_raw * 1.25, f"pooling must not hurt well-separated biases: {mse_pooled:.3f} vs {mse_raw:.3f}"

    # 2. The gate as a pure function: BOTH bars must clear. ----------------------
    assert _decide(0.10, 0.02, 0.03) is True            # above floor AND CI>0  -> fire
    assert _decide(0.10, -0.01, 0.03) is False           # CI includes 0         -> hold
    assert _decide(0.01, 0.005, 0.03) is False           # below practical floor -> hold
    assert _decide(0.03, 0.0, 0.03) is False             # CI touches 0 (not strict) -> hold

    # 3. End-to-end HONEST verdict on a realistic analog panel.  Members share a
    #    ~−1.0 °C bias on a thin (~18-day) analog window, so each per-fold estimate is
    #    noisy.  At the MEMBER level pooling would help (proved in 1a); but the
    #    inverse-variance blend already averages that noise away, so the *blended* MAE
    #    barely moves and the gate correctly HOLDS.  This is the headline finding. -----
    def _panel(rng_seed, true_biases, day_noise, n_per_year, years):
        rng = random.Random(rng_seed)
        mp: dict[str, list[tuple[str, float, float]]] = {}
        for mi, tb in enumerate(true_biases):
            rows = []
            for y in years:
                for k in range(n_per_year):
                    o = 28.0 + rng.gauss(0.0, 3.0)            # the day's true high
                    f = o + tb + rng.gauss(0.0, day_noise)   # member forecast
                    rows.append((f"{y}-06-{1 + k:02d}", f, o))
            mp[f"m{mi}"] = rows
        return mp

    years = ["2022", "2023", "2024", "2025"]
    realistic = _panel(7, [-1.0, -1.0, -1.0], day_noise=1.6, n_per_year=18, years=years)
    ev = analog_shrink_eval(realistic, weight_power=2)
    assert ev is not None and ev.n_days >= MIN_HELDOUT_DAYS, "should score a panel this size"
    assert not ev.recommend, f"blend already damps per-member noise; must HOLD: {ev.summary()}"
    assert abs(ev.improvement_pooled) < MIN_IMPROVEMENT_C, (
        f"pooling must not move blended MAE past the floor (coupling): {ev.summary()}")

    # Sanity: a wider, well-separated panel also HOLDS (λ→1, nothing to borrow). ----
    sep = _panel(9, [-2.5, 0.0, 2.5], day_noise=0.4, n_per_year=60, years=years)
    ev2 = analog_shrink_eval(sep, weight_power=2)
    assert ev2 is not None and not ev2.recommend, f"distinct well-measured biases must HOLD: {ev2.summary()}"

    # 4. Too little data -> no verdict at all (never act on a thin sample). ---------
    thin = {"m0": [("2022-06-01", 1.0, 0.0)], "m1": [("2022-06-01", 1.0, 0.0)]}
    assert analog_shrink_eval(thin, weight_power=2) is None

    print("analog_shrink self-test PASSED")
    print("  estimator    : pooling cut member bias-MSE (mean λ<0.6 shared/noisy, >0.85 separated)")
    print("  gate logic   : _decide fires iff improvement>=floor AND bootstrap CI>0")
    print(f"  blend verdict: {ev.summary()}")
    print("                 -> the inverse-variance blend already damps per-member bias noise;")
    print("                    member-level shrinkage does not survive the blend (HOLD).")


if __name__ == "__main__":
    _self_test()
