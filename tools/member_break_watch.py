#!/usr/bin/env python3
"""member_break_watch.py — CLI for the per-member bias-break watch (alert-only).

Executes ledger/preregistered/member_bias_break_watch.md: joins issue-time provenance
votes (raw_high per member) to settled truth on the verdicts table, pins each
(city, member) cell's FIRST 20 settled errors as its frozen reference
(reports/member_bias_ref.json — a re-pin is a human, documented breakpoint), and alerts
when a cell's rolling 10-error mean exits the reference's bootstrap 99% CI — the same
break test as the TWC driver-health monitor.

Recommend-only: prints ALERT lines; changes nothing served. Runs inside accumulate.
As of first shipping (2026-07-12) every cell reads ACCRUING: provenance logging began
2026-07-11 and none of those verdicts has settled yet — the watch arms itself as the
settled∧provenance join fills (~3 weeks to first pins for daily cities).

Run:       PYTHONPATH=. python3 tools/member_break_watch.py
Self-test: PYTHONPATH=. python3 tools/member_break_watch.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from weather_council.member_break import REF_N, ROLL_K, assess_all, extract_errors  # noqa: E402

DB = os.path.join(ROOT, "verdicts.db")
PINS = os.path.join(ROOT, "reports", "member_bias_ref.json")


def load_settled_provenance(db_path: str = DB) -> list[tuple[str, str, float, list[dict]]]:
    """Settled verdicts carrying quarantine-clean provenance, oldest-first."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT place, target_date, actual_high, provenance_json FROM verdicts "
            "WHERE actual_high IS NOT NULL AND provenance_json IS NOT NULL "
            "AND (provenance_ok IS NULL OR provenance_ok = 1) "
            "ORDER BY target_date").fetchall()
    finally:
        conn.close()
    out = []
    for place, date, actual, blob in rows:
        try:
            votes = json.loads(blob).get("votes") or []
        except ValueError:
            continue
        out.append((place, date, actual, votes))
    return out


def _load_pins() -> dict:
    try:
        with open(PINS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        from weather_council import member_break
        member_break._self_test()
        return 0

    cells = extract_errors(load_settled_provenance())
    pins_before = _load_pins()
    pins, results = assess_all(cells, pins_before)
    if pins != pins_before:
        os.makedirs(os.path.dirname(PINS), exist_ok=True)
        with open(PINS, "w", encoding="utf-8") as f:
            json.dump(pins, f, indent=1, sort_keys=True)

    breaks = {k: r for k, r in results.items() if r["status"] == "BREAK"}
    ok = sum(1 for r in results.values() if r["status"] == "OK")
    accruing = sum(1 for r in results.values() if r["status"] == "ACCRUING")
    newly_pinned = len(pins) - len(pins_before)

    print(f"  MEMBER-BIAS BREAK WATCH (alert-only; ref n={REF_N}, roll k={ROLL_K}, 99% CI): "
          f"{len(results)} cells — {len(breaks)} BREAK, {ok} OK, {accruing} accruing"
          + (f", {newly_pinned} newly pinned" if newly_pinned else ""))
    if not results:
        print("    no settled provenance rows yet (logging began 2026-07-11) — the watch "
              "arms itself as the join fills; nothing to assert today")
    for k, r in sorted(breaks.items()):
        lo, hi = r["ci"]
        print(f"    !! BREAK {k}: rolling{ROLL_K} raw-bias {r['rolling_mean']:+.2f}C outside "
              f"frozen ref CI [{lo:+.2f},{hi:+.2f}] (ref mean {r['ref_mean']:+.2f}) — "
              f"pipeline change suspected; route to the fold-gated recalibration path "
              f"(NEVER auto-correct)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
