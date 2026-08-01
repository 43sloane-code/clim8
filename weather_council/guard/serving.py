"""Serving — Gate 2 at the pmf: the served base and per-bucket % by provenance.

The serving path (intraday_ceiling -> run.py) CALLS these; it never reimplements
provenance policy. Two questions, answered purely:

  * served_base_c — which running max the sharpened pmf is built on (the banking
    gate's answer; an uncorroborated lead is NOT in it);
  * bucket_prob   — which % may print on a bucket, by tier (provenance.served_prob):
    RECORDED tiers serve the pmf %, CORROBORATED_NOWCAST serves the RECORDED-bucket %
    until §5 promotion, UNCORROBORATED_NOWCAST serves None (annotation only — no %).

lead_annotation renders the excluded lead as a label carrying NO percentage — the
exact inverse of the 2026-07-31 KSFO harm (the label said "UNCORROBORATED" while the
served % moved).
"""
from __future__ import annotations

__all__ = ["served_base_c", "bucket_prob", "lead_annotation"]

from . import provenance as prov


def served_base_c(provenance: str, fused_with_cur_c: float | None,
                  fused_without_cur_c: float | None) -> float | None:
    """The running-max base the sharpened pmf may use, by tier. RECORDED and
    CORROBORATED serve the full fusion; UNCORROBORATED serves the cur_f-free
    fusion. Unknown tier -> the cur_f-free fusion (fail-closed)."""
    if provenance in (prov.RECORDED, prov.CORROBORATED_NOWCAST):
        return fused_with_cur_c
    return fused_without_cur_c


def bucket_prob(provenance: str, recorded_bucket_prob: float | None) -> float | None:
    """The servable % on the floor bucket for this tier (D1 — see provenance.py)."""
    return prov.served_prob(provenance, recorded_bucket_prob)


def lead_annotation(provenance: str, lead_bucket: int | None,
                    unit: str) -> dict | None:
    """The excluded lead as an ANNOTATION: {bucket, unit, prob=None, label}. None
    unless the tier is UNCORROBORATED_NOWCAST with a real lead bucket. The prob key
    is ALWAYS None — no % is ever fabricated on an uncorroborated nowcast."""
    if provenance != prov.UNCORROBORATED_NOWCAST or lead_bucket is None:
        return None
    return {"bucket": lead_bucket, "unit": unit, "prob": None,
            "label": prov.tier_label(provenance)}
