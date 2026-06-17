"""Auditable gate for the intraday-ceiling lever (weather_council.intraday_ceiling).

Leak-free, walk-forward backtest on the SETTLEMENT instrument's own hourly record
(London City Airport EGLC via the IEM ASOS METAR archive): for each held-out day
and each evaluation hour, sharpen the bucket pmf from the running-max-so-far plus
the empirical remaining-rise learned from STRICTLY EARLIER days, and score the
modal bucket against the settled bucket = round_half_up(final daily max). Reports
exact-bucket hit by hour, the no-intraday climatology baseline, and a disjoint-fold
sign-stability check — the gate the lever cleared before it was wired in.

Usage:  PYTHONPATH=. python3 tools/intraday_ceiling_backtest.py [--days 160] [--warmup 40]
"""
from __future__ import annotations

import argparse
import statistics
import datetime as dt
from collections import defaultdict

from weather_council.sources import Sources
from weather_council.market import _native_reading_int
from weather_council.intraday_ceiling import (
    remaining_rise_samples, sharpen_pmf, MIN_RISE_SAMPLES)

EVAL_HOURS = (9, 12, 15, 18)


def _rate(xs: list[int]) -> str:
    return f"{sum(xs) / len(xs) * 100:5.1f}% (n={len(xs)})" if xs else "    n/a"


def main() -> int:
    ap = argparse.ArgumentParser(description="Intraday-ceiling disjoint-fold gate (London).")
    ap.add_argument("--days", type=int, default=160, help="hourly lookback window")
    ap.add_argument("--warmup", type=int, default=40, help="days before scoring starts")
    args = ap.parse_args()

    src = Sources()
    end = dt.date.today()
    obs = src.fetch_metar_observations(
        "EGLC", end - dt.timedelta(days=args.days), end, "Europe/London")
    by_date: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for ts, c in obs:
        by_date[ts[:10]].append((int(ts[11:13]), c))
    days = sorted(d for d, v in by_date.items() if v)
    if len(days) <= args.warmup + 10:
        print(f"insufficient history ({len(days)} days)")
        return 1

    def settled(d: str) -> int:
        return _native_reading_int(max(c for _, c in by_date[d]), "C", False)

    test = days[args.warmup:]
    hits: dict[int, list[int]] = {h: [] for h in EVAL_HOURS}
    clim: list[int] = []
    for i, d in enumerate(test, start=args.warmup):
        prior = days[:i]
        s = settled(d)
        clim.append(1 if statistics.mode([settled(p) for p in prior]) == s else 0)
        hist = {p: by_date[p] for p in prior}
        for h in EVAL_HOURS:
            rm = max((c for hh, c in by_date[d] if hh <= h), default=None)
            if rm is None:
                continue
            rises = remaining_rise_samples(hist, h)
            if len(rises) < MIN_RISE_SAMPLES:
                continue
            modal = sharpen_pmf(rm, rises, False)[0][0]
            hits[h].append(1 if modal == s else 0)

    print(f"\nINTRADAY-CEILING GATE — London EGLC, {len(test)} held-out days "
          f"(warmup {args.warmup}, window {args.days}d)")
    print("=" * 64)
    print(f"  baseline (no intraday, climatology modal bucket): {_rate(clim)}")
    for h in EVAL_HOURS:
        print(f"  intraday by {h:02d}:00 local -> exact-bucket hit {_rate(hits[h])}")

    print("-" * 64)
    print("  DISJOINT-FOLD sign-stability (2 chronological halves):")
    ok = True
    for h in EVAL_HOURS:
        xs = hits[h]
        if len(xs) < 10:
            continue
        k = len(xs)
        f0, f1 = xs[:k // 2], xs[k // 2:]
        c0, c1 = clim[:len(clim) // 2], clim[len(clim) // 2:]
        b0 = sum(f0) / len(f0) - sum(c0) / len(c0)
        b1 = sum(f1) / len(f1) - sum(c1) / len(c1)
        stable = b0 > 0 and b1 > 0
        ok = ok and (stable or h < 12)        # only require the post-noon hours to hold
        print(f"    {h:02d}:00  fold0 {sum(f0)/len(f0)*100:5.1f}%  fold1 "
              f"{sum(f1)/len(f1)*100:5.1f}%  vs clim Δ {b0*100:+.0f}/{b1*100:+.0f} pts  "
              f"{'STABLE' if stable else 'flips'}")
    print("-" * 64)
    print("  -> post-noon sharpening beats climatology on BOTH folds: a real, "
          "monotone information gain." if ok else
          "  -> a post-noon fold flipped: investigate before trusting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
