#!/usr/bin/env python3
"""Candidate 41 runner — replay each logged stream, rebuild the three leak-free
lineages (council / persistence / climatology), and score a HELD-OUT paired CRPS
of the inverse-CRPS BLEND against (a) the council alone and (b) the leak-free
best-single-pick, with the harness's own seeded paired bootstrap CI. Read-only.

    PYTHONPATH=. python3 tools/lineage_blend_run.py [--window 60]

Verdict per stream (recommend-only — never auto-bakes):
  * < 30 held-out scored days                    -> UNDERPOWERED
  * blend CI-lo vs BEST single > 0               -> BLEND IMPROVEMENT (recommend-only)
  * blend beats council but not the best single  -> BEATS-COUNCIL-ONLY
  * otherwise (CI includes 0 / favours a single) -> NO-BAKE

The honest expectation (one independently-logged NWP lineage; persistence and
climatology reconstructed from the obs) is that the council dominates and the
blend lands at — not below — the best single lineage: an informative NO-BAKE.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from weather_council.lineage_blend import walk_forward_blend                 # noqa: E402
from tools.daily_healthcheck import _paired_bootstrap_ci, BOOT_CI            # noqa: E402

REPORTS = ROOT / "reports"
STREAMS = {
    "london_high": "london_high.csv",
    "london_low": "london_low.csv",
    "hong_kong_high": "hong_kong_high.csv",
    "hong_kong_low": "hong_kong_low.csv",
}


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


def _verdict(res, lo_best):
    if res["n_test"] < 30:
        return "UNDERPOWERED"
    if lo_best is not None and lo_best > 0.0:
        return "BLEND IMPROVEMENT"
    # beats council out-of-sample but not the best single pick?
    pt_c, lo_c, _, _ = _paired_bootstrap_ci(res["deltas_council"])
    if lo_c is not None and lo_c > 0.0:
        return "BEATS-COUNCIL-ONLY"
    return "NO-BAKE"


def _mean_top_weight(weight_log):
    """Mean council weight across the held-out days (a quick read on how much the
    blend leans on the council vs the references)."""
    if not weight_log:
        return 0.0
    return sum(w.get("council", 0.0) for w in weight_log) / len(weight_log)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=60)
    a = ap.parse_args(argv)

    print(f"LINEAGE BLEND (candidate 41) — held-out paired CRPS, window={a.window}")
    print("inverse-CRPS blend of council / persistence / climatology vs council & best-single; "
          f"{int(BOOT_CI*100)}% paired bootstrap CI")
    print(f"{'station':16s} {'nTest':>5s} {'CRPSblend':>9s} {'CRPScoun':>9s} {'CRPSbest':>9s} "
          f"{'dVScoun':>8s} {'dVSbest':>8s} {'biLo':>7s} {'biHi':>7s} {'wCoun':>6s}  verdict")
    n_improve = 0
    for name, fname in STREAMS.items():
        path = REPORTS / fname
        if not path.exists():
            print(f"{name:16s}    -  (missing {fname})")
            continue
        rows = _load(path)
        res = walk_forward_blend(rows, window=a.window)
        if res["n_test"] == 0:
            print(f"{name:16s}    -  (no held-out scored days)")
            continue
        pt_c, _, _, _ = _paired_bootstrap_ci(res["deltas_council"])
        pt_b, lo_b, hi_b, _ = _paired_bootstrap_ci(res["deltas_best"])
        verdict = _verdict(res, lo_b)
        n_improve += verdict == "BLEND IMPROVEMENT"
        lo_s = f"{lo_b:+.4f}" if lo_b is not None else "   -  "
        hi_s = f"{hi_b:+.4f}" if hi_b is not None else "   -  "
        print(f"{name:16s} {res['n_test']:5d} {res['mean_crps_blend']:9.4f} "
              f"{res['mean_crps_council']:9.4f} {res['mean_crps_best']:9.4f} "
              f"{pt_c:+8.4f} {pt_b:+8.4f} {lo_s:>7s} {hi_s:>7s} "
              f"{_mean_top_weight(res['weights']):6.2f}  {verdict}")

    print()
    if n_improve:
        print(f"{n_improve} stream(s) show a HELD-OUT blend CRPS improvement over the BEST single "
              "lineage with CI excluding zero -> recommend-only (additive overlay); NEVER auto-baked.")
    else:
        print("No stream clears the gate (every blend-vs-best CI includes zero or favours a single "
              "lineage) -> NO-BAKE. With one independently-logged NWP lineage the council dominates; "
              "soft inverse-CRPS selection lands AT the best single, not below. Honest no-edge result "
              "(dVS>0 means blend lower CRPS). Value is robustness, not free skill.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
