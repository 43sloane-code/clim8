"""Intraday running-max dead-bucket eliminator (ledger candidate 48).

A daily-MAX temperature settlement has one property the forward forecast does
not: as the local day unfolds, every observation already recorded is a HARD
LOWER BOUND on the final maximum. The realized max can only go UP. The
settlement quantizer (HK floor, London round-half-up) is monotone in the
temperature, so once the running max so far lands in whole-degree bucket B,
every bucket strictly BELOW B is mechanically impossible — it can never settle.

This module turns that into a READ-ONLY annotation: it fetches the observations
recorded so far today from the SAME settlement instrument the market resolves on
(London City Airport EGLC via the IEM ASOS METAR archive; the Hong Kong
Observatory via the HKO real-time feed), computes the running max, and reports
which buckets are now dead. It NEVER moves the verdict's central pick, a vote, a
weight, or a trade — it only eliminates outcomes that observed reality has
already ruled out.

Two safety invariants, both mirroring the TC gate's "feed failure != all-clear":

  * ZERO FALSE ELIMINATIONS. We only ever eliminate buckets STRICTLY BELOW the
    bucket the running max already guarantees. Because final_max >= running_max
    and the quantizer is monotone, floor(final_max) >= floor(running_max), so
    those low buckets are impossible with certainty. The bucket the running max
    itself lands in stays ALIVE (the day could end right there).
  * A feed-down or a future/closed target eliminates NOTHING and says so loudly.
    Silence is never read as "no buckets dead"; an unverified state is surfaced.

Applicability: this is a TODAY-only refinement. If the target day is in the
future (lead >= 1) there are no observations yet; if it has already settled the
question is moot. Only the two basket settlement cities are configured.
"""

from __future__ import annotations

__all__ = ['IntradayFloor', 'intraday_floor']

import datetime as dt
from dataclasses import dataclass

from .market import _native_reading_int
from .sources import Place, Sources, place_today


@dataclass(frozen=True)
class _CityCfg:
    """How one basket city settles and where its live obs come from."""
    key: str             # lower-case name token matched by containment
    sub_degree: bool     # True => floor (HK 0.1C); False => round-half-up (others)
    fetch: str           # "hko" | "metar"
    label: str
    icao: str | None = None   # settlement airport ICAO for the "metar" fetch
    grain: str = "C"          # settlement unit: "C" everywhere except San Francisco ("F")


# The bucket cities, keyed exactly like run.py's SETTLEMENT_REFERENCE so the
# settlement rule here is the SAME one the rest of the pipeline commits to.
# Manila (Ninoy Aquino RPLL) and London (London City EGLC) both settle whole-°C
# round-half-up on an airport with an hourly METAR record (so the intraday-ceiling
# lever applies); Hong Kong settles floor on the Observatory (no hourly record).
_CITY_CONFIG: tuple[_CityCfg, ...] = (
    _CityCfg("hong kong", sub_degree=True, fetch="hko",
             label="Hong Kong Observatory (floor / range-containment)"),
    _CityCfg("london", sub_degree=False, fetch="metar", icao="EGLC",
             label="London City Airport EGLC (round-half-up)"),
    _CityCfg("manila", sub_degree=False, fetch="metar", icao="RPLL",
             label="Ninoy Aquino Intl RPLL (round-half-up)"),
    _CityCfg("singapore", sub_degree=False, fetch="metar", icao="WSSS",
             label="Changi WSSS (round-half-up)"),
    _CityCfg("san francisco", sub_degree=False, fetch="metar", icao="KSFO",
             grain="F", label="San Francisco Intl KSFO (whole °F, round-half-up)"),
)


@dataclass(frozen=True)
class IntradayFloor:
    """The dead-bucket annotation for one city/day.

    kind:
      * "floor"      — a running max was observed; `floor_bucket` is the lowest
                       whole-degree bucket still possible and everything below it
                       is dead.
      * "not_today"  — target is not the city-local current day; nothing to track.
      * "unverified" — the live feed was unreachable/empty; NO buckets eliminated,
                       surfaced loudly (feed failure is never read as all-clear).
      * "not_basket" — city is not one of the two configured settlement cities.
    """
    kind: str
    city: str
    target: str
    sub_degree: bool
    grain: str = "C"          # settlement unit for floor_bucket / running_max display
    label: str | None = None
    running_max_c: float | None = None
    record_time: str | None = None
    source: str | None = None
    floor_bucket: int | None = None   # lowest still-possible bucket (guaranteed)
    n_obs: int = 0
    note: str | None = None

    @property
    def is_floor(self) -> bool:
        return self.kind == "floor"

    @property
    def is_unverified(self) -> bool:
        return self.kind == "unverified"

    def is_dead(self, bucket_int: int) -> bool:
        """True iff a whole-degree bucket is mechanically impossible given the
        running max. Only meaningful for a 'floor' state; conservatively False
        otherwise (an unknown floor eliminates nothing)."""
        if self.floor_bucket is None:
            return False
        return bucket_int < self.floor_bucket


