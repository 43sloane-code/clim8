"""Candidate 42 — a market-usable daily verdict CONTRACT + boundary guard.

Why this module exists
----------------------
The served verdict's headline is a point high/low plus a *word* — "Confidence
HIGH/MED/LOW" (run.py:1050, council.Verdict.confidence). A word is not the object
the market pays on, and it hides two failure modes the desk actually loses money
on:

  * **No probabilities.** The market settles on a whole-degree bucket; a usable
    verdict must state P(bucket) for the buckets in play, not an adjective.
  * **Silent boundary risk.** Under round-half-up the bucket edges are the
    HALF-integers (a high near 64.5 °F is a coin-flip between buckets 64 and 65).
    A central that lands a hair from such an edge is a coin-flip the word "HIGH
    confidence" actively misrepresents. (Note: the originating spec's "64.04 °F is
    just over the 63/64 boundary" example is itself wrong — 64.04 °F is the
    dead-CENTRE of bucket 64, edges at 63.5/64.5, so it is maximally robust. The
    geometry here follows the harness's own `_round_half_up` / market.edge_distance_c,
    not that example.)

This module converts a predictive (central °C, sigma °C) into the settlement
object — bucket probabilities by the closed-form normal CDF, mapped °C→°F (or the
native grain) and rounded by the SAME `sources._round_half_up` the council settles
with — and raises a hard `boundary_flag` when the central sits within
`BOUNDARY_GUARD_F` of a bucket edge (a half-integer). Banned from the output:
confidence adjectives. Probabilities only.

Discipline
----------
  * **Additive / recommend-only.** This never mutates the served Verdict or run.py;
    it is an emitter the desk (or a future promotion) can call. Mirrors the
    recommend-only pattern of the other diagnostics.
  * **Settlement-correct.** Buckets are integrated in the unit the market resolves
    on; the modal integer always equals `_round_half_up` of the central, so the
    contract can never disagree with what the council would settle.
  * **Closed-form, not Monte Carlo.** A 1-D bucket probability is an exact normal
    CDF difference; no sampling, no seed, bit-for-bit reproducible.
  * **No live tape in the repo.** `resolution_source`/price fields are populated
    only when a real WeatherMarket is supplied AND its rounding rule has been
    confirmed; otherwise they are explicitly marked UNVERIFIED, never faked.

Stdlib only (math). Reuses scoring._Phi (normal CDF) and sources._round_half_up.
"""

from __future__ import annotations

__all__ = [
    "BOUNDARY_GUARD_F",
    "bucket_probabilities",
    "boundary_distance",
    "compact_buckets",
    "daily_contract",
]

import math

from .scoring import _Phi          # Φ, the standard-normal CDF (math.erf based)
from .sources import _round_half_up

# Within this many settlement units of a bucket edge, the central is a coin-flip:
# raise the boundary guard. 0.3 °F ≈ 0.17 °C — about a third of a bucket width.
BOUNDARY_GUARD_F = 0.3


def _to_settlement(central_c: float, sigma_c: float, grain: str) -> tuple[float, float]:
    """Map a predictive (central, sigma) in °C into the unit the market settles on.
    For a whole-°F market the linear map scales sigma by 9/5; for a native-°C
    market it is the identity."""
    if grain == "F":
        return central_c * 9.0 / 5.0 + 32.0, sigma_c * 9.0 / 5.0
    if grain == "C":
        return central_c, sigma_c
    raise ValueError(f"grain must be 'F' or 'C', got {grain!r}")


