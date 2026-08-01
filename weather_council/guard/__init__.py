"""cur_f corroboration guard v2 — two coupled gates around the live-floor fusion.

Frozen, binding design: ledger/preregistered/cur_f_corroboration_guard_v2.md.

  * Gate 1 BANKING (corroboration.py + banking.py): cur_f advances the banked floor
    above the recorded hourly ONLY when CORROBORATED = fresh ∧ (sustained ∨
    converging). An uncorroborated lead is excluded from the served running-max
    base — the sharpened pmf is monotone-safe, so a lead in the base floors the
    whole served distribution at the lead (the 2026-07-31 KSFO 74-over-72 harm,
    incident specimen K6; same class as London 07-11, K2).
  * Gate 2 CONFIDENCE-PROVENANCE (provenance.py + serving.py): the served % is a
    pure function of provenance ∈ {RECORDED, CORROBORATED_NOWCAST,
    UNCORROBORATED_NOWCAST}. Observation-grade %/"banked" vocabulary is reserved
    for RECORDED obs; the corroborated tier serves the RECORDED-bucket % until the
    §5 promotion gate clears (reconcile.py — MEASURED-PENDING at go-live); the
    uncorroborated tier is annotation only, NO %.
  * ObsLog (obslog.py): every v3 read-sequence persisted from go-live — future
    incidents are replayable, not reconstructed.
  * FAIL-CLOSED everywhere: any state fault (corrupt/missing ObsLog, unknown city
    config, unparseable stamps, an internal error) degrades the decision to
    UNCORROBORATED — the lead is stripped, never banked.

The guard WRAPS/FEEDS sources._fuse_live_floor (which keeps its frozen register
caps, a42ffa2/6533fca — untouched); it never deletes or re-implements it.
Out of scope per the prereg: no cur_f ≤ daily-max clamp, no CRPS gating, no new
data sources, no change to the RECORDED-floor lock %.

Public entry: evaluate_cur_f_lead() — one call, one GuardResult, never raises.
KAT: tests/test_cur_f_guard.py (9 KATs, exact-match, no partial certification).
"""
from __future__ import annotations

__all__ = ["GuardResult", "evaluate_cur_f_lead", "obslog", "corroboration",
           "banking", "serving", "provenance", "reconcile"]

import datetime as dt
from dataclasses import dataclass

from . import banking, corroboration, obslog, provenance, reconcile, serving


@dataclass(frozen=True)
class GuardResult:
    """One guard evaluation. `served_running_max_c` is the ONLY running max the
    sharpened pmf may be built on; `banked_running_max_c` the banked floor;
    `lead_c` the excluded/annotated cur_f lead (None when no lead or it banked).
    `provenance` is Gate 2's tier; the rest are the audit trail."""
    provenance: str
    served_running_max_c: float | None
    banked_running_max_c: float | None
    lead_c: float | None
    corroborated: bool
    fresh: bool | None
    sustained: bool | None
    converging: bool | None
    pre_peak: bool | None
    freshness_window_min: float | None
    freshness_basis: str | None
    cur_f: float | None
    note: str

    def as_dict(self) -> dict:
        return {"provenance": self.provenance,
                "served_running_max_c": self.served_running_max_c,
                "banked_running_max_c": self.banked_running_max_c,
                "lead_c": self.lead_c, "corroborated": self.corroborated,
                "fresh": self.fresh, "sustained": self.sustained,
                "converging": self.converging, "pre_peak": self.pre_peak,
                "freshness_window_min": self.freshness_window_min,
                "freshness_basis": self.freshness_basis, "cur_f": self.cur_f,
                "promotion_state": provenance.PROMOTION_STATE, "note": self.note}


