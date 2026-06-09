"""Recency-weighted bias correction — a recommend-only accuracy/precision check.

What the council ships today
----------------------------
In the backtest blend (Council._blend_on_date) each member's bias is corrected by
the UNWEIGHTED mean of its forecast−observed errors over the whole training
window: `bias = mean(f - o for f, o in train_pairs)`. That is the right estimator
when the bias is stationary. But a station's daily-high bias drifts on a seasonal
timescale — at Hong Kong Observatory and London City Airport a June target sits
above the mean of a training window that still contains cooler spring days, so an
expanding/trailing mean lags and the forecast is dragged systematically cool.
That is exactly the bucket-verdict fingerprint we measured on HK high: misses with
NEGATIVE edge-fragility (gross cool errors, not boundary coin-flips).

What this module measures
-------------------------
The standard fix for a drifting bias is RECENCY weighting: weight recent training
pairs more, so the bias tracks the current regime. `recency_weighted_bias` does
this with a single exponential half-life (a day `h` old gets weight 0.5**(age/h)).
`evaluate` then scores, leak-free over the SAME walk-forward Council._validate
uses, an incumbent (mean-bias) prediction stream against a candidate
(recency-bias) stream — on BOTH the proper rule (CRPS, each dressed with its own
strictly-earlier residual cloud) AND the object the market settles on (the
whole-degree bucket hit-rate, via bucket_verdict).

Discipline (why this is signal, not noise)
------------------------------------------
  * Leak-free: every day is predicted from STRICTLY-earlier pairs only, and each
    day's CRPS uses only its own earlier residual cloud — identical walk-forward
    to Council._validate.
  * Proper rule + economic rule: a recency bias is recommended only when it beats
    the incumbent on CRPS past the standard error of the paired per-day
    difference (a real gain past the noise floor) AND does not lower the
    whole-degree bucket hit-rate. Sharper-but-wrong is punished by CRPS;
    right-distribution-wrong-bucket is caught by the bucket gate.
  * The non-obvious part this makes honest: the SERVED distribution dresses the
    point with an empirical residual cloud that HAS A MEAN, so it already absorbs
    any CONSTANT bias. A fixed-day lag under LINEAR drift is exactly a constant
    offset — recency cuts the point MAE but leaves CRPS and the bucket unchanged,
    so the gate (rightly) does NOT recommend it. Recency earns a recommend only
    for the CURVATURE the cloud cannot absorb (accelerating/regime drift). MAE is
    still reported, because the headline °C verdict is NOT re-centred by the cloud
    and a real point-accuracy gain matters to a reader even when the bucket holds.
  * No tuning leak: ONE a-priori half-life (RECENCY_HALFLIFE_DAYS) is tested, not
    a grid selected on the same data. A single principled choice that fails is an
    honest negative — it says the bias is not materially non-stationary here (or
    that seasonal.py already absorbs the out-of-season part).
  * Recommend-only: never changes the served bias. Emits a finding for human
    review, exactly like the conditional-spread and coverage checks.

Stdlib only (math, statistics, datetime); reuses scoring.crps_sample and
bucket_verdict.bucket_verdict_eval_grouped.
"""

from __future__ import annotations

__all__ = [
    'RecencyBiasEval', 'recency_weighted_bias', 'evaluate',
    'RECENCY_HALFLIFE_DAYS',
]

import datetime as dt
import math
import statistics
from dataclasses import dataclass

from .scoring import crps_sample
from .bucket_verdict import bucket_verdict_eval_grouped

# A-priori exponential half-life (days) for the recency bias. ~1 month: a day a
# month old gets half weight, two months a quarter — so the bias reflects roughly
# the current season while still pooling an effective ~2h/ln2 ≈ 86 weighted days.
# Chosen once, before seeing results; NOT tuned on the backtest (no grid).
RECENCY_HALFLIFE_DAYS = 30.0
# The incumbent residual cloud needs this many strictly-earlier days before a
# day's CRPS means anything — the same floor Council._validate uses.
CRPS_MIN_SAMPLES = 10
# Below this many paired CRPS days the improvement is too noisy to act on.
MIN_PAIRED = 30
# A recency bias is recommended only when its CRPS edge clears this many standard
# errors of the paired per-day difference — the same noise floor as calibration.
Z_THRESHOLD = 2.0


@dataclass(frozen=True)
class RecencyBiasEval:
    """Incumbent (mean-bias) vs candidate (recency-bias) over the leak-free
    walk-forward. Positive `crps_improvement` means the recency bias is the
    sharper-and-calibrated distribution (lower CRPS is better)."""
    halflife_days: float
    n_paired: int                 # held-out days scored on CRPS for both
    mae_incumbent: float
    mae_candidate: float
    crps_incumbent: float
    crps_candidate: float
    crps_improvement: float       # incumbent − candidate (>0 ⇒ candidate better)
    crps_se: float                # standard error of the paired per-day diff
    z: float                      # improvement / se (past the noise floor when ≥2)
    bucket_hit_incumbent: float   # pooled whole-degree bucket hit-rate, incumbent
    bucket_hit_candidate: float   # … candidate
    recommend: bool

    @property
    def improvement_pct(self) -> float:
        return (self.crps_improvement / self.crps_incumbent
                if self.crps_incumbent else 0.0)