def bucket_probabilities(central_c: float, sigma_c: float, *, grain: str = "F") -> dict[int, float]:
    """Settlement-bucket probabilities for a Gaussian predictive N(central, sigma²).

    Bucket integer k (in the settlement unit) collects the mass that rounds-half-up
    to k, i.e. the half-open interval [k − 0.5, k + 0.5). The probability is the
    exact normal-CDF difference Φ((k+0.5−μ)/σ) − Φ((k−0.5−μ)/σ); no Monte Carlo.
    A degenerate σ ≤ 0 returns a point mass on the rounded central.

    The returned dict spans μ ± ~6σ (mass < 1e-9 dropped) and is renormalised so
    the truncated tails do not leak probability — it sums to 1.0 by construction.
    """
    mu, sd = _to_settlement(central_c, sigma_c, grain)
    if sd <= 0.0:
        return {int(_round_half_up(mu)): 1.0}
    k_lo = int(math.floor(mu - 6.0 * sd - 0.5))
    k_hi = int(math.ceil(mu + 6.0 * sd + 0.5))
    probs: dict[int, float] = {}
    for k in range(k_lo, k_hi + 1):
        p = _Phi((k + 0.5 - mu) / sd) - _Phi((k - 0.5 - mu) / sd)
        if p > 1e-9:
            probs[k] = p
    total = sum(probs.values())
    return {k: p / total for k, p in probs.items()}


def boundary_distance(central_c: float, *, grain: str = "F") -> float:
    """Distance (in settlement units) from the central to the nearest bucket edge.

    With round-half-up the edges sit at the half-integers, so 0.0 means the central
    is exactly on a coin-flip boundary and 0.5 means it is dead-centre in a bucket.
    """
    mu, _ = _to_settlement(central_c, 0.0, grain)
    frac = (mu + 0.5) % 1.0
    return min(frac, 1.0 - frac)


def compact_buckets(probs: dict[int, float], *, tail_floor: float = 0.005) -> dict[str, float]:
    """Render a per-integer pmf as the compact market-facing cells the desk reads: the interior span
    [lo, hi] (lo/hi = the lowest/highest bucket carrying ≥ `tail_floor`) with the low/high tails folded
    into "<=lo-1" / ">=hi+1" aggregates.

    WP-6 (served-number campaign): the interior is emitted CONTIGUOUSLY over [lo, hi] — including any
    sub-floor bucket that falls between lo and hi — so the cells `<=lo-1 | {lo..hi} | >=hi+1` partition
    the integer line (each integer claimed exactly once) and NO interior mass is dropped. The old code
    emitted only the above-floor keys, silently dropping the mass of a sub-floor interior bucket, so the
    result did not sum to 1. A permanent runtime invariant now asserts mass preservation."""
    if not probs:
        return {}
    above_floor = sorted(k for k, p in probs.items() if p >= tail_floor)
    if not above_floor:                            # everything is tail — keep the mode as the interior
        above_floor = [max(probs, key=probs.get)]
    lo, hi = above_floor[0], above_floor[-1]
    out: dict[str, float] = {}
    below = sum(p for k, p in probs.items() if k < lo)
    above = sum(p for k, p in probs.items() if k > hi)
    if below > 0.0:
        out[f"<={lo - 1}"] = below
    for k in range(lo, hi + 1):                    # CONTIGUOUS interior — no interior mass dropped
        out[str(k)] = probs.get(k, 0.0)
    if above > 0.0:
        out[f">={hi + 1}"] = above
    # Partition invariant: below-tail | [lo..hi] | above-tail claims every integer exactly once (the
    # cells are disjoint by structure), so the compacted pmf preserves the input's total mass.
    assert abs(sum(out.values()) - sum(probs.values())) < 1e-9, (out, probs)
    return out


