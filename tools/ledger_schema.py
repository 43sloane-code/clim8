#!/usr/bin/env python3
"""Candidate 45 — the ledger SCHEMA v2 validator (+ repo-hygiene companion).

The harness-optimizer ledger grew three eras of entry shape:

  * LEGACY  — store_cli rows: {ts, name, verdict, metrics, note}. No `id`.
  * PRE_V2  — id-bearing narrative entries (ids ~35-39) written before the formal
              40-45 program. They predate the honesty schema and are grandfathered.
  * V2      — every entry of the 40-45 program (marked by a `candidate` field). These
              MUST carry the multiple-comparisons / power / effect / cost fields that
              keep a search honest, so a future entry cannot quietly drop them.

This module makes V2 enforceable. An entry is "in scope" exactly when it has a
`candidate` field; such an entry must carry every required field (value MAY be null
where the metric does not apply — e.g. a CORRECTNESS or PROCESS entry has no CRPS
delta — but the KEY must be present so a reviewer sees it was considered, not
forgotten). The bootstrap-CI requirement is satisfied by EITHER `bootstrap_ci` or
`bootstrap_ci_95`.

Read-only: the auditor never rewrites history. It reports LEGACY / PRE_V2 / V2 /
INVALID so the loop driver (and a human) can see at a glance whether the program's
entries are schema-clean. Exit code is non-zero iff any in-scope entry is INVALID.

    PYTHONPATH=. python3 tools/ledger_schema.py [--ledger PATH]
"""
from __future__ import annotations

__all__ = [
    "V2_REQUIRED", "V2_CI_ONEOF", "V2_OPTIONAL", "classify_entry", "validate_v2",
    "audit",
]

import argparse
import json
import sys
from pathlib import Path

# Required keys for an in-scope (candidate) entry. Value may be null where N/A; the
# KEY must exist so the honesty field is acknowledged rather than silently omitted.
V2_REQUIRED = (
    "id", "candidate", "date", "kind", "title", "verdict",
    "evaluation",               # held-out vs search-set label (anti-overfitting)
    "K_candidates_evaluated",   # multiple-comparisons disclosure
    "n_paired_rows",            # statistical power
    "score_delta_crps",         # the effect (null for non-accuracy kinds)
    "cost",                     # wall/web cost (accuracy-vs-cost frontier)
    "artifacts",                # what code carries the claim
    "acceptance",               # the pre-stated bar and whether it was met
    "result",                   # the plain-language outcome
)
# At least one of these CI keys must be present (value may be null).
V2_CI_ONEOF = ("bootstrap_ci", "bootstrap_ci_95")
V2_OPTIONAL = (
    "execution_order", "note", "spec_divergences_corrected", "reaudit",
)


def classify_entry(entry: dict) -> str:
    """LEGACY (store_cli, no id) / PRE_V2 (id-bearing, pre-program) / SCOPE (a
    candidate entry that must satisfy the v2 schema)."""
    if "candidate" in entry:
        return "SCOPE"
    if "id" in entry and entry.get("id") is not None:
        return "PRE_V2"
    return "LEGACY"


def validate_v2(entry: dict) -> list:
    """Return a list of schema problems for an in-scope entry ([] == conformant)."""
    problems = []
    for k in V2_REQUIRED:
        if k not in entry:
            problems.append(f"missing required field: {k}")
    if not any(k in entry for k in V2_CI_ONEOF):
        problems.append(f"missing a bootstrap-CI field (one of {list(V2_CI_ONEOF)})")

    # Light type checks on the honesty fields (only when present and non-null).
    if isinstance(entry.get("K_candidates_evaluated"), bool) or \
            (entry.get("K_candidates_evaluated") is not None and
             not isinstance(entry.get("K_candidates_evaluated"), int)):
        problems.append("K_candidates_evaluated must be an int")
    cost = entry.get("cost")
    if cost is not None and not (isinstance(cost, dict) and "wall_minutes" in cost):
        problems.append("cost must be a dict with at least wall_minutes")
    npr = entry.get("n_paired_rows")
    if npr is not None and not isinstance(npr, (dict, int)):
        problems.append("n_paired_rows must be a dict, int, or null")
    arts = entry.get("artifacts")
    if arts is not None and not isinstance(arts, list):
        problems.append("artifacts must be a list")
    return problems


def audit(log: list) -> dict:
    """Classify every entry; validate the in-scope ones. Returns a structured
    report with counts and per-id problems."""
    legacy, pre_v2, v2_ok = 0, [], []
    invalid = {}
    for entry in log:
        cls = classify_entry(entry)
        if cls == "LEGACY":
            legacy += 1
        elif cls == "PRE_V2":
            pre_v2.append(entry.get("id"))
        else:  # SCOPE
            problems = validate_v2(entry)
            if problems:
                invalid[entry.get("id")] = problems
            else:
                v2_ok.append(entry.get("id"))
    return {
        "n_entries": len(log),
        "legacy": legacy,
        "pre_v2": sorted(x for x in pre_v2 if isinstance(x, int)),
        "v2_conformant": sorted(v2_ok),
        "invalid": invalid,
        "clean": not invalid,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=str(Path(".harness_opt") / "ledger.json"))
    a = ap.parse_args(argv)

    with open(a.ledger, "r", encoding="utf-8") as fh:
        log = json.load(fh)["log"]
    rep = audit(log)

    print("LEDGER SCHEMA v2 AUDIT (candidate 45)")
    print(f"  entries        : {rep['n_entries']}")
    print(f"  legacy         : {rep['legacy']} (store_cli rows, no id)")
    print(f"  pre-v2         : {len(rep['pre_v2'])} grandfathered  ids={rep['pre_v2']}")
    print(f"  v2 conformant  : {len(rep['v2_conformant'])}  ids={rep['v2_conformant']}")
    if rep["invalid"]:
        print(f"  INVALID        : {len(rep['invalid'])}")
        for eid, probs in sorted(rep["invalid"].items(), key=lambda kv: (kv[0] is None, kv[0])):
            print(f"    #{eid}:")
            for p in probs:
                print(f"        - {p}")
        print("\nNOT CLEAN — at least one in-scope (candidate) entry violates schema v2.")
        return 1
    print("\nCLEAN — every in-scope candidate entry satisfies schema v2.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