def evaluate_cur_f_lead(*, icao: str, cur_f: float | None,
                        valid_local: str | None,
                        reads: list[dict] | None,
                        now_local: dt.datetime | None,
                        inter_obs_min: list[float] | None,
                        recorded_max_c: float | None,
                        fused_with_cur_c: float | None,
                        fused_without_cur_c: float | None,
                        config_path: str = corroboration.CONFIG_PATH) -> GuardResult:
    """Evaluate the latest cur_f read against the recorded floor and return the
    floors/provenance the serving path must use. NEVER raises: any fault degrades
    to UNCORROBORATED (fail-closed) with the cur_f-free fusion served.

    `cur_f`/`valid_local` are the CURRENT v3 read (supplied independently of the
    ledger, so a corrupt/missing ObsLog can never masquerade as "no lead" — with
    no corroborating sequence the lead simply cannot bank). `reads` is this
    city/day's ObsLog sequence; the current read is appended to it if absent.
    `recorded_max_c` the recorded floor (obs running max ∪ WU daily-max endpoint);
    the two `fused_*` are _fuse_live_floor's output with and without the cur_f
    contribution (the register path passes through unchanged)."""
    try:
        rows = [r for r in (reads or []) if isinstance(r, dict)]
        if isinstance(cur_f, (int, float)):
            have_current = any(isinstance(r.get("cur_f"), (int, float))
                               and r.get("valid_local") == valid_local
                               and abs(r["cur_f"] - cur_f) < 1e-9 for r in rows)
            if not have_current:
                rows = rows + [{"ts_utc": dt.datetime.now(dt.timezone.utc)
                                .isoformat(timespec="seconds"),
                                "cur_f": cur_f, "valid_local": valid_local,
                                "secondaries": {}}]
        cur_c = corroboration.f2c(cur_f) if isinstance(cur_f, (int, float)) else None
        lead = (cur_c is not None and recorded_max_c is not None
                and cur_c > recorded_max_c + 1e-9)
        cfg = corroboration.load_city_config(icao, config_path)
        if cur_f is None or not lead:
            served, banked, prov = banking.served_floors(
                recorded_c=recorded_max_c, cur_f=cur_f,
                fused_with_cur_c=fused_with_cur_c,
                fused_without_cur_c=fused_without_cur_c, corroborated=False)
            return GuardResult(prov, served, banked, None, False, None, None,
                               None, None, None, None, cur_f,
                               "no cur_f lead over the recorded floor")
        if cfg is None or now_local is None or getattr(now_local, "tzinfo", None) is None:
            # Fail-closed on unknown city config / unprovable clock.
            return GuardResult(
                provenance.UNCORROBORATED_NOWCAST, fused_without_cur_c,
                recorded_max_c, cur_c, False, None, None, None, None, None,
                None, cur_f,
                "fail-closed: guard state unavailable (config/clock)")
        dec = corroboration.decide(rows, now_local=now_local,
                                   inter_obs_min=inter_obs_min,
                                   recorded_max_c=recorded_max_c, cfg=cfg)
        served, banked, prov = banking.served_floors(
            recorded_c=recorded_max_c, cur_f=cur_f,
            fused_with_cur_c=fused_with_cur_c,
            fused_without_cur_c=fused_without_cur_c,
            corroborated=dec.corroborated)
        note = (f"corroborated (fresh={dec.fresh}, sustained={dec.sustained}, "
                f"converging={dec.converging})" if dec.corroborated else
                f"fresh={dec.fresh}, sustained={dec.sustained}, "
                f"converging={dec.converging}")
        return GuardResult(prov, served, banked,
                           cur_c if prov == provenance.UNCORROBORATED_NOWCAST else None,
                           dec.corroborated, dec.fresh, dec.sustained,
                           dec.converging, dec.pre_peak, dec.freshness_window_min,
                           dec.freshness_basis, cur_f, note)
    except Exception as exc:                                  # fail-closed, always
        try:
            served, banked, prov = banking.served_floors(
                recorded_c=recorded_max_c, cur_f=cur_f,
                fused_with_cur_c=fused_with_cur_c,
                fused_without_cur_c=fused_without_cur_c, corroborated=False)
        except Exception:
            served, banked, prov = (fused_without_cur_c, recorded_max_c,
                                    provenance.UNCORROBORATED_NOWCAST)
        return GuardResult(prov, served, banked, None, False, None, None, None,
                           None, None, None, None,
                           f"fail-closed: guard fault ({type(exc).__name__})")