def daily_contract(
    station: str,
    date: str,
    central_c: float,
    sigma_c: float,
    *,
    grain: str = "F",
    lineage_weights: dict[str, float] | None = None,
    kalman_bias_f: float = 0.0,
    market=None,
    rounding_confirmed: bool = False,
    calibration_red: bool = False,
    calibration_reason: str | None = None,
) -> dict:
    """Emit the market-usable daily verdict contract (candidate 42 schema).

    `central_c`/`sigma_c` are the council's predictive in °C (sigma from the
    residual cloud the council already dresses with). `market`, when supplied, is a
    `weather_council.market.WeatherMarket`; its rounding-rule chain is quoted only
    when `rounding_confirmed=True`, otherwise the resolution source is marked
    UNVERIFIED (the repo carries no confirmed live tape by default).

    Two HARD gates can set `refusal` (the prediction layer treats a refusal as
    abstain-tier; there is no confidence adjective in the output by design):

      * **Boundary guard** (candidate 42): if the central lands within
        BOUNDARY_GUARD_F of a bucket edge, `boundary_flag` is True and the contract
        refuses unless the rounding rule has been manually confirmed for this market.
      * **Calibration gate** (candidate 43): if `calibration_red=True` — i.e. the
        station's rolling held-out PIT failed flatness on a non-small sample, per
        `calibration_gate.calibration_tier` — the predictive pmf is itself
        untrustworthy, so the contract emits NO bucket probabilities and refuses
        with "REFUSED: calibration". This is the additive hook that lets candidate
        43's RED tier actually block emission rather than merely report.
    """
    probs = bucket_probabilities(central_c, sigma_c, grain=grain)
    central_settle, sigma_settle = _to_settlement(central_c, sigma_c, grain)
    bdist = boundary_distance(central_c, grain=grain)
    flagged = bdist < BOUNDARY_GUARD_F

    # Unit / rounding provenance: verbatim from the market if we have a confirmed
    # one, else an explicit honest placeholder. NEVER fabricated.
    if market is not None and rounding_confirmed:
        resolution_source = getattr(market, "title", None) or getattr(market, "slug", "market")
        unit_chain = (
            f"settles in °{getattr(market, 'grain', grain)}; "
            f"central {central_c:.2f}°C -> {central_settle:.2f}°{grain}; "
            f"round-half-up -> {int(_round_half_up(central_settle))}"
        )
    else:
        resolution_source = "UNVERIFIED: no confirmed live tape joined in repo"
        unit_chain = (
            f"source reports °C; convert °C*9/5+32 -> {central_settle:.2f}°{grain}; "
            f"round-half-up -> {int(_round_half_up(central_settle))} "
            f"(rounding rule NOT manually confirmed)"
        )

    boundary_refusal = bool(flagged and not rounding_confirmed)
    refusal = boundary_refusal or bool(calibration_red)

    reasons: list[str] = []
    if boundary_refusal:
        reasons.append(
            f"boundary: central {bdist:.2f} {grain}-units from a bucket edge, "
            f"rounding unconfirmed"
        )
    if calibration_red:
        reasons.append(
            "REFUSED: calibration"
            + (f" ({calibration_reason})" if calibration_reason else "")
        )
    refusal_reason = "; ".join(reasons) if reasons else None

    # On a calibration RED the pmf itself is miscalibrated: emit NO bucket
    # probabilities (refuse rather than serve a pmf we have just measured to be
    # untrustworthy). The boundary refusal, by contrast, keeps the pmf — a near-edge
    # central genuinely IS a coin-flip between the two named buckets.
    if calibration_red:
        emitted_buckets: dict[str, float] = {}
    else:
        emitted_buckets = {k: round(v, 4) for k, v in compact_buckets(probs).items()}

    return {
        "date": date,
        "station": station,
        "grain": grain,
        "central_F": round(central_settle, 3) if grain == "F" else None,
        "central_C": round(central_c, 3),
        "sigma_F": round(sigma_settle, 3) if grain == "F" else None,
        "sigma_settle": round(sigma_settle, 3),
        "buckets": emitted_buckets,
        "modal_bucket": int(_round_half_up(central_settle)),
        "boundary_distance_settle": round(bdist, 4),
        "boundary_flag": flagged,
        "refusal": refusal,
        "refusal_reason": refusal_reason,
        "resolution_source": resolution_source,
        "unit_chain": unit_chain,
        "lineage_weights": lineage_weights or {},
        "kalman_bias_F": round(kalman_bias_f, 4),
    }


# Schema keys every emitted contract must carry — used by the replay validator and
# tests so the contract can never silently drop a market-facing field.
REQUIRED_KEYS = frozenset({
    "date", "station", "grain", "central_C", "sigma_settle", "buckets",
    "modal_bucket", "boundary_distance_settle", "boundary_flag", "refusal",
    "refusal_reason", "resolution_source", "unit_chain", "lineage_weights",
    "kalman_bias_F",
})

# Vocabulary banned from any market-facing output (probabilities only).
_BANNED = ("confidence high", "confidence med", "confidence low",
           "confidence: high", "confidence: med", "confidence: low")


