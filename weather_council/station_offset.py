"""Measure the systematic offset between a market's settlement station and the
station the council backtests on — the missing piece that lets a sub-degree
market (e.g. Hong Kong, which settles on the Hong Kong Observatory, not the
airport) be compared honestly instead of withheld.

Why this exists
---------------
Some markets settle on a *different* sensor than the council's backtest truth.
Hong Kong is the canonical case: the contract resolves on the Hong Kong
Observatory ("Absolute Daily Max", 0.1 °C), while the council backtests skill
against the airport (VHHH) Meteostat series. Setting a VHHH-trained verdict
beside an HKO-settled market is apples-to-oranges *unless* the offset between the
two stations is known. This module measures that offset from the data both
stations already publish in the allowlisted Meteostat archive — so the
correction is earned, not assumed.

Honesty boundaries
------------------
  * The settlement station is matched to a Meteostat station by **geography +
    name-token overlap**, never by a hand-coded city→station table. If no nearby
    station shares a meaningful name token, this returns None and the caller
    keeps withholding the comparison rather than guessing.
  * The offset is **seasonal** (target day-of-year ± a window): station-vs-station
    differences vary with regime (sea-breeze, terrain), so a single annual mean
    would smear them. We require a minimum seasonal sample before asserting one.
  * The result carries its own **vintage**: Meteostat's per-station coverage can
    end years ago (HKO's daily file stops in 1992). A non-recent overlap is a
    *climatological* offset, flagged as such — stable station microclimate
    relationships justify using it, but the caller must disclose it.
"""

from __future__ import annotations

__all__ = [
    'StationOffset', 'measure_settlement_offset'
]

import datetime as dt
import statistics
from dataclasses import dataclass

from .sources import Sources, Place, Station, DailySeries

# Settlement and backtest stations must sit in the same metro to be a credible
# transfer pair. HKO↔VHHH is 4 km apart; 30 km is a generous metro bound.
MAX_PAIR_DISTANCE_KM = 30.0
# Day-of-year half-window for the seasonal offset. ±21 d ≈ the WMO-style monthly
# regime around the target without bleeding into the next season.
SEASON_WINDOW_DAYS = 21
# Below this many same-season overlapping days we will not assert an offset — the
# same "don't claim what you haven't measured" floor used elsewhere.
MIN_SEASON_DAYS = 30
# Overlap whose most recent day is older than this is a climatological (not live)
# offset, and is flagged accordingly.
MODERN_RECENCY_DAYS = 730

# Generic tokens that carry no identifying signal when matching a settlement
# station name to a Meteostat station name.
_STOPWORDS = frozenset({
    "the", "of", "and", "at", "by", "in", "de", "la", "el",
    "international", "intl", "airport", "station", "city", "the",
})


@dataclass(frozen=True)
class StationOffset:
    """A measured settlement-vs-backtest station offset for the high temperature,
    with full provenance so the caller can disclose exactly what it applied."""
    settlement_station_id: str
    settlement_station_name: str
    settlement_distance_km: float
    backtest_station_id: str
    backtest_station_name: str
    high_mean: float            # mean(settlement − backtest) high, °C, seasonal
    high_median: float
    high_sd: float              # day-to-day station-transfer noise (not the SE)
    n_season: int               # same-season overlapping days used
    n_all: int                  # all overlapping days (context)
    season_window_days: int
    overlap_start: str
    overlap_end: str
    is_modern: bool             # overlap reaches within MODERN_RECENCY_DAYS of target

    @property
    def standard_error(self) -> float:
        """SE of the seasonal mean offset — how precisely the systematic shift is
        pinned down (distinct from the day-to-day sd)."""
        return self.high_sd / (self.n_season ** 0.5) if self.n_season else float("inf")

    def note(self) -> str:
        vintage = (
            "live" if self.is_modern
            else f"climatological (Meteostat overlap ends {self.overlap_end})"
        )
        return (
            f"settlement station {self.settlement_station_name} "
            f"({self.settlement_distance_km:.1f} km) vs backtest station "
            f"{self.backtest_station_name}: seasonal high offset "
            f"{self.high_mean:+.2f} °C (median {self.high_median:+.2f}, "
            f"±{self.standard_error:.2f} SE, day-to-day sd {self.high_sd:.2f}, "
            f"n={self.n_season} same-season days, {vintage})"
        )


def _tokens(name: str, extra_stop: frozenset[str]) -> set[str]:
    raw = "".join(c.lower() if c.isalpha() else " " for c in name).split()
    return {t for t in raw if t not in _STOPWORDS and t not in extra_stop and len(t) > 1}


def _doy_in_window(day: str, target: dt.date, window: int) -> bool:
    """True if ISO `day` falls within ±window days-of-year of the target,
    handling the year wrap (Dec↔Jan)."""
    try:
        m, d = int(day[5:7]), int(day[8:10])
        a = dt.date(2000, m, d)
    except (ValueError, IndexError):
        return False
    b = dt.date(2000, target.month, target.day)
    diff = abs((a - b).days)
    return min(diff, 366 - diff) <= window


