"""Confidence provenance — Gate 2's vocabulary law (frozen design, binding).

The served % is a PURE FUNCTION of provenance ∈ {RECORDED, CORROBORATED_NOWCAST,
UNCORROBORATED_NOWCAST}:

  * RECORDED               — the floor is present in a settlement ob (hourly record
                             or the WU daily-max endpoint). Observation-grade % and
                             "banked" vocabulary are reserved for THIS tier alone.
  * CORROBORATED_NOWCAST   — a cur_f lead that cleared Gate 1 (fresh ∧ sustained ∨
                             converging) may bank above the recorded hourly, but D1:
                             NO FABRICATED % AT ANY TIER — it is served the
                             RECORDED-bucket % until the §5 promotion gate clears a
                             MEASURED confirmation rate (n≥30/city, Jeffreys 95%
                             lower bound, disjoint fit/verify, pooling compatibility
                             test, regression tripwire). Its own % enters FINDINGS.md
                             as MEASURED-PENDING at go-live.
  * UNCORROBORATED_NOWCAST — annotation only. NO % is served on the lead, and the
                             lead never enters the pmf base.

This module is the single source of that policy; the serving path CALLS it, never
reimplements it. KAT: tests/test_cur_f_guard.py.
"""
from __future__ import annotations

__all__ = ["RECORDED", "CORROBORATED_NOWCAST", "UNCORROBORATED_NOWCAST",
           "PROMOTION_STATE", "served_prob", "allows_observation_grade",
           "tier_label"]

RECORDED = "RECORDED"
CORROBORATED_NOWCAST = "CORROBORATED_NOWCAST"
UNCORROBORATED_NOWCAST = "UNCORROBORATED_NOWCAST"

# §5 promotion: the corroborated tier's OWN % is MEASURED-PENDING at go-live; it
# becomes SUPPORTED only when reconcile.promotion_state clears every frozen gate.
PROMOTION_STATE = "MEASURED-PENDING"

_TIERS = (RECORDED, CORROBORATED_NOWCAST, UNCORROBORATED_NOWCAST)


def served_prob(provenance: str, recorded_bucket_prob: float | None, *,
                measured_lower_bound: float | None = None,
                promotion_state: str = PROMOTION_STATE) -> float | None:
    """The % the guard permits on a bucket, by tier. Pure function of provenance:
      * RECORDED               -> the pmf's own (recorded-bucket) probability;
      * CORROBORATED_NOWCAST   -> the RECORDED-bucket % until §5 promotes; only a
                                  SUPPORTED promotion may substitute the MEASURED
                                  Jeffreys 95% lower bound — never a fabricated %;
      * UNCORROBORATED_NOWCAST -> None: annotation only, NO % is servable.
    An unknown tier serves nothing (fail-closed)."""
    if provenance == UNCORROBORATED_NOWCAST:
        return None
    if provenance == RECORDED:
        return recorded_bucket_prob
    if provenance == CORROBORATED_NOWCAST:
        if promotion_state == "SUPPORTED" and measured_lower_bound is not None:
            return measured_lower_bound
        return recorded_bucket_prob
    return None


def allows_observation_grade(provenance: str) -> bool:
    """"Banked" / observation-grade vocabulary is reserved for RECORDED obs. A
    corroborated nowcast is a nowcast; an uncorroborated one is an annotation."""
    return provenance == RECORDED


def tier_label(provenance: str) -> str:
    """The one honest label per tier (renderers quote this; they do not invent
    their own vocabulary)."""
    return {RECORDED: "recorded (observation-grade)",
            CORROBORATED_NOWCAST: "corroborated nowcast (recorded-bucket %; own % §5 MEASURED-PENDING)",
            UNCORROBORATED_NOWCAST: "uncorroborated nowcast (annotation only — no %)"}.get(
                provenance, "unknown (fail-closed)")
