"""Provenance audit (Plan 3 Phase 0 step 1) — does stored provenance cover what attribution needs?

The post-mortem engine (Phase 3) decomposes each settled error into INPUT / BLEND / BIAS /
SETTLEMENT components. Each component consumes specific fields captured at issue time. This tool
enumerates that dependency, checks the latest captured provenance blob actually carries each
field, and reports coverage (attributable vs UNATTRIBUTABLE-PREPROVENANCE) per city. It is a
READ-ONLY report — the gap list, if any, is the spec for what Phase 0 still has to capture.

Usage:  PYTHONPATH=. python3 tools/provenance_audit.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from weather_council.storage import DB_PATH                 # noqa: E402
from weather_council.provenance import validate_provenance  # noqa: E402

# Each Phase-3 attribution component and the provenance fields it consumes. A dotted path with
# "[]" means "present on every vote". This IS the contract Phase 3 will rely on.
REQUIREMENTS = {
    "input_error (were the raw inputs collectively wrong?)":
        ["votes[].corrected_high", "votes[].weight_high"],
    "blend_deviation (did weighting beat naive consensus?)":
        ["blend.high_pre_bias", "blend.naive_high"],
    "bias_contribution (did the correction help THIS day?)":
        ["blend.bias_high", "blend.high"],
    "settlement_divergence (anchor vs contract payout)":
        ["_columnar.station_icao"],          # station identity is columnar, cross-ref market_snapshots
    "regime conditioning (attribute by regime)":
        ["regime", "consensus"],
    "code-era conditioning":
        ["pipeline_version"],
}


def _has_path(prov: dict, path: str) -> bool:
    if path.startswith("_columnar."):
        return True                          # station_* live in verdicts columns, not the blob
    if "[]" in path:                         # "votes[].field" -> field present + non-null on every vote
        base, field = path.split("[].")
        items = prov.get(base)
        if not isinstance(items, list) or not items:
            return False
        return all(isinstance(it, dict) and it.get(field) is not None for it in items)
    cur = prov
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return cur is not None


def main() -> int:
    conn = sqlite3.connect(DB_PATH)
    try:
        latest = conn.execute(
            "SELECT place, target_date, provenance_json FROM verdicts "
            "WHERE provenance_json IS NOT NULL ORDER BY issued_at DESC LIMIT 1").fetchone()
        total = conn.execute("SELECT COUNT(*) FROM verdicts").fetchone()[0]
        withp = conn.execute(
            "SELECT COUNT(*) FROM verdicts WHERE provenance_json IS NOT NULL").fetchone()[0]
        quarantined = conn.execute(
            "SELECT COUNT(*) FROM verdicts WHERE provenance_ok = 0").fetchone()[0]
        by_city = conn.execute(
            "SELECT place, COUNT(*), "
            "  SUM(CASE WHEN provenance_json IS NOT NULL THEN 1 ELSE 0 END) "
            "FROM verdicts GROUP BY place ORDER BY place").fetchall()
    finally:
        conn.close()

    print("PROVENANCE AUDIT (Plan 3 Phase 0) — attribution-readiness of captured provenance")
    print("=" * 78)
    if latest is None:
        print("  NO provenance captured yet — every verdict is UNATTRIBUTABLE-PREPROVENANCE.")
        print("  Run one verdict (log_verdict) to capture the first blob, then re-audit.")
        return 0

    place, tgt, pj = latest
    prov = json.loads(pj)
    print(f"  latest blob: {place} {tgt}  ({len(pj.encode())} B)  validate: "
          f"{validate_provenance(prov) or 'OK'}")
    print()
    print("  ATTRIBUTION-COMPONENT COVERAGE (what Phase 3 needs vs what is stored):")
    gaps = 0
    for comp, paths in REQUIREMENTS.items():
        missing = [p for p in paths if not _has_path(prov, p)]
        mark = "OK " if not missing else "GAP"
        print(f"    [{mark}] {comp}")
        for p in paths:
            tick = "·" if _has_path(prov, p) else "✗ MISSING"
            print(f"           {p:32} {tick}")
        gaps += len(missing)
    print()
    print(f"  GAPS vs the Phase-3 taxonomy: {gaps}  "
          + ("(spec closed — attribution can run)" if gaps == 0 else "(these fields must be captured)"))
    print()
    print("  COVERAGE (per city):")
    print(f"    repo-wide: {withp}/{total} attributable, {total - withp} "
          f"UNATTRIBUTABLE-PREPROVENANCE, {quarantined} quarantined (provenance_ok=0)")
    for pl, n, wp in by_city:
        wp = wp or 0
        print(f"    {pl[:26]:26} {wp:4}/{n:<4} attributable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