def recency_weighted_bias(
    dated_errors: list[tuple[str, float]],
    target_day: str,
    halflife: float = RECENCY_HALFLIFE_DAYS,
) -> tuple[float, float]:
    """Exponentially recency-weighted (bias, weighted MAD-about-bias).

    `dated_errors` are (ISO date, forecast−observed) for STRICTLY-earlier training
    days; weight = 0.5 ** (age_in_days / halflife) relative to `target_day`. The
    MAD is the recency-weighted mean absolute deviation about the weighted bias —
    the consistent dispersion for the member's skill weight. With halflife → ∞ the
    weights flatten to the plain mean (the incumbent), so this strictly
    generalizes the current estimator."""
    t = dt.date.fromisoformat(target_day)
    w_sum = wb_sum = 0.0
    weights: list[float] = []
    errs: list[float] = []
    for diso, e in dated_errors:
        age = (t - dt.date.fromisoformat(diso)).days
        if age < 0:
            age = 0
        w = 0.5 ** (age / halflife)
        weights.append(w)
        errs.append(e)
        w_sum += w
        wb_sum += w * e
    if w_sum <= 0:
        m = statistics.mean(errs)
        return m, statistics.mean(abs(e - m) for e in errs)
    bias = wb_sum / w_sum
    mad = sum(w * abs(e - bias) for w, e in zip(weights, errs)) / w_sum
    return bias, mad


def _score_attr(
    triples: list[tuple[float, float, float]],
    inc_pairs: list[tuple[float, float]],
    cand_pairs: list[tuple[float, float]],
    paired: list[float],
    acc: dict,
) -> None:
    """Walk ONE attribute's ordered (incumbent_pred, candidate_pred, observed)
    stream. Builds each variant's expanding residual cloud leak-free and, once
    both clouds clear the floor, records the paired per-day CRPS difference. Folds
    MAE and the bucket-verdict (pred, obs) pairs into the shared structures."""
    prior_inc: list[float] = []
    prior_cand: list[float] = []
    for inc_pred, cand_pred, obs in triples:
        r_inc = obs - inc_pred
        r_cand = obs - cand_pred
        acc["mae_inc"].append(abs(r_inc))
        acc["mae_cand"].append(abs(r_cand))
        inc_pairs.append((inc_pred, obs))
        cand_pairs.append((cand_pred, obs))
        if len(prior_inc) >= CRPS_MIN_SAMPLES and len(prior_cand) >= CRPS_MIN_SAMPLES:
            c_inc = crps_sample(prior_inc, r_inc)
            c_cand = crps_sample(prior_cand, r_cand)
            acc["crps_inc"].append(c_inc)
            acc["crps_cand"].append(c_cand)
            paired.append(c_inc - c_cand)     # >0 ⇒ candidate sharper/calibrated
        prior_inc.append(r_inc)
        prior_cand.append(r_cand)


def evaluate(
    streams_by_attr: dict[str, list[tuple[float, float, float]]],
    *,
    halflife: float = RECENCY_HALFLIFE_DAYS,
) -> RecencyBiasEval | None:
    """Score incumbent vs recency-bias prediction streams and gate the result.

    `streams_by_attr` maps an attribute ("high"/"low") to its ordered list of
    (incumbent_pred, candidate_pred, observed) for the walk-forward. Each
    attribute is scored against its OWN residual cloud and its OWN bucket ladder
    (high and low are separate markets), then pooled. Returns None below the
    paired-day floor."""
    acc = {k: [] for k in ("mae_inc", "mae_cand", "crps_inc", "crps_cand")}
    paired: list[float] = []
    inc_streams: list[list[tuple[float, float]]] = []
    cand_streams: list[list[tuple[float, float]]] = []
    for triples in streams_by_attr.values():
        inc_pairs: list[tuple[float, float]] = []
        cand_pairs: list[tuple[float, float]] = []
        _score_attr(triples, inc_pairs, cand_pairs, paired, acc)
        inc_streams.append(inc_pairs)
        cand_streams.append(cand_pairs)
    if len(paired) < MIN_PAIRED:
        return None

    crps_inc = statistics.mean(acc["crps_inc"])
    crps_cand = statistics.mean(acc["crps_cand"])
    improvement = statistics.mean(paired)            # incumbent − candidate
    se = (statistics.pstdev(paired) / math.sqrt(len(paired))) if len(paired) > 1 else 0.0
    z = (improvement / se) if se > 0 else 0.0

    bv_inc = bucket_verdict_eval_grouped(inc_streams)
    bv_cand = bucket_verdict_eval_grouped(cand_streams)
    hit_inc = bv_inc.hit_rate if bv_inc else 0.0
    hit_cand = bv_cand.hit_rate if bv_cand else 0.0

    # Recommend only when the recency bias is the better DISTRIBUTION past the
    # noise floor AND does not cost whole-degree bucket accuracy (the settled
    # object). Both gates must pass; either alone is not enough.
    recommend = (z >= Z_THRESHOLD) and (improvement > 0) and (hit_cand >= hit_inc)
    return RecencyBiasEval(
        halflife_days=halflife,
        n_paired=len(paired),
        mae_incumbent=statistics.mean(acc["mae_inc"]),
        mae_candidate=statistics.mean(acc["mae_cand"]),
        crps_incumbent=crps_inc,
        crps_candidate=crps_cand,
        crps_improvement=improvement,
        crps_se=se,
        z=z,
        bucket_hit_incumbent=hit_inc,
        bucket_hit_candidate=hit_cand,
        recommend=recommend,
    )


