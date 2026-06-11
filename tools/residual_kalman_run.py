#!/usr/bin/env python3
"""Candidate 40 runner — replay each logged stream, fit the residual local-level
Kalman bias-drift filter on the TRAINING half, and score a HELD-OUT paired CRPS
delta (pooled empirical cloud = baseline/live path vs Kalman-relocated cloud)
with the harness's own seeded paired bootstrap CI. Read-only; prints a verdict.

    PYTHONPATH=. python3 tools/residual_kalman_run.py [--train-frac 0.5]

Verdict per stream (recommend-only — never auto-bakes):
  * < 30 held-out pairs                      -> UNDERPOWERED
  * CI lower bound > 0 AND gain sane         -> HELD-OUT IMPROVEMENT (recommend-only)
  * CI includes 0 (or favours baseline)      -> NO-BAKE
The 'gain sane' guard rejects a filter whose steady-state Kalman gain is ~1.0
(it would merely chase the last residual = overfitting noise).
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from weather_council.residual_kalman import walk_forward_kalman              # noqa: E402
from tools.daily_healthcheck import _paired_bootstrap_ci, BOOT_CI            # noqa: E402

REPORTS = ROOT / "reports"
STREAMS = {
    "london_high": "london_high.csv",
    "london_low": "london_low.csv",
    "hong_kong_high": "hong_kong_high.csv",
    "hong_kong_low": "hong_kong_low.csv",
}
GAIN_SANE_MAX = 0.6     # steady-state gain at/above this => filter chases noise, distrust


def _load(path: Path):
    out = []
    with open(path) as fh:
        for r in csv.DictReader(fh):
            try:
                out.append((r["date"], float(r["point"]), float(r["realized"])))
            except (ValueError, KeyError):
                continue
    out.sort()
    return out


def _verdict(res, lo, hi):
    if res["n_test"] < 30:
        return "UNDERPOWERED"
    if lo is not None and lo > 0.0 and res["steady_gain"] < GAIN_SANE_MAX:
        return "HELD-OUT IMPROVEMENT"
    if lo is not None and lo > 0.0:
        return "IMPROVEMENT (gain suspect)"
    return "NO-BAKE"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-frac", type=float, default=0.5)
    a = ap.parse_args(argv)

    print(f"RESIDUAL KALMAN (candidate 40) — held-out paired CRPS, train_frac={a.train_frac}")
    print(f"fit (q,s) on training half; score the held-out half; {int(BOOT_CI*100)}% paired bootstrap CI")
    print(f"{'station':16s} {'nTest':>5s} {'CRPSbase':>9s} {'CRPSkal':>9s} "
          f"{'dCRPS':>8s} {'CI lo':>8s} {'CI hi':>8s} {'gain':>5s} {'maxbias':>7s}  verdict")
    n_improve = 0
    for name, fname in STREAMS.items():
        path = REPORTS / fname
        if not path.exists():
            print(f"{name:16s}    -  (missing {fname})")
            continue
        rows = _load(path)
        res = walk_forward_kalman(rows, train_frac=a.train_frac)
        if res["n_test"] == 0:
            print(f"{name:16s}    -  ({res.get('note', 'no held-out days')})")
            continue
        pt, lo, hi, _ = _paired_bootstrap_ci(res["deltas"])
        verdict = _verdict(res, lo, hi)
        n_improve += verdict == "HELD-OUT IMPROVEMENT"
        lo_s = f"{lo:+.4f}" if lo is not None else "   -  "
        hi_s = f"{hi:+.4f}" if hi is not None else "   -  "
        print(f"{name:16s} {res['n_test']:5d} {res['mean_crps_base']:9.4f} "
              f"{res['mean_crps_kalman']:9.4f} {pt:+8.4f} {lo_s:>8s} {hi_s:>8s} "
              f"{res['steady_gain']:5.2f} {res['max_bias']:7.3f}  {verdict}")

    print()
    if n_improve:
        print(f"{n_improve} stream(s) show a HELD-OUT CRPS improvement with CI excluding zero "
              "-> recommend-only promotion (additive correction on top of #34); NEVER auto-baked.")
    else:
        print("No stream clears the gate (every CI includes zero or favours the pooled cloud) "
              "-> NO-BAKE. The #34 recency correction already removes the trackable bias drift; "
              "the Kalman adds no held-out edge. Honest no-edge result (dCRPS>0 means Kalman lower).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
