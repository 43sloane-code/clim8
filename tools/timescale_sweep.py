"""Run the systematic verdict rule (weather_council/timescale.py) across every
timescale from a second to a year, on the real settlement sensors.

Honesty about timescale: a weather station reports at a finite cadence (ASOS
METAR ~hourly + SPECIs; daily climate records once per day). So "a second" and
"a minute" are below what was ever measured. This sweep does NOT invent sub-
cadence truth — it bins the real observations at each timescale and lets the
verdict rule's observability gate declare a scale UNOBSERVABLE when the data
cannot support it. Every reported verdict is therefore data-derived and testable.

Sources, all already disk-cached:
  * sub-daily (second/minute/hour): London City Airport (EGLC) raw IEM METAR obs.
  * daily and coarser (day/week/month/year): per-city daily MEAN temperature
    ((high+low)/2) from each market's settlement sensor — HKO Observatory open
    data and EGLC METAR.

Recommend-only: this never edits the council, places a trade, or moves funds.
"""
from __future__ import annotations

import datetime as dt
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from weather_council.sources import Sources
from weather_council import timescale as ts

_EPOCH = dt.datetime(1970, 1, 1)

TIMESCALES = [
    ("second", 1.0),
    ("minute", 60.0),
    ("hour", 3600.0),
    ("day", 86400.0),
    ("week", 7 * 86400.0),
    ("month", 30 * 86400.0),
    ("year", 365 * 86400.0),
]


def _secs_from_iso(s: str) -> float:
    # Naive local timestamp -> seconds since 1970 (tz-agnostic; only relative
    # spacing matters for binning, so no timezone conversion is applied).
    return (dt.datetime.fromisoformat(s) - _EPOCH).total_seconds()


def _raw_points_eglc(days_back: int = 120) -> tuple[list[tuple[float, float]], float]:
    """Raw (epoch_s, temp_c) METAR obs for EGLC + the measured median cadence (s)."""
    s = Sources()
    end = dt.date.today()
    start = end - dt.timedelta(days=days_back)
    obs = s.fetch_metar_observations("EGLC", start, end, "Europe/London")
    pts = [(_secs_from_iso(t), c) for t, c in obs]
    pts.sort()
    gaps = sorted(pts[i][0] - pts[i - 1][0] for i in range(1, len(pts)))
    median_gap = gaps[len(gaps) // 2] if gaps else float("nan")
    return pts, median_gap


def _daily_mean_points(series: dict[str, tuple[float, float]]) -> list[tuple[float, float]]:
    """(epoch_at_noon, mean_temp) from a {date -> (high, low)} settlement series."""
    pts = []
    for d, (hi, lo) in series.items():
        secs = (dt.datetime.fromisoformat(d) - _EPOCH).total_seconds() + 43200
        pts.append((secs, 0.5 * (hi + lo)))
    pts.sort()
    return pts


def _sweep(name: str, raw_pts: list, median_gap: float,
           daily_pts: list) -> None:
    print(f"\n=== {name} ===")
    if median_gap == median_gap:   # not NaN
        print(f"  measured obs cadence: median {median_gap/60:.1f} min "
              f"between raw readings  ({len(raw_pts)} sub-daily obs)")
    print(f"  {'scale':<9}{'n':>7}{'MAE_F':>10}{'MAE_R':>10}"
          f"{'skill':>9}{'DM':>8}{'p':>8}{'obs':>8}   verdict")
    for label, period in TIMESCALES:
        pts = raw_pts if period < 86400.0 else daily_pts
        series, obsv = ts.resample(pts, period)
        v = ts.evaluate(series, label, obsv)
        print(v.line())


def main() -> None:
    ts._self_test()
    s = Sources()
    today = dt.date.today()

    raw_eglc, gap = _raw_points_eglc(120)
    ldn_daily = _daily_mean_points(s.london_eglc_truth_series(today, back_years=2))
    hk_daily = _daily_mean_points(s.hko_truth_series(today, back_years=4))

    _sweep("London (EGLC City Airport)", raw_eglc, gap, ldn_daily)
    # Hong Kong open data is daily-only: no raw sub-daily feed, so second/minute/
    # hour are unobservable by data, not by choice.
    _sweep("Hong Kong (HKO Observatory)", [], float("nan"), hk_daily)

    print("\nlegend: MAE_F=persistence  MAE_R=climatology  skill=1-MAE_F/MAE_R  "
          "DM=Diebold-Mariano (NW-HAC)  p=two-sided  obs=observability")
    print("(recommend-only: never edits the council, places a trade, or moves funds.)")


if __name__ == "__main__":
    main()