def _self_test() -> None:
    """Deterministic oracles: the recency bias tracks a drifting bias and reduces
    to the plain mean when flat; the gate recommends on real drift and declines on
    stationary noise."""
    import random

    # 1) recency_weighted_bias reduces to the plain mean when the errors are flat
    #    (every error equal => weighting cannot matter).
    flat = [(f"2026-01-{d:02d}", 0.5) for d in range(1, 21)]
    b, mad = recency_weighted_bias(flat, "2026-02-01", halflife=30)
    assert abs(b - 0.5) < 1e-9 and mad < 1e-9, (b, mad)

    # 2) On a LINEARLY drifting error the recency bias sits ABOVE the plain mean
    #    (it weights the recent, larger errors more) — i.e. it tracks the drift.
    drift = [(f"2026-03-{d:02d}", 0.1 * d) for d in range(1, 28)]   # error grows
    b_rec, _ = recency_weighted_bias(drift, "2026-03-28", halflife=10)
    plain = statistics.mean(e for _, e in drift)
    assert b_rec > plain + 0.2, (b_rec, plain)

    # 3) The subtle truth: under LINEAR drift a fixed-day lag is a CONSTANT value
    #    offset, which the empirical residual cloud (it has a mean) ABSORBS — so
    #    CRPS and bucket-hit are unchanged even though point MAE drops sharply.
    #    The served distribution only benefits from a constant bias via the cloud,
    #    not via recency; the gate must NOT recommend here despite the MAE gain.
    rng = random.Random(7)
    triples = []
    for i in range(400):
        truth = 20.0 + 0.01 * i + rng.gauss(0.0, 0.3)   # linearly warming truth
        inc_pred = 20.0 + 0.01 * (i - 40)               # constant 0.4 lag
        cand_pred = 20.0 + 0.01 * i                     # tracks
        triples.append((inc_pred, cand_pred, truth))
    ev = evaluate({"high": triples})
    assert ev is not None
    assert ev.mae_candidate < ev.mae_incumbent - 0.1, ev   # accuracy clearly up
    assert abs(ev.crps_improvement) < 1e-3, ev             # cloud absorbs the offset
    assert not ev.recommend, ev                            # served dist unchanged

    # 3b) The case recency GENUINELY helps the served distribution: CURVED
    #     (accelerating) drift. A fixed-day lag now leaves a GROWING value gap the
    #     residual cloud under-absorbs, so the incumbent residuals fatten and CRPS
    #     worsens; the candidate that tracks the curve wins past the noise floor.
    rng = random.Random(17)
    triples = []
    for i in range(400):
        curve = 0.0003 * i * i
        truth = 20.0 + curve + rng.gauss(0.0, 0.3)
        inc_pred = 20.0 + 0.0003 * (i - 30) ** 2          # lag gap grows with i
        cand_pred = 20.0 + curve
        triples.append((inc_pred, cand_pred, truth))
    ev = evaluate({"high": triples})
    assert ev is not None
    assert ev.crps_improvement > 0 and ev.z >= Z_THRESHOLD, ev
    assert ev.mae_candidate < ev.mae_incumbent, ev
    assert ev.recommend, ev

    # 4) Gate DECLINES on stationary noise where the two streams are equivalent in
    #    expectation (no real edge to find).
    rng = random.Random(8)
    triples = []
    for _ in range(400):
        truth = 18.0 + rng.gauss(0.0, 1.0)
        triples.append((truth + rng.gauss(0, 0.5), truth + rng.gauss(0, 0.5), truth))
    ev = evaluate({"high": triples})
    assert ev is not None
    assert not ev.recommend, ev

    # 5) Thin sample => None.
    assert evaluate({"high": [(20.0, 20.0, 20.0)] * 20}) is None

    print("recency_bias self-test PASSED "
          "(flat→mean; drift tracked; recommends on real drift; declines on noise; "
          "thin=None)")


if __name__ == "__main__":
    _self_test()
