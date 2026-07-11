"""twc_offset_report.py — print the live TWC signed-offset table (Plan 4 Phase 3, recommend-only).

Which way TWC runs vs the WU settlement oracle, per city, per attr, with n + CI + the three-gate
certification. It will honestly read UNMEASURED for weeks until each cell reaches n≥20 — that is
correct output, not a bug. TWC never votes and never settles; this only informs the displayed
cross-reference line.

Run:  PYTHONPATH=. python3 tools/twc_offset_report.py [--source twc]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from weather_council.twc_offset import estimate_offsets, report_lines


def main() -> int:
    ap = argparse.ArgumentParser(description="Signed-offset table for a tracked forecaster.")
    ap.add_argument("--source", default="twc", help="tracked_forecasts source tag (default: twc)")
    args = ap.parse_args()
    for line in report_lines(estimate_offsets(args.source)):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