def _self_test() -> None:
    """Deterministic oracles: pmf integrates to 1; modal == round-half-up(central);
    boundary guard fires exactly at the edge and clears at the centre; no banned
    vocabulary leaks into the contract."""
    # 1) pmf is a probability distribution and centred on the right bucket.
    for c, s in ((17.3, 0.8), (28.9, 1.2), (10.0, 0.3), (33.7, 2.0)):
        p = bucket_probabilities(c, s, grain="F")
        assert abs(sum(p.values()) - 1.0) < 1e-9, (c, s, sum(p.values()))
        f = c * 9.0 / 5.0 + 32.0
        assert max(p, key=p.get) == int(_round_half_up(f)), (c, max(p, key=p.get))

    # 2) Degenerate sigma => point mass on the rounded central.
    p0 = bucket_probabilities(20.0, 0.0, grain="F")
    assert p0 == {int(_round_half_up(68.0)): 1.0}, p0

    # 3) Boundary guard fires near a settlement EDGE (the half-integers under
    #    round-half-up) and clears at a bucket CENTRE. The spec's "64.04 °F" is a
    #    bucket-centre (edges 63.5/64.5) -> robust; a value near 64.5 is the real
    #    coin-flip. This is the re-derivation against market.py/_round_half_up.
    c_centre = (64.04 - 32.0) * 5.0 / 9.0          # dead-centre of bucket 64 -> robust
    assert boundary_distance(c_centre, grain="F") > 0.45, boundary_distance(c_centre, grain="F")
    c_edge = (64.45 - 32.0) * 5.0 / 9.0            # 0.05 °F from the 64/65 edge at 64.5
    assert boundary_distance(c_edge, grain="F") < BOUNDARY_GUARD_F
    k_centre = daily_contract("TEST", "2026-06-10", c_centre, 0.8)
    assert k_centre["boundary_flag"] is False, k_centre["boundary_distance_settle"]
    k_edge = daily_contract("TEST", "2026-06-10", c_edge, 0.8)
    assert k_edge["boundary_flag"] is True and k_edge["refusal"] is True, k_edge

    # 4) A confirmed rounding rule downgrades the refusal even when flagged.
    class _Mkt:
        title = "HK high 2026-06-10"; grain = "F"
    k_conf = daily_contract("HK", "2026-06-10", c_edge, 0.8, market=_Mkt(), rounding_confirmed=True)
    assert k_conf["boundary_flag"] is True and k_conf["refusal"] is False, k_conf

    # 5) Required keys present; no banned confidence vocabulary anywhere in output.
    blob = " ".join(f"{k}={v}" for k, v in k_edge.items()).lower()
    assert set(k_edge) >= REQUIRED_KEYS, REQUIRED_KEYS - set(k_edge)
    assert not any(b in blob for b in _BANNED), "banned confidence vocabulary leaked"

    # 6) compact_buckets folds tails and still sums to ~1.
    cb = compact_buckets(bucket_probabilities(28.9, 1.5, grain="F"))
    assert abs(sum(cb.values()) - 1.0) < 1e-9, cb
    assert any(k.startswith("<=") for k in cb) or any(k.startswith(">=") for k in cb), cb

    # 7) Candidate-43 calibration hook: a RED tier refuses and emits NO buckets,
    #    even on an otherwise robust bucket-centre central. The boundary path is
    #    untouched (a non-RED central still serves its pmf).
    k_red = daily_contract("HK", "2026-06-10", c_centre, 0.8,
                           calibration_red=True, calibration_reason="p=0.004 on n=240")
    assert k_red["refusal"] is True and k_red["buckets"] == {}, k_red
    assert k_red["refusal_reason"].startswith("REFUSED: calibration"), k_red["refusal_reason"]
    assert k_red["boundary_flag"] is False, k_red          # geometry still reported honestly
    k_green = daily_contract("HK", "2026-06-10", c_centre, 0.8)
    assert k_green["refusal"] is False and k_green["buckets"], k_green

    print("bucket_contract self-test PASSED "
          "(pmf sums to 1; modal==round-half-up; boundary guard fires at edge, "
          "clears at centre; confirmed-rounding downgrades refusal; calibration RED "
          "refuses with empty buckets; no banned vocab)")


if __name__ == "__main__":
    _self_test()
