"""Issue-time provenance capture (Plan 3 Phase 0) — store the DECISIONS behind each verdict.

The verdicts table persists only the final high/low/confidence + anchor identity. The per-source
votes, the applied bias, the regime classification, and the ensemble spread — everything an error
ATTRIBUTION (Phase 3) needs — are computed at issue time and then DISCARDED. Attribution without
the stored inputs is retrodiction, not measurement. This module snapshots those decisions, exactly
as computed, into one compact JSON blob so a settled error can later be decomposed into INPUT vs
BLEND vs BIAS vs SETTLEMENT components.

SCOPE. It stores DECISIONS, not raw source payloads — a NWP center's full forecast is re-fetchable;
the weight the council gave it, and the bias it removed, are not. Budget: ≤ 8 KB/row. READ-ONLY
w.r.t. forecasting: it records what was decided, never a served number, and never feeds back into a
vote. Pure extraction from the Verdict (no forecast is recomputed). Deterministic, stdlib-only.

A row with provenance_json IS NULL is permanently UNATTRIBUTABLE-PREPROVENANCE — pre-dates this
capture. Downstream code MUST count those and never guess about them.

Self-test:  python3 -m weather_council.provenance
"""
from __future__ import annotations

__all__ = ["PROVENANCE_VERSION", "MAX_BYTES", "build_provenance",
           "validate_provenance", "pipeline_version"]

import json
import subprocess
from pathlib import Path

PROVENANCE_VERSION = 1
MAX_BYTES = 8 * 1024           # decisions, not raw payloads — measured on write


