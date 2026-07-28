#!/usr/bin/env python3
"""mc_verdict_sim.py — Monte Carlo + 10y backtest validation of the SF intraday
CLI-seam guard, run THROUGH the shipped code (run._cli_seam_guard_lines).

Born 2026-07-27 (obs modal 69°F served at 78-98% while the CLI paid 70 via the
18-00Z catch): the guard ships as labeling — this harness is the evidence layer
that its vocabulary fires when it should and stays quiet when it should. It does
NOT score the frozen seam-shift probe (ledger/preregistered/sf_cli_scale_intraday_pmf.md
— one attempt, separate data assembly); it validates the GUARD's warning behavior
and measures the 10y driver the prereg's kill condition watches.

Three layers, all leak-free and deterministic:
  1. DRIVER MEASUREMENT (10y, real): obs-scale daily max (data/ksfo_hourly_iem.jsonl)
     vs CLI high (IEM parsed-CLI, cached data/ksfo_cli_iem_10y.jsonl) → the catch
     distribution (CLI − obs max), sign-stability across chronological halves
     (the prereg kill-condition input).
  2. REAL-DATA BACKTEST: at each day × served hour H∈[10,16], reconstruct the
     guard's anchor = max(running max by H, modal proxy) with the modal proxy
     rm(H) + median remaining-rise learned from STRICTLY-EARLIER days, the seam
     estimator = expanding mean of strictly-earlier catches (fallback 2.0°F
     below n=30, mirroring the shipped unlogged fallback), fire the shipped
     distance predicate, and score vs the actual CLI settle: did the bucket
     ABOVE the anchor's market bucket pay? Report recall / precision / warning
     rate, both chronological halves, and the old parity rule as the baseline.
  3. MONTE CARLO: bootstrap (seeded) resample of real days × hours, synthetic
     modal = rm(H) + median-rise + empirical residual, IntradayCeiling objects
     pushed through run._cli_seam_guard_lines ITSELF (not a reimplementation) —
     validates the served code path end-to-end at scale.

Run:      PYTHONPATH=. python3 tools/mc_verdict_sim.py [--sims 20000] [--seed 20260727] [--write reports/mc_verdict_sim_$(date +%F).json]
Self-test: PYTHONPATH=. python3 tools/mc_verdict_sim.py --selftest
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import random
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OBS_ARCHIVE = ROOT / "data" / "ksfo_hourly_iem.jsonl"
CLI_CACHE = ROOT / "data" / "ksfo_cli_iem_10y.jsonl"

SERVED_HOURS = list(range(10, 17))     # the window the intraday read serves
FALLBACK_CATCH_F = 2.0                  # mirrors run._cli_seam_guard_lines
MIN_SEAM_SAMPLES = 30


# ---------------------------------------------------------------- data layer

def load_obs_archive(path: Path = OBS_ARCHIVE) -> dict:
    """{date_iso: [(hour, celsius), ...]} — screened (finegrain_read._screen_obs
    rule: malformed / out-of-band rows dropped before any statistic)."""
    out = {}
    with open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            obs = [(h, c) for h, c in (r.get("obs") or [])
                   if isinstance(c, (int, float)) and not isinstance(c, bool)
                   and -30.0 <= c <= 55.0]
            if len(obs) >= 20:
                out[r["date"]] = obs
    return out


def load_cli_truth(path: Path = CLI_CACHE) -> dict:
    """{date_iso: high_f} from the cached IEM parsed-CLI pull; fetches and caches
    on first use (one request per year — the cache makes reruns offline)."""
    if path.exists():
        return {r["date"]: r["high_f"] for r in
                (json.loads(l) for l in path.read_text().splitlines() if l.strip())}
    from weather_council.sources import Sources
    s = Sources()
    start_year = min(load_obs_archive())[:4]
    end = dt.date.today() - dt.timedelta(days=1)
    out = {}
    for year in range(int(start_year), end.year + 1):
        cli = s.nws_cli_daily("KSFO", dt.date(year, 1, 1),
                              dt.date(year, 12, 31))
        for d, row in cli.items():
            if row.get("high_f") is not None:
                out[d] = row["high_f"]
    with open(path, "w") as f:
        for d in sorted(out):
            f.write(json.dumps({"date": d, "high_f": out[d]}) + "\n")
    return out


def build_dataset(obs: dict, cli: dict) -> tuple[list[dict], list[dict]]:
    """Chronological day records: running max °F by served hour, obs final max °F,
    CLI high °F, catch = CLI − obs. Only days with both records AND full diurnal
    coverage — a day whose obs end before 21:00 can have its true max truncated
    away. Separately QUARANTINED: days with |catch| > 5°F. The 6-hourly catch is
    physically bounded (~3°F per the register bounds); the 2026-07-27 audit found
    the >5°F tail to be truth-source artifacts, not weather (2018-07-06: obs 84 /
    CLI 95 with no 95 in any neighboring obs day; 2018-10-13's CLI 78 matches
    10-12's OBS 78 — station-mix / day-shift rows in the parsed archive). These
    days are excluded from every statistic and reported, pending the direct
    CLISFO product check (tools/verify_cli_archive.py — the S2 rule-2 path).
    Returns (clean_days, quarantined_days)."""
    days, n_trunc, quarantined = [], 0, []
    for d in sorted(obs):
        if d not in cli:
            continue
        # Screen before ANY statistic touches the rows (same rule as
        # finegrain_read._screen_obs) — build_dataset is called with already-
        # screened rows by load_obs_archive, but the statistic layer must not
        # trust its caller (defense in depth; the 999°C-row KAT pins this).
        rows = [(h, c) for h, c in obs[d]
                if isinstance(c, (int, float)) and not isinstance(c, bool)
                and -30.0 <= c <= 55.0]
        if len(rows) < 20:
            continue
        if max(h for h, _ in rows) < 21.0:
            n_trunc += 1
            continue
        rm = {}
        for h in SERVED_HOURS:
            by_h = [c for hh, c in rows if hh <= h]
            if by_h:
                rm[h] = max(by_h) * 9 / 5 + 32
        if len(rm) < len(SERVED_HOURS):
            continue                       # day starts too late to serve
        obs_max_f = max(c for _, c in rows) * 9 / 5 + 32
        day = {"date": d, "rm": rm, "obs_max_f": obs_max_f,
               "cli_f": float(cli[d]), "catch_f": float(cli[d]) - obs_max_f}
        if abs(day["catch_f"]) > 5.0:
            quarantined.append(day)
        else:
            days.append(day)
    return days, quarantined


# ------------------------------------------------------- guard under test

def _top_boundary(anchor_f: float) -> float:
    """run._market_bucket_top_boundary, mirrored for the backtest half (the MC
    half calls the SHIPPED function — see mc_layer)."""
    return 2 * math.floor(anchor_f / 2) + 1.5


def guard_fires(anchor_f: float, hour: int, seam_f: float) -> bool:
    """The shipped distance predicate (run._cli_seam_guard_lines, 2026-07-27
    stress revision): warn iff the anchor sits within the catch estimate of the
    top of its 2°F market bucket before the 18-00Z group (~17:00 local)."""
    if hour >= 17:
        return False
    return (_top_boundary(anchor_f) - anchor_f) < seam_f


def parity_fires(modal_bucket: int, hour: int) -> bool:
    """The ORIGINAL parity rule (odd modal = top of bucket) — the baseline the
    distance rule replaced; kept here so the backtest quantifies the upgrade."""
    return hour < 17 and modal_bucket % 2 == 1


def _round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


# -------------------------------------------------- layer 2: real backtest

def backtest(days: list[dict]) -> dict:
    """Leak-free walk-forward over real days. Modal proxy = rm(H) + median
    remaining rise from strictly-earlier days; seam estimator = expanding mean
    of strictly-earlier catches (fallback 2.0°F below 30 samples). Outcome:
    the CLI paid the bucket ABOVE the anchor's market bucket."""
    recs = []
    for i, day in enumerate(days):
        hist = days[:i]
        rises = {h: [hd["obs_max_f"] - hd["rm"][h] for hd in hist] for h in SERVED_HOURS}
        catches = [hd["catch_f"] for hd in hist]
        seam_est = (statistics.mean(catches[-30:]) if len(catches) >= MIN_SEAM_SAMPLES
                    else FALLBACK_CATCH_F)
        for h in SERVED_HOURS:
            rm_f = day["rm"][h]
            med_rise = statistics.median(rises[h]) if rises[h] else 2.0
            modal = _round_half_up(rm_f + med_rise)
            anchor = max(rm_f, modal)
            boundary = _top_boundary(anchor)
            recs.append({
                "date": day["date"], "hour": h, "anchor": anchor,
                "modal": modal, "seam_est": seam_est,
                "warn_distance": guard_fires(anchor, h, seam_est),
                "warn_parity": parity_fires(modal, h),
                "above_pays": day["cli_f"] > boundary,
            })
    return {"overall": _score(recs, "warn_distance"),
            "parity_baseline": _score(recs, "warn_parity"),
            "halves": [_score(r, "warn_distance")
                       for r in _chrono_halves(recs, "date")],
            "by_band": {"h10_13": _score([r for r in recs if r["hour"] <= 13],
                                         "warn_distance"),
                        "h14_16": _score([r for r in recs if r["hour"] >= 14],
                                         "warn_distance")},
            "n_cells": len(recs)}


def _chrono_halves(recs: list[dict], key: str) -> list[list[dict]]:
    days = sorted({r[key] for r in recs})
    mid = days[len(days) // 2]
    return ([r for r in recs if r[key] < mid],
            [r for r in recs if r[key] >= mid])


def _score(recs: list[dict], flag: str) -> dict:
    n = len(recs)
    warned = [r for r in recs if r[flag]]
    hits = [r for r in recs if r["above_pays"]]
    caught = [r for r in warned if r["above_pays"]]
    return {"n": n, "warn_rate": round(len(warned) / n, 4) if n else None,
            "above_pay_rate": round(len(hits) / n, 4) if n else None,
            "recall": round(len(caught) / len(hits), 4) if hits else None,
            "precision": round(len(caught) / len(warned), 4) if warned else None,
            "n_above_pays": len(hits), "n_warned": len(warned),
            "n_caught": len(caught)}


# ---------------------------------------------------- layer 3: Monte Carlo

def mc_layer(days: list[dict], sims: int, seed: int) -> dict:
    """Bootstrap MC through the SHIPPED guard (run._cli_seam_guard_lines on real
    IntradayCeiling objects — the served code path, not a reimplementation).
    Synthetic modal = rm(H) + median-rise(hour) + bootstrapped empirical
    residual; the seam figure is the live ledger's (what production serves)."""
    import run as _run
    from types import SimpleNamespace
    from weather_council.intraday_ceiling import IntradayCeiling

    rng = random.Random(seed)
    rises = {h: [d["obs_max_f"] - d["rm"][h] for d in days] for h in SERVED_HOURS}
    med = {h: statistics.median(rises[h]) for h in SERVED_HOURS}
    resid = {h: [r - med[h] for r in rises[h]] for h in SERVED_HOURS}
    seam = _run._load_cli_seam("KSFO")
    seam_f = abs(seam["mean"]) if seam else FALLBACK_CATCH_F

    n_warn = n_above = n_caught = 0
    for _ in range(sims):
        d = days[rng.randrange(len(days))]
        h = SERVED_HOURS[rng.randrange(len(SERVED_HOURS))]
        rm_f = d["rm"][h]
        modal = _round_half_up(rm_f + med[h] + resid[h][rng.randrange(len(resid[h]))])
        c = IntradayCeiling(
            kind="sharpened", city="San Francisco", target=d["date"],
            sub_degree=False, grain="F", hour=h,
            running_max_c=(rm_f - 32) * 5 / 9, n_rise=160,
            pmf=((modal, 0.9), (modal + 1, 0.1)),
            modal_bucket=modal, modal_prob=0.9, source="mc")
        warned = any("⚠" in l for l in _run._cli_seam_guard_lines(c))
        anchor = max(rm_f, modal)
        above = d["cli_f"] > _top_boundary(anchor)
        n_warn += warned
        n_above += above
        n_caught += warned and above
    return {"sims": sims, "seed": seed, "seam_f_used": seam_f,
            "warn_rate": round(n_warn / sims, 4),
            "above_pay_rate": round(n_above / sims, 4),
            "recall": round(n_caught / n_above, 4) if n_above else None,
            "precision": round(n_caught / n_warn, 4) if n_warn else None,
            "path": "run._cli_seam_guard_lines (shipped)"}


# ------------------------------------------------------------- report

def driver_stats(days: list[dict]) -> dict:
    """The 10y catch distribution — the prereg kill-condition input (the driver
    dies if the catch is not positive and sign-stable across halves). Also the
    BUCKET-cross rate: how often the CLI settle lands in a higher 2°F market
    bucket than the obs-scale final max (the day-type the guard exists for)."""
    catches = [d["catch_f"] for d in days]
    h1, h2 = _chrono_halves(days, "date")
    bucket = lambda f: 2 * math.floor(f / 2)
    cross = [d for d in days if bucket(d["cli_f"]) > bucket(d["obs_max_f"])]
    def _stats(xs):
        return {"n": len(xs), "mean": round(statistics.mean(xs), 3),
                "p_catch_gt_0": round(sum(1 for x in xs if x > 0.05) / len(xs), 4),
                "p50": round(statistics.median(xs), 2),
                "p90": round(sorted(xs)[int(0.9 * (len(xs) - 1))], 2),
                "max": round(max(xs), 2)}
    return {"all": _stats(catches),
            "h1": _stats([d["catch_f"] for d in h1]),
            "h2": _stats([d["catch_f"] for d in h2]),
            "bucket_cross_rate": round(len(cross) / len(days), 4),
            "note": "CLI − hourly-obs daily max (°F); >=0 by mechanism (6-hourly "
                    "groups). Sign-stable positive across halves = driver alive."}


def main() -> int:
    ap = argparse.ArgumentParser(description="MC + 10y backtest of the SF CLI-seam guard.")
    ap.add_argument("--sims", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=20260727)
    ap.add_argument("--write", default=None, help="also write the JSON report here")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()

    obs = load_obs_archive()
    cli = load_cli_truth()
    days, quarantined = build_dataset(obs, cli)
    report = {"generated": dt.datetime.now().isoformat(timespec="seconds"),
              "station": "KSFO", "n_days": len(days),
              "n_quarantined_truth": len(quarantined),
              "quarantined_days": [{"date": q["date"], "obs_max_f": round(q["obs_max_f"], 1),
                                    "cli_f": q["cli_f"], "catch_f": round(q["catch_f"], 2)}
                                   for q in quarantined],
              "span": [days[0]["date"], days[-1]["date"]],
              "caveats": [
                  "backtest anchor is IEM-obs-scale (10y catch mean ~0.88°F); "
                  "the PRODUCTION anchor is WU-scale (CLI−WU seam 1.27°F) — the "
                  "backtest validates predicate mechanics, the MC layer validates "
                  "the shipped path with the shipped seam",
                  "obs archive right edge: the 10y hourly file ends 2026-07-05 "
                  "(backfill is the launchd spine's duty) — recent days excluded",
                  "the frozen seam-shift probe (sf_cli_scale_intraday_pmf.md) is "
                  "NOT scored here — this validates the labeling guard only",
                  "|catch| > 5°F days are quarantined as truth-source artifacts "
                  "(physically implausible as between-obs spikes; resolution path "
                  "= tools/verify_cli_archive.py direct-product check)",
              ],
              "driver": driver_stats(days),
              "backtest": backtest(days),
              "monte_carlo": mc_layer(days, args.sims, args.seed)}
    txt = json.dumps(report, indent=2)
    print(txt)
    if args.write:
        Path(args.write).write_text(txt + "\n")
        print(f"written: {args.write}")
    return 0


def _selftest() -> int:
    # Predicate known-answers (mirror of the shipped distance rule)
    assert guard_fires(69.1, 15, 1.27) is True        # 0.4 < seam -> warn
    assert guard_fires(68.0, 15, 1.27) is False       # 1.5 > seam -> quiet
    assert guard_fires(68.9, 14, 1.27) is True        # even modal, high in bucket
    assert guard_fires(69.1, 17, 1.27) is False       # post-group: quiet
    assert parity_fires(69, 15) is True and parity_fires(68, 15) is False
    # Synthetic two-day dataset: deterministic known answers through the scorers
    mk = lambda d, rm, om, cl: {"date": d, "obs_max_f": om, "cli_f": cl,
                                "catch_f": cl - om,
                                "rm": {h: rm for h in SERVED_HOURS}}
    days = [mk("2026-01-01", 60.0, 62.0, 62.0),
            mk("2026-01-02", 69.1, 69.1, 70.0)]     # catch pays the bucket above
    bt = backtest(days)
    assert bt["n_cells"] == 2 * len(SERVED_HOURS)
    mc = mc_layer(days, sims=200, seed=1)
    assert mc["sims"] == 200 and 0.0 <= mc["warn_rate"] <= 1.0
    dr = driver_stats(days)
    assert dr["all"]["n"] == 2 and dr["all"]["max"] == 0.9
    print("mc_verdict_sim selftest PASS (predicate KAs, backtest/MC/scorers wired, "
          "driver stats sane)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