def _cfg_for(place: Place) -> _CityCfg | None:
    name = (getattr(place, "name", "") or "").strip().lower()
    for cfg in _CITY_CONFIG:
        if cfg.key in name or name in cfg.key:
            return cfg
    return None


def _running_max_metar(sources: Sources, icao: str, target: dt.date,
                       timezone: str) -> tuple[float | None, str | None, int]:
    """Max METAR air temperature observed so far on the target LOCAL day at the
    settlement airport `icao` (EGLC for London, RPLL for Manila).

    The IEM archive treats the end date as exclusive, so we ask through the day
    after the target and keep only obs whose local timestamp falls on the target
    date. Returns (running_max_c, record_time_of_max, n_obs)."""
    obs = sources.fetch_metar_observations(
        icao, target, target + dt.timedelta(days=1),
        timezone or "Etc/UTC")
    today_iso = target.isoformat()
    todays = [(ts, c) for (ts, c) in obs if ts[:10] == today_iso]
    if not todays:
        return (None, None, 0)
    ts_max, c_max = max(todays, key=lambda p: p[1])
    record_time = ts_max.replace(" ", "T", 1) if " " in ts_max else ts_max
    return (c_max, record_time, len(todays))


def _running_max_hko(sources: Sources) -> tuple[float | None, str | None, int]:
    """Hong Kong's running floor from the HKO real-time feed. The feed exposes
    only the CURRENT reading, not an intraday series — but a current observation
    is still a hard lower bound on the day's max (max >= now), so it is a valid,
    if weaker, eliminator. Returns (current_c, record_time, 1) or (None, ...)."""
    live = sources.hko_current()
    if not live or live.get("temperature_2m") is None:
        return (None, None, 0)
    return (float(live["temperature_2m"]),
            live.get("record_time"), 1)


def intraday_floor(place: Place, target: dt.date, *,
                   sources: Sources | None = None,
                   today: dt.date | None = None) -> IntradayFloor:
    """Compute the read-only intraday dead-bucket annotation for one city/day.

    `today` overrides the city-local current date (tests / determinism); when
    omitted it is resolved with place_today(place). `sources` is required for the
    two basket cities (the live fetch); a feed failure yields an 'unverified'
    state that eliminates nothing.
    """
    city = getattr(place, "name", "") or "?"
    tgt_iso = target.isoformat()
    cfg = _cfg_for(place)
    if cfg is None:
        return IntradayFloor(kind="not_basket", city=city, target=tgt_iso,
                             sub_degree=False,
                             note="not one of the configured settlement cities")

    local_today = today if today is not None else place_today(place)
    if target != local_today:
        when = "the future" if target > local_today else "already settled"
        return IntradayFloor(
            kind="not_today", city=city, target=tgt_iso, sub_degree=cfg.sub_degree,
            label=cfg.label,
            note=(f"target {tgt_iso} is {when} (city-local today is "
                  f"{local_today.isoformat()}); intraday tracking applies only to "
                  f"the current day"))

    if sources is None:
        return IntradayFloor(
            kind="unverified", city=city, target=tgt_iso, sub_degree=cfg.sub_degree,
            label=cfg.label, note="no Sources handle; cannot fetch live obs")

    try:
        if cfg.fetch == "metar":
            rmax, rtime, n = _running_max_metar(
                sources, cfg.icao, target, place.timezone)
            source = f"{cfg.label} (live IEM ASOS METAR)"
        else:
            rmax, rtime, n = _running_max_hko(sources)
            source = "Hong Kong Observatory (live HKO real-time feed)"
    except Exception as exc:
        return IntradayFloor(
            kind="unverified", city=city, target=tgt_iso, sub_degree=cfg.sub_degree,
            label=cfg.label, source=cfg.label,
            note=f"live obs feed errored: {exc}")

    if rmax is None or n == 0:
        return IntradayFloor(
            kind="unverified", city=city, target=tgt_iso, sub_degree=cfg.sub_degree,
            label=cfg.label, source=source,
            note="no observations recorded yet on the target local day")

    floor_bucket = _native_reading_int(rmax, cfg.grain, cfg.sub_degree)
    return IntradayFloor(
        kind="floor", city=city, target=tgt_iso, sub_degree=cfg.sub_degree,
        grain=cfg.grain, label=cfg.label, running_max_c=rmax, record_time=rtime,
        source=source, floor_bucket=floor_bucket, n_obs=n)
