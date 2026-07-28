#!/usr/bin/env python3
"""probe_sf_cli_scale.py — the FROZEN, PRE-REGISTERED, ONE-ATTEMPT probe of the
SF CLI-scale seam shift of the intraday sharpened pmf.

Prereg: ledger/preregistered/sf_cli_scale_intraday_pmf.md (frozen 2026-07-27
BEFORE any scoring — one attempt; fail any gate criterion -> dead ledger).
Context: 2026-07-27 KSFO, obs-scale modal 69°F served at 78-98% while the
settling CLISFO printed 70 via the 18-00Z 6-hourly catch (10211 = 21.1°C =
69.98°F, invisible to the hourly record).

PROBE (verbatim from the prereg / task spec — do not tune post-scoring):
  Walk-forward, strictly chronological, leak-free. For each day D with >= 30
  strictly-earlier days of history (earlier days skipped and counted) and each
  served hour H in 10..16:
    * remaining-rise sharpened pmf from ONLY days < D: the empirical
      distribution of (final_obs_max_f - running_max_f_by_H) over strictly-
      earlier days; candidate final max values = rm_H + each prior rise;
      quantize to whole °F via round-half-up (mirrors the shipped
      intraday_ceiling.sharpen_pmf resample).
    * ARM A (served status quo): bucket that pmf at obs scale
      (quantize rm_H + rise directly).
    * ARM B (candidate): first shift by the seam estimator = expanding-window
      MEAN of (cli_f - obs_max_f) over strictly-earlier days (quantize
      rm_H + seam_est + rise). seam_est uses ONLY days < D.
    * Score both arms against the ACTUAL CLI settle (cli_f, whole °F)
      aggregated onto the 2°F Kalshi market buckets (value v -> bucket lower
      2*floor(v/2)).

GATE (ALL required; any failure = candidate DEAD):
  C1: market-bucket hit rate (argmax 2°F bucket == actual CLI bucket) — arm B
      beats arm A on BOTH chronological halves (cells split by day at the
      median date).
  C2: log score of the 2°F bucket distribution (log p on the actual CLI
      bucket; probabilities floored at 1e-6) — arm B beats arm A on BOTH
      halves.
  C3 (design check, report only): the shift applies to the pmf, never to the
      banked floor — verified in code (sharpened_pmf returns rm untouched;
      every cell asserts it).
  C4: driver alive at probe time — the seam series (CLI - obs catch) mean is
      positive and sign-stable across BOTH chronological halves.

Data layer (loaders, build_dataset with the |catch|>5°F truth quarantine,
SERVED_HOURS, _round_half_up) is REUSED from tools/mc_verdict_sim.py — the
16 truth-artifact days stay excluded.

Run:      PYTHONPATH=. python3 tools/probe_sf_cli_scale.py
Self-test: PYTHONPATH=. python3 tools/probe_sf_cli_scale.py --selftest
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path

from tools.mc_verdict_sim import (SERVED_HOURS, _chrono_halves, _round_half_up,
                                  build_dataset, driver_stats, load_cli_truth,
                                  load_obs_archive)

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "probe_sf_cli_scale_2026-07-27.json"
PREREG_PATH = "ledger/preregistered/sf_cli_scale_intraday_pmf.md"

MIN_HISTORY_DAYS = 30      # a day is scored only with >= 30 strictly-earlier days
LOG_FLOOR = 1e-6           # C2 probability floor


# ------------------------------------------------------------- probe math

def market_bucket(v: float) -> int:
    """The 2°F inclusive Kalshi bucket lower edge: v -> 2*floor(v/2)."""
    return 2 * math.floor(v / 2)


def sharpened_pmf(rm_f: float, rises: list[float], shift_f: float = 0.0):
    """The remaining-rise sharpened whole-°F pmf: resample the empirical rise
    cloud onto today's running max (PLUS the arm-B seam shift) and quantize
    each draw round-half-up — the shipped intraday_ceiling.sharpen_pmf
    resample, °F grain. Deterministic full enumeration, no RNG.

    C3 (design check): the shift is applied INSIDE the quantize input only;
    the banked running max is taken by value and returned untouched so the
    caller can verify the floor was never altered."""
    counts: dict[int, int] = {}
    for r in rises:
        v = _round_half_up(rm_f + shift_f + r)
        counts[v] = counts.get(v, 0) + 1
    n = len(rises)
    return {v: c / n for v, c in counts.items()}, rm_f


def bucket_pmf(pmf: dict[int, float]) -> dict[int, float]:
    """Aggregate a whole-°F pmf onto the 2°F market buckets."""
    out: dict[int, float] = {}
    for v, p in pmf.items():
        b = market_bucket(v)
        out[b] = out.get(b, 0.0) + p
    return out


def argmax_bucket(bpmf: dict[int, float]) -> int:
    """The served call: highest-probability 2°F bucket. Deterministic
    tie-break: the LOWEST bucket among ties (never data-dependent)."""
    best = max(bpmf.values())
    return min(b for b, p in bpmf.items() if p == best)


def score_cell(bpmf: dict[int, float], cli_f: float) -> tuple[bool, float]:
    """(bucket hit, log score) of a 2°F bucket pmf against the actual CLI
    settle. Log score floored at LOG_FLOOR per the frozen criterion."""
    actual = market_bucket(cli_f)
    hit = argmax_bucket(bpmf) == actual
    return hit, math.log(max(bpmf.get(actual, 0.0), LOG_FLOOR))


# ------------------------------------------------------------- walk-forward

def probe_cells(days: list[dict]) -> tuple[list[dict], int]:
    """The leak-free walk-forward. Day i is scored only when i >=
    MIN_HISTORY_DAYS; the seam estimator and every rise sample come from
    days[:i] ONLY — day i's own row is appended to history AFTER its cells
    are built. Returns (cells, n_skipped_early_days)."""
    hist_rises: dict[int, list[float]] = {h: [] for h in SERVED_HOURS}
    catch_sum = 0.0
    cells: list[dict] = []
    n_skipped = 0
    for i, day in enumerate(days):
        if i < MIN_HISTORY_DAYS:
            n_skipped += 1
        else:
            seam_est = catch_sum / i          # expanding mean, days < D only
            for h in SERVED_HOURS:
                rm_f = day["rm"][h]
                pmf_a, rm_after_a = sharpened_pmf(rm_f, hist_rises[h], 0.0)
                pmf_b, rm_after_b = sharpened_pmf(rm_f, hist_rises[h], seam_est)
                # C3 in code: the shift never alters the banked floor.
                assert rm_after_a == rm_after_b == rm_f
                bp_a, bp_b = bucket_pmf(pmf_a), bucket_pmf(pmf_b)
                hit_a, ls_a = score_cell(bp_a, day["cli_f"])
                hit_b, ls_b = score_cell(bp_b, day["cli_f"])
                cells.append({
                    "date": day["date"], "hour": h, "rm_f": rm_f,
                    "seam_est": seam_est, "cli_f": day["cli_f"],
                    "pmf_a": pmf_a, "pmf_b": pmf_b,
                    "hit_a": hit_a, "hit_b": hit_b,
                    "log_a": ls_a, "log_b": ls_b,
                })
        # history update happens strictly AFTER day i's cells are built
        catch_sum += day["catch_f"]
        for h in SERVED_HOURS:
            hist_rises[h].append(day["obs_max_f"] - day["rm"][h])
    return cells, n_skipped


# ------------------------------------------------------------- gate scoring

def _arm_stats(cells: list[dict], arm: str) -> dict:
    n = len(cells)
    if not n:
        return {"n": 0, "bucket_hit_rate": None, "mean_log_score": None}
    return {"n": n,
            "bucket_hit_rate": round(sum(c[f"hit_{arm}"] for c in cells) / n, 6),
            "mean_log_score": round(sum(c[f"log_{arm}"] for c in cells) / n, 6)}


def _block(cells: list[dict]) -> dict:
    a, b = _arm_stats(cells, "a"), _arm_stats(cells, "b")
    d_hit = (b["bucket_hit_rate"] - a["bucket_hit_rate"]) if cells else None
    d_log = (b["mean_log_score"] - a["mean_log_score"]) if cells else None
    return {"arm_A": a, "arm_B": b,
            "delta_hit_B_minus_A": round(d_hit, 6) if d_hit is not None else None,
            "delta_log_B_minus_A": round(d_log, 6) if d_log is not None else None}


def evaluate(cells: list[dict], days: list[dict]) -> dict:
    """The frozen gate. Halves split by day at the median scored date (the
    mc_verdict_sim chronological-halves rule). C3 is a report-only design
    check verified in code (see probe_cells); C1/C2 require arm B strictly
    better on BOTH halves; C4 requires the catch mean positive and
    sign-stable across both chronological halves of the probe dataset."""
    h1, h2 = _chrono_halves(cells, "date")
    halves = [_block(h1), _block(h2)]
    drv = driver_stats(days)
    c1 = all(h["delta_hit_B_minus_A"] > 0 for h in halves)
    c2 = all(h["delta_log_B_minus_A"] > 0 for h in halves)
    rm_by_date = {d["date"]: d["rm"] for d in days}
    c3 = all(c["rm_f"] == rm_by_date[c["date"]][c["hour"]] for c in cells)
    c4 = (drv["all"]["mean"] is not None and drv["all"]["mean"] > 0
          and drv["h1"]["mean"] is not None and drv["h1"]["mean"] > 0
          and drv["h2"]["mean"] is not None and drv["h2"]["mean"] > 0)
    failed = [name for name, ok in (("C1", c1), ("C2", c2), ("C4", c4))
              if not ok]
    return {"pooled": _block(cells),
            "half1": halves[0], "half2": halves[1],
            "half_split": "chronological by day at the median scored date",
            "gate": {"C1_bucket_hit_both_halves": c1,
                     "C2_log_score_both_halves": c2,
                     "C3_shift_never_touches_floor_design_check": c3,
                     "C4_driver_alive_sign_stable": c4,
                     "failed": failed,
                     "verdict": "DEAD" if failed else "PASS"},
            "driver": {"catch_mean_all": drv["all"]["mean"],
                       "catch_mean_h1": drv["h1"]["mean"],
                       "catch_mean_h2": drv["h2"]["mean"],
                       "p_catch_gt_0_all": drv["all"]["p_catch_gt_0"],
                       "bucket_cross_rate": drv["bucket_cross_rate"]}}


# ------------------------------------------------------------- report

def run_probe(write: Path = REPORT_PATH) -> dict:
    obs = load_obs_archive()
    cli = load_cli_truth()
    days, quarantined = build_dataset(obs, cli)
    cells, n_skipped = probe_cells(days)
    result = evaluate(cells, days)
    report = {
        "probe": "sf_cli_scale_intraday_pmf",
        "prereg": PREREG_PATH,
        "frozen": "2026-07-27, BEFORE scoring — ONE registered attempt",
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "station": "KSFO",
        "spec": {
            "served_hours": SERVED_HOURS,
            "min_history_days": MIN_HISTORY_DAYS,
            "seam_estimator": "expanding-window mean of (cli_f - obs_max_f) "
                              "over strictly-earlier days",
            "quantize": "round-half-up whole °F (mirrors intraday_ceiling "
                        "sharpen_pmf)",
            "bucket": "2°F inclusive, v -> 2*floor(v/2)",
            "log_floor": LOG_FLOOR,
            "argmax_tie_break": "lowest bucket (deterministic)",
        },
        "dataset": {"n_days": len(days),
                    "span": [days[0]["date"], days[-1]["date"]],
                    "n_quarantined_truth": len(quarantined),
                    "quarantined_dates": [q["date"] for q in quarantined],
                    "n_skipped_early_days": n_skipped,
                    "n_cells": len(cells)},
        **result,
    }
    txt = json.dumps(report, indent=2)
    write.write_text(txt + "\n")
    return report


def print_summary(report: dict) -> None:
    g = report["gate"]
    print(f"PROBE sf_cli_scale_intraday_pmf — {report['frozen']}")
    d = report["dataset"]
    print(f"dataset: {d['n_days']} days {d['span'][0]}..{d['span'][1]}, "
          f"{d['n_quarantined_truth']} truth-quarantined, "
          f"{d['n_skipped_early_days']} early days skipped, "
          f"{d['n_cells']} day×hour cells")
    drv = report["driver"]
    print(f"C4 driver: catch mean {drv['catch_mean_all']:+.3f}°F "
          f"(halves {drv['catch_mean_h1']:+.3f} / {drv['catch_mean_h2']:+.3f}), "
          f"P(catch>0)={drv['p_catch_gt_0_all']}, "
          f"bucket-cross rate={drv['bucket_cross_rate']}")
    print(f"{'block':<8} {'n':>6} {'hitA':>8} {'hitB':>8} {'Δhit':>8} "
          f"{'logA':>9} {'logB':>9} {'Δlog':>8}")
    for name in ("half1", "half2", "pooled"):
        b = report[name]
        print(f"{name:<8} {b['arm_A']['n']:>6} "
              f"{b['arm_A']['bucket_hit_rate']:>8.4f} "
              f"{b['arm_B']['bucket_hit_rate']:>8.4f} "
              f"{b['delta_hit_B_minus_A']:>+8.4f} "
              f"{b['arm_A']['mean_log_score']:>9.4f} "
              f"{b['arm_B']['mean_log_score']:>9.4f} "
              f"{b['delta_log_B_minus_A']:>+8.4f}")
    print(f"GATE: C1(bucket hit, both halves)={g['C1_bucket_hit_both_halves']}  "
          f"C2(log score, both halves)={g['C2_log_score_both_halves']}  "
          f"C3(floor untouched, design check)="
          f"{g['C3_shift_never_touches_floor_design_check']}  "
          f"C4(driver alive)={g['C4_driver_alive_sign_stable']}")
    verdict = g["verdict"]
    tail = (f" — failed: {', '.join(g['failed'])}" if g["failed"]
            else " — all criteria met")
    print(f"VERDICT: {verdict}{tail}")


# ------------------------------------------------------------- selftest

def _mk_day(d, rm, om, cl):
    return {"date": d, "obs_max_f": om, "cli_f": cl, "catch_f": cl - om,
            "rm": {h: rm for h in SERVED_HOURS}}


def _selftest() -> int:
    # Bucket / quantize known answers
    assert market_bucket(69) == 68 and market_bucket(70) == 70
    assert market_bucket(71.9) == 70 and market_bucket(68.0) == 68
    pmf, rm_back = sharpened_pmf(60.4, [0.0, 0.2, 1.0])
    assert pmf == {60: 1 / 3, 61: 2 / 3} and rm_back == 60.4
    pmf_s, rm_back = sharpened_pmf(60.4, [0.0, 0.2, 1.0], shift_f=1.0)
    assert pmf_s == {61: 1 / 3, 62: 2 / 3} and rm_back == 60.4   # C3: rm intact
    assert argmax_bucket({68: 0.4, 70: 0.4, 72: 0.2}) == 68       # tie -> lowest

    # KAT 1: synthetic dataset where arm B is constructed to win -> gate PASS.
    # rise = 0 always, catch = +2 always: arm A serves bucket 60, arm B serves
    # bucket 62 = the actual CLI bucket, on every cell of both halves.
    days = [_mk_day(f"2026-01-{i:02d}", 60.0, 60.0, 62.0) for i in range(1, 41)]
    cells, skipped = probe_cells(days)
    assert skipped == MIN_HISTORY_DAYS
    assert len(cells) == 10 * len(SERVED_HOURS)
    res = evaluate(cells, days)
    assert res["gate"]["verdict"] == "PASS", res["gate"]
    assert res["pooled"]["arm_B"]["bucket_hit_rate"] == 1.0
    assert res["pooled"]["arm_A"]["bucket_hit_rate"] == 0.0
    assert res["pooled"]["delta_log_B_minus_A"] > 13.0   # 0 - log(1e-6)

    # KAT 2: zero seam -> arm B identical to arm A, deltas exactly zero -> DEAD.
    days = [_mk_day(f"2026-01-{i:02d}", 60.0, 60.0, 60.0) for i in range(1, 41)]
    cells, _ = probe_cells(days)
    for c in cells:
        assert c["pmf_a"] == c["pmf_b"]
    res = evaluate(cells, days)
    assert res["pooled"]["delta_hit_B_minus_A"] == 0.0
    assert res["pooled"]["delta_log_B_minus_A"] == 0.0
    assert res["gate"]["verdict"] == "DEAD"
    assert "C1" in res["gate"]["failed"] and "C2" in res["gate"]["failed"]

    # KAT 3: leak-freeness — day D's own row must never touch day D's cell.
    # Days 0..29: rise 0, catch 0. Day 30: rise 4, catch 40 (loud marker).
    # Day 31: rise 0, catch 0.
    days = [_mk_day(f"2026-01-{i:02d}", 60.0, 60.0, 60.0) for i in range(1, 31)]
    days.append(_mk_day("2026-01-31", 60.0, 64.0, 104.0))   # catch +40 marker
    days.append(_mk_day("2026-02-01", 60.0, 60.0, 60.0))
    cells, _ = probe_cells(days)
    c30 = [c for c in cells if c["date"] == "2026-01-31"]
    c31 = [c for c in cells if c["date"] == "2026-02-01"]
    assert len(c30) == len(c31) == len(SERVED_HOURS)
    for c in c30:
        # seam_est from days<30 only = 0.0; rises from days<30 only = all 0
        assert c["seam_est"] == 0.0
        assert c["pmf_a"] == c["pmf_b"] == {60: 1.0}
    for c in c31:
        # day 30 IS strictly earlier than day 31: seam = 40/31, rise 4 enters
        assert abs(c["seam_est"] - 40.0 / 31) < 1e-12
        assert abs(c["pmf_a"].get(64, 0.0) - 1.0 / 31) < 1e-12
        # pmf_b: rises 0 (x30) and 4 (x1), shift 40/31:
        #   round(60+40/31)=61 (x30), round(64+40/31)=65 (x1)
        assert abs(c["pmf_b"][61] - 30 / 31) < 1e-12
        assert abs(c["pmf_b"][65] - 1 / 31) < 1e-12

    # KAT 4: C3 — on real-shaped cells the banked floor equals the day's rm.
    days = [_mk_day(f"2026-01-{i:02d}", 60.0 + i * 0.1, 61.0 + i * 0.1,
                    62.0 + i * 0.1) for i in range(1, 41)]
    cells, _ = probe_cells(days)
    res = evaluate(cells, days)
    assert res["gate"]["C3_shift_never_touches_floor_design_check"] is True

    print("probe_sf_cli_scale selftest PASS (bucket/quantize KAs, arm-B-wins "
          "gate PASS, zero-seam zero-delta DEAD, leak-freeness, C3 floor check)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Frozen one-attempt probe: SF "
                                             "CLI-scale seam shift of the intraday pmf.")
    ap.add_argument("--write", default=str(REPORT_PATH),
                    help="JSON report path (default: the frozen report file)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    report = run_probe(Path(args.write))
    print_summary(report)
    print(f"written: {args.write}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
