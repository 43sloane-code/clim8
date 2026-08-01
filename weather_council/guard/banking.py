"""Banking — Gate 1's floors: what a cur_f lead may do to the banked floor and the
served running-max base (frozen design, binding).

  * cur_f advances the BANKED floor above the recorded hourly ONLY when
    CORROBORATED (fresh ∧ (sustained ∨ converging) — corroboration.decide).
  * An UNCORROBORATED lead is excluded from the SERVED running-max base (the
    sharpened pmf is monotone-safe, so a lead in the base floors the whole
    distribution at the lead — the 2026-07-31 KSFO 74-over-72 harm). The recorded
    floor and any register-fused floor are untouched.
  * The 24h REGISTER path is NOT the guard's object: it keeps its own frozen caps
    (attribution a42ffa2 / phantom 6533fca in sources._fuse_live_floor) and fuses
    unchanged. The guard feeds on _fuse_live_floor's two variants (with/without
    cur_f) and only ever governs the cur_f contribution.

Fail-closed: a fault anywhere degrades to UNCORROBORATED (the lead is stripped,
never banked). PURE — KAT: tests/test_cur_f_guard.py.
"""
from __future__ import annotations

__all__ = ["banked_floor_c", "served_floors"]

from . import provenance as prov
from .corroboration import f2c

_EPS = 1e-9


def banked_floor_c(recorded_c: float | None, cur_c: float | None,
                   corroborated: bool) -> float | None:
    """The banked floor: the recorded floor, widened by cur_f ONLY when the lead is
    corroborated. Never lowered; a corroborated cur_f below the record changes
    nothing."""
    if cur_c is None or not corroborated:
        return recorded_c
    if recorded_c is None or cur_c > recorded_c + _EPS:
        return cur_c
    return recorded_c


def served_floors(*, recorded_c: float | None, cur_f: float | None,
                  fused_with_cur_c: float | None, fused_without_cur_c: float | None,
                  corroborated: bool) -> tuple[float | None, float | None, str]:
    """(served_running_max_c, banked_c, provenance) for the ceiling.

    A cur_f LEAD exists when cur_f converts above the recorded floor (obs ∪ WU
    daily-max endpoint). No lead -> RECORDED and the pre-guard fusion passes through
    byte-identical. A corroborated lead banks and serves the full fusion. An
    uncorroborated lead serves the cur_f-free fusion (recorded floor + register
    path) and leaves the lead as annotation."""
    cur_c = f2c(cur_f) if isinstance(cur_f, (int, float)) else None
    lead = (cur_c is not None and recorded_c is not None
            and cur_c > recorded_c + _EPS)
    if not lead:
        return fused_with_cur_c, recorded_c, prov.RECORDED
    if corroborated:
        return fused_with_cur_c, banked_floor_c(recorded_c, cur_c, True), \
            prov.CORROBORATED_NOWCAST
    return fused_without_cur_c, recorded_c, prov.UNCORROBORATED_NOWCAST
