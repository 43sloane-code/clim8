#!/usr/bin/env python3
"""Candidate 43 runner — replay the last N logged days per station, compute the
leak-free PIT calibration tier (GREEN/AMBER/RED), and save a per-station PIT plot
to .harness_opt/pit/. Read-only; writes only text artifacts. No network.

    PYTHONPATH=. python3 tools/calibration_gate_run.py [--window 60]

The tier is the candidate-43 gate: RED means the prediction layer must emit
'REFUSED: calibration' rather than bucket probabilities.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from weather_council.scoring import pit                              # noqa: E402
from weather_council.calibration_gate import calibration_tier  # noqa: E402

REPORTS = ROOT / "reports"
PIT_DIR = ROOT / ".harness_opt" / "pit"
STREAMS = {
    "london_high": "london_high.csv",
    "london_low": "london_low.csv",
    "hong_kong_high": "hong_kong_high.csv",
    "hong_kong_low": "hong_kong_low.csv",
}
WARMUP = 10            # min strictly-earlier residuals before a PIT is meaningful


def _load(path: Path) -> list[tuple[str, float, float]]:
    out = []
    with open(path) as fh:
        for r in csv.DictReader(fh):
            try:
                out.append((r["date"], float(r["point"]), float(r["realized"])))
            except (ValueError, KeyError):
                continue
    out.sort()
    return out


def _leakfree_pits(rows, window):
    """PIT of each of the last `window` days' residual through ONLY strictly-earlier
    residuals — identical construction to daily_healthcheck._walk_forward."""
    resid = [rz - pt for _, pt, rz in rows]
    dates = [d for d, _, _ in rows]
    start = max(WARMUP, len(rows) - window)
    pits, win_dates = [], []
    for i in range(start, len(rows)):
        prior = resid[:i]
        if len(prior) >= WARMUP:
            pits.append(pit(prior, resid[i]))
            win_dates.append(dates[i])
    return pits, win_dates


def _plot(name, tier_info, window) -> str:
    """A tiny text PIT histogram with the Bröcker–Smith consistency bar marked."""
    hist = tier_info["histogram"]
    bins = len(hist) or 1
    n = sum(hist)
    lo, hi = tier_info["consistency_bar"]
    width = 40
    peak = max(hist) if hist else 1
    lines = [
        f"PIT histogram — {name}  (last {window} held-out days, n={n})",
        f"tier={tier_info['tier']}  reasons={'; '.join(tier_info['reasons'])}",
        f"consistency bar (95%) per bin: [{lo}, {hi}]  (E={n/bins:.1f})",
        "bin   count  " + "-" * width,
    ]
    for b, c in enumerate(hist):
        bar = "#" * int(round(width * c / peak)) if peak else ""
        mark = "  <-- outside bar" if (c < lo or c > hi) else ""
        lines.append(f"{b/bins:.1f}  {c:5d}  {bar}{mark}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=60)
    a = ap.parse_args(argv)
    PIT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"CALIBRATION GATE (candidate 43) — rolling {a.window}-day held-out PIT")
    print(f"{'station':16s} {'n':>4s}  {'tier':6s}  {'chi2':>7s} {'p':>7s}  reasons")
    any_red = False
    for name, fname in STREAMS.items():
        path = REPORTS / fname
        if not path.exists():
            print(f"{name:16s}    -  (missing {fname})")
            continue
        rows = _load(path)
        pits, win_dates = _leakfree_pits(rows, a.window)
        info = calibration_tier(pits, win_dates)
        f = info["flatness"]
        chi2 = f"{f['chi2']:.2f}" if f["chi2"] is not None else "-"
        pval = f"{f['pvalue']:.3f}" if f["pvalue"] is not None else "-"
        print(f"{name:16s} {len(pits):4d}  {info['tier']:6s}  {chi2:>7s} {pval:>7s}  "
              f"{'; '.join(info['reasons'])}")
        (PIT_DIR / f"{name}.txt").write_text(_plot(name, info, a.window))
        any_red = any_red or info["blocks_emit"]

    print(f"\nPIT plots saved to {PIT_DIR.relative_to(ROOT)}/")
    print("RED tiers present -> those stations REFUSE bucket-probability emission."
          if any_red else "No RED tiers -> all stations clear to emit bucket probabilities.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
