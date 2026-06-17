#!/usr/bin/env python3
"""Candidate 44 runner — read the live ledger + stop-rule config and print the
loop's current go/no-go state, the trailing no-bake streak, the re-arm picture,
and the FALSIFIED re-audit. Read-only; the loop driver consults this each
iteration before proposing a new lever.

    PYTHONPATH=. python3 tools/stop_rule_run.py [--settlement-day] \
        [--new-rows london_high=30,london_low=30,...]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from weather_council.stop_rule import (                                      # noqa: E402
    load_config, loop_state, classify_verdict,
    reaudit_falsified, format_state,
)

LEDGER = ROOT / ".harness_opt" / "ledger.json"
CONFIG = ROOT / ".harness_opt" / "stop_rule.json"


def _parse_rows(s):
    if not s:
        return None
    out = {}
    for part in s.split(","):
        k, _, v = part.partition("=")
        out[k.strip()] = int(v)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--settlement-day", action="store_true")
    ap.add_argument("--new-rows", default="", help="station=count,station=count,...")
    a = ap.parse_args(argv)

    cfg = load_config(str(CONFIG))
    with open(LEDGER, encoding="utf-8") as fh:
        log = json.load(fh)["log"]

    new_rows = _parse_rows(a.new_rows)
    state = loop_state(log, cfg, new_rows_since_suspend=new_rows,
                       settlement_day=a.settlement_day)

    print("STOP/RESTART RULE (candidate 44) — harness-optimizer search loop")
    print(f"config: suspend at {cfg['max_consecutive_nobake']} consecutive no-bakes; "
          f"re-arm at {cfg['rearm_min_new_rows_per_station']} new rows/station; "
          f"settlement_freeze={cfg['settlement_freeze']}; reaudit_min_n={cfg['reaudit_min_n']}")
    print()

    # The trailing gating entries that formed (or broke) the streak.
    print("trailing gating entries (newest first):")
    shown = 0
    for entry in reversed(log):
        cls = classify_verdict(entry, cfg)
        if cls == "NEUTRAL":
            continue
        label = entry.get("title") or entry.get("verdict") or entry.get("kind") or "?"
        print(f"  #{entry.get('id')}  {cls:8s}  {str(label)[:62]}")
        shown += 1
        if cls == "POSITIVE" or shown >= 8:
            break
    print()
    print(format_state(state))
    print()

    ra = reaudit_falsified(log, cfg)
    if ra:
        print("FALSIFIED re-audit:")
        for r in ra:
            n = r["n"] if r["n"] is not None else "??"
            print(f"  #{r['id']}  n={n}  -> {r['proposal']}: {r['reason']}")
    else:
        print("FALSIFIED re-audit: no entry carries a FALSIFIED token.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