def _same_station_offset(
    sources: Sources,
    station: Station,
    target: dt.date,
    season_window: int,
    min_season_days: int,
) -> StationOffset | None:
    """A zero offset for the case where the market's settlement station IS the
    council's backtest station. The shift is exactly 0 °C *by identity* (one
    instrument, one quantity), never a measured cross-station transfer — but we
    keep the same in-season support floor and report the real overlap vintage so
    the disclosure stays honest (n same-season days, recency)."""
    series = sources.fetch_station_daily(station)
    in_season = sorted(
        d for d in series
        if series[d][0] is not None and _doy_in_window(d, target, season_window)
    )
    if len(in_season) < min_season_days:
        return None
    overlap_end = in_season[-1]
    try:
        is_modern = (target - dt.date.fromisoformat(overlap_end)).days <= MODERN_RECENCY_DAYS
    except ValueError:
        is_modern = False
    return StationOffset(
        settlement_station_id=station.id,
        settlement_station_name=station.name,
        settlement_distance_km=0.0,
        backtest_station_id=station.id,
        backtest_station_name=station.name,
        high_mean=0.0, high_median=0.0, high_sd=0.0,
        n_season=len(in_season), n_all=len(series),
        season_window_days=season_window,
        overlap_start=in_season[0], overlap_end=overlap_end,
        is_modern=is_modern,
    )


def measure_settlement_offset(
    sources: Sources,
    place: Place,
    backtest_station_id: str,
    settlement_station_name: str,
    target: dt.date,
    *,
    season_window: int = SEASON_WINDOW_DAYS,
    min_season_days: int = MIN_SEASON_DAYS,
) -> StationOffset | None:
    """Measure the seasonal high-temperature offset between the market's named
    settlement station and the council's backtest station, both via Meteostat.

    Returns None (caller keeps withholding) when the settlement station cannot be
    confidently matched to a Meteostat station, when it coincides with the
    backtest station, or when same-season overlap is too thin to assert an offset.
    """
    if not settlement_station_name or not backtest_station_id:
        return None

    nearby = sources.nearest_stations(place, max_deg=0.75, limit=10)
    by_id = {s.id: s for s in nearby}
    backtest = by_id.get(str(backtest_station_id))
    if backtest is None:                       # backtest station not among neighbours
        return None

    # Match the settlement station by name-token overlap (excluding the city's own
    # name and generic words) among nearby stations, never a hard-coded mapping.
    place_stop = _tokens(place.name, frozenset())
    want = _tokens(settlement_station_name, place_stop)
    if not want:
        return None
    best: Station | None = None
    best_score = 0
    for s in nearby:
        if s.id == backtest.id:
            continue
        if s.distance_km > MAX_PAIR_DISTANCE_KM:
            continue
        score = len(_tokens(s.name, place_stop) & want)
        if score > best_score or (score == best_score and score > 0
                                  and best is not None
                                  and s.distance_km < best.distance_km):
            best, best_score = s, score

    # Same-station case. The council may already anchor its backtest on the
    # market's OWN settlement station (Hong Kong is the canonical case: once the
    # modern HKO open-data record is overlaid, the council backtests on the Hong
    # Kong Observatory — exactly what the contract resolves on). The backtest
    # station's name then matches the settlement name at least as well as any
    # neighbour, so there is no cross-station transfer to make: the verdict already
    # lives on the settlement scale and the offset is 0 °C by identity, earned not
    # assumed. Take this path BEFORE declining for want of a *second* station.
    back_score = len(_tokens(backtest.name, place_stop) & want)
    if back_score >= 1 and back_score >= best_score:
        return _same_station_offset(sources, backtest, target,
                                    season_window, min_season_days)

    if best is None or best_score < 1:
        return None

    settle_series: DailySeries = sources.fetch_station_daily(best)
    # The bulk archive for some settlement stations (notably the Hong Kong
    # Observatory, which ends in 1992) is too old to reach the modern window.
    # When the matched station exposes a recent first-party record, fold it in so
    # the overlap — and therefore is_modern — reflects the current relationship,
    # not a decades-stale one. Returns None for any station without such a feed.
    recent = sources.recent_station_series(best, target)
    if recent:
        settle_series = {**settle_series, **recent}
    back_series: DailySeries = sources.fetch_station_daily(backtest)
    common = sorted(set(settle_series) & set(back_series))
    if not common:
        return None

    season_days = [d for d in common if _doy_in_window(d, target, season_window)]
    diffs = [
        settle_series[d][0] - back_series[d][0]
        for d in season_days
        if settle_series[d][0] is not None and back_series[d][0] is not None
    ]
    if len(diffs) < min_season_days:
        return None

    overlap_end = common[-1]
    try:
        end_date = dt.date.fromisoformat(overlap_end)
        is_modern = (target - end_date).days <= MODERN_RECENCY_DAYS
    except ValueError:
        is_modern = False

    return StationOffset(
        settlement_station_id=best.id,
        settlement_station_name=best.name,
        settlement_distance_km=round(best.distance_km, 1),
        backtest_station_id=backtest.id,
        backtest_station_name=backtest.name,
        high_mean=round(statistics.mean(diffs), 3),
        high_median=round(statistics.median(diffs), 3),
        high_sd=round(statistics.pstdev(diffs), 3) if len(diffs) > 1 else 0.0,
        n_season=len(diffs),
        n_all=len(common),
        season_window_days=season_window,
        overlap_start=common[0],
        overlap_end=overlap_end,
        is_modern=is_modern,
    )