def pipeline_version() -> str:
    """Short git hash of the code that produced the verdict, so attribution can condition on the
    code era (a bias fixed in commit X should not be blamed for pre-X errors). Best-effort:
    returns 'unknown' when git is unavailable; never raises."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True, text=True, timeout=5)
        h = out.stdout.strip()
        return h or "unknown"
    except Exception:
        return "unknown"


def _f(x):
    """Round a float for compact, deterministic storage; None passes through."""
    return round(float(x), 3) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _jsonable(obj):
    """Coerce to JSON-safe (non-serializable -> str) so provenance is robust to whatever the
    regime/consensus helpers return, without hard-coding their schema."""
    try:
        return json.loads(json.dumps(obj, default=str))
    except Exception:
        return str(obj)


def build_provenance(v, version=None) -> dict:
    """Snapshot the decisions behind verdict `v`: per-source votes (raw + bias-corrected + weight),
    the blend pre/post bias, the applied bias per attribute, the regime + consensus dicts, the
    spread, and the pipeline version. Pure extraction — no forecast is recomputed. `version`
    overrides the git hash (tests pass a fixed value). Never raises on a partially-formed Verdict."""
    from .council import applied_bias_correction, _classify_regime, regime_consensus

    wh = v.weights_high or {}
    wl = v.weights_low or {}
    votes = []
    for vote in (v.votes or []):
        mid = getattr(vote.spec, "member_id", None)
        votes.append({
            "member_id": mid,
            "model": getattr(vote.spec, "model", None),
            "institution": getattr(vote.spec, "institution", None),
            "raw_high": _f(vote.raw_high), "raw_low": _f(vote.raw_low),
            "corrected_high": _f(vote.corrected_high), "corrected_low": _f(vote.corrected_low),
            "skill_high": _f(vote.skill_high), "skill_low": _f(vote.skill_low),
            "weight_high": _f(wh.get(mid)), "weight_low": _f(wl.get(mid)),
            "eligible": bool(vote.eligible),
        })

    try:
        bias_h = applied_bias_correction(v, "high")
    except Exception:
        bias_h = None
    try:
        bias_l = applied_bias_correction(v, "low")
    except Exception:
        bias_l = None
    try:
        regime = _jsonable(_classify_regime(v))
    except Exception:
        regime = None
    try:
        consensus = _jsonable(regime_consensus(v))
    except Exception:
        consensus = None

    return {
        "version": PROVENANCE_VERSION,
        "pipeline_version": version if version is not None else pipeline_version(),
        "votes": votes,
        "included_high": list(v.included_high or []),
        "included_low": list(v.included_low or []),
        "blend": {
            "high": _f(v.high), "low": _f(v.low),
            "bias_high": _f(bias_h), "bias_low": _f(bias_l),
            "high_pre_bias": _f(v.high - bias_h) if bias_h is not None else None,
            "low_pre_bias": _f(v.low - bias_l) if bias_l is not None else None,
            "naive_high": _f(v.naive_high), "naive_low": _f(v.naive_low),
        },
        "spread": {"high": _f(v.high_spread), "low": _f(v.low_spread)},
        "confidence": {"tier": v.confidence, "detail": str(v.confidence_detail)[:200]},
        "regime": regime,
        "consensus": consensus,
        # A LOGGED verdict means the tropical-cyclone halt gate did NOT fire — a halted city
        # yields no verdict at all — so this records the (implicit) no-halt state honestly.
        "tc_gate": {"halted": False},
    }


def validate_provenance(prov) -> list:
    """Return a list of problems ([] == conformant). QUARANTINE-AND-ALARM: the caller stores the
    row either way (provenance_ok=0 on problems) — a validator bug must never lose a decision
    record — and records a soft failure so the gap is visible, not silent."""
    problems = []
    if not isinstance(prov, dict):
        return ["provenance is not a dict"]
    for k in ("version", "pipeline_version", "votes", "blend", "regime"):
        if k not in prov:
            problems.append(f"missing key: {k}")
    votes = prov.get("votes")
    if not isinstance(votes, list) or not votes:
        problems.append("votes must be a non-empty list")
    else:
        for i, vt in enumerate(votes):
            if not isinstance(vt, dict) or "member_id" not in vt:
                problems.append(f"vote[{i}] missing member_id")
                break
    b = prov.get("blend")
    if not isinstance(b, dict) or "high" not in b or "bias_high" not in b:
        problems.append("blend must be a dict with high + bias_high")
    try:
        size = len(json.dumps(prov).encode())
        if size > MAX_BYTES:
            problems.append(f"provenance {size}B exceeds the {MAX_BYTES}B budget")
    except Exception:
        problems.append("provenance not JSON-serializable")
    return problems


def _selftest() -> None:
    import types
    # A minimal Verdict-shim carrying exactly what build_provenance reads.
    def _vote(mid, rh, ch, sk):
        return types.SimpleNamespace(
            spec=types.SimpleNamespace(member_id=mid, model=mid.upper(), institution="Inst"),
            raw_high=rh, raw_low=rh - 6, corrected_high=ch, corrected_low=ch - 6,
            skill_high=sk, skill_low=sk, eligible=True)
    v = types.SimpleNamespace(
        high=31.3, low=25.1, high_spread=1.1, low_spread=0.6,
        confidence="HIGH", confidence_detail={"tier": "high"},
        votes=[_vote("ecmwf", 30.5, 31.4, 0.9), _vote("gfs", 30.8, 31.2, 0.8)],
        included_high=["ecmwf", "gfs"], included_low=["ecmwf", "gfs"],
        weights_high={"ecmwf": 0.6, "gfs": 0.4}, weights_low={"ecmwf": 0.5, "gfs": 0.5},
        naive_high=30.65, naive_low=24.65)
    # patch the council helpers used inside build_provenance to avoid needing a full Verdict
    import weather_council.provenance as _P
    import weather_council.council as _C
    _orig = (_C.applied_bias_correction, _C._classify_regime, _C.regime_consensus)
    _C.applied_bias_correction = lambda vv, attr="high": (0.85 if attr == "high" else 0.5)
    _C._classify_regime = lambda vv: {"regime": "in-season", "elevated": False}
    _C.regime_consensus = lambda vv: {"status": "MATCHED", "sigma": 0.61}
    try:
        p = build_provenance(v, version="testhash")
    finally:
        _C.applied_bias_correction, _C._classify_regime, _C.regime_consensus = _orig
    assert p["version"] == PROVENANCE_VERSION and p["pipeline_version"] == "testhash"
    assert len(p["votes"]) == 2 and p["votes"][0]["member_id"] == "ecmwf"
    assert p["votes"][0]["weight_high"] == 0.6 and p["votes"][0]["raw_high"] == 30.5
    # bias math: pre-bias = final - bias
    assert p["blend"]["bias_high"] == 0.85 and p["blend"]["high_pre_bias"] == round(31.3 - 0.85, 3)
    assert p["regime"]["regime"] == "in-season" and p["consensus"]["status"] == "MATCHED"
    assert p["tc_gate"]["halted"] is False
    assert validate_provenance(p) == []
    # size well under budget
    size = len(json.dumps(p).encode())
    assert size <= MAX_BYTES, size
    # validation catches a malformed blob
    bad = dict(p); bad.pop("votes")
    assert any("votes" in m for m in validate_provenance(bad))
    huge = dict(p); huge["regime"] = {"x": "y" * (MAX_BYTES + 10)}
    assert any("budget" in m for m in validate_provenance(huge))
    print(f"provenance selftest PASSED (votes/bias/regime extracted; pre-bias math; "
          f"size {size}B ≤ {MAX_BYTES}B; validate catches missing-votes + over-budget)")


if __name__ == "__main__":
    _selftest()
