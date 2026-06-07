"""Seasonal-analog bias correction for out-of-season targets.

Why this exists
---------------
Each member's bias correction is `mean(forecast − observed)` over the trailing
backtest window. That is sound *when the window shares the target's regime*. But
the free Meteostat bulk archive lags ~2-3 months, so for a warm-season target
every station's trailing window can sit in the previous (cool) season — and a
forecast model's bias is regime-dependent. Hong Kong is the worked example: the
panel is mildly cold-biased in winter (~−0.9 °C) but strongly cold-biased in
summer (~−1.8 °C), so a winter-trained correction *under*-corrects a June verdict.

The fix is the standard climatological move: estimate the bias from **same
day-of-year analog days across prior years** instead of the out-of-season
trailing window. This is purely data-derived (real forecast-vs-observed pairs)
and backtested: on 172 held-out June days for Hong Kong (leave-one-year-out), the
seasonal-analog bias cut blended MAE from 0.838 °C (winter-window bias) to
0.571 °C — a 32% reduction — and removed the residual cold bias (signed error
−0.69 → −0.02 °C). The raw, uncorrected blend scored 1.640 °C, so the correction
is doing real work; it just has to be learned on the right season.

Scope / safety
--------------
  * This only ever *re-estimates a bias from real data*; it never invents a
    number. If too few analog days exist (a young archive, a data-sparse city) it
    returns None and the caller keeps the trailing-window bias.
  * It is gated by the council to fire ONLY when the trailing window is out of
    season (season_gap > SEASON_MATCH_DAYS), so in-season verdicts are unchanged.
"""

from __future__ import annotations

__all__ = [
    'doy_distance', 'filter_analog', 'seasonal_skill'
]

import datetime as dt
import statistics

from .agents import Skill
from .sources import DailySeries

# Day-of-year half-window for "same season" — matches the window the Hong Kong
# leave-one-year-out backtest was validated on, and the ±~3 weeks over which a
# monthly climate normal is roughly constant.
SEASON_ANALOG_WINDOW_DAYS = 21
# How far back to gather analog years. Open-Meteo's historical-forecast archive
# (the model's *own* past forecasts, needed to measure its bias) effectively
# begins in 2022, so this floor — not a guessed count of years — bounds the reach.
SEASON_ANALOG_ARCHIVE_FLOOR = dt.date(2022, 1, 1)
# Minimum analog forecast/observed pairs before we will assert a seasonal bias —
# the same "don't claim what you haven't measured" floor used across the project.
MIN_ANALOG_SAMPLES = 15


def doy_distance(day: str, target: dt.date) -> int | None:
    """Circular day-of-year distance between an ISO date and the target (handles
    the Dec↔Jan wrap). None if the date can't be parsed."""
    try:
        m, d = int(day[5:7]), int(day[8:10])
        a = dt.date(2000, m, d)
    except (ValueError, IndexError):
        return None
    b = dt.date(2000, target.month, target.day)
    diff = abs((a - b).days)
    return min(diff, 366 - diff)


def filter_analog(series: DailySeries, target: dt.date,
                  *, window: int = SEASON_ANALOG_WINDOW_DAYS,
                  not_after: str | None = None) -> DailySeries:
    """Keep only the days within ±window day-of-year of the target (and, if
    given, on/before `not_after` so the trailing window can't leak in)."""
    out: DailySeries = {}
    for day, val in series.items():
        if not_after is not None and day > not_after:
            continue
        dd = doy_distance(day, target)
        if dd is not None and dd <= window:
            out[day] = val
    return out


def seasonal_skill(forecast: DailySeries, observed: DailySeries, target: dt.date,
                   attr: str, *, window: int = SEASON_ANALOG_WINDOW_DAYS) -> Skill | None:
    """Bias / MAE for one variable from same-season analog days only. Returns
    None if fewer than MIN_ANALOG_SAMPLES paired analog days are available."""
    idx = 0 if attr == "high" else 1
    pairs: list[tuple[float, float]] = []
    for day, fc in forecast.items():
        obs = observed.get(day)
        if obs is None:
            continue
        dd = doy_distance(day, target)
        if dd is None or dd > window:
            continue
        f, o = fc[idx], obs[idx]
        if f is None or o is None:
            continue
        pairs.append((f, o))
    if len(pairs) < MIN_ANALOG_SAMPLES:
        return None
    diffs = [f - o for f, o in pairs]
    bias = statistics.mean(diffs)
    mae_raw = statistics.mean(abs(x) for x in diffs)
    mae_corrected = statistics.mean(abs(x - bias) for x in diffs)
    return Skill(bias=bias, mae_raw=mae_raw, mae_corrected=mae_corrected, n=len(pairs))
