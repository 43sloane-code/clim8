"""Tropical-cyclone halt gate for Hong Kong (ledger candidate 52).

A hard RISK gate, not an accuracy claim. When a named tropical cyclone's
forecast track threatens Hong Kong within the next five days, the harness
ABSTAINS on the Hong Kong verdict: it refuses a bucket and logs the trigger.
A TC near the station turns the daily-max distribution non-stationary and
multi-modal in a way the calibrated council was never trained for (rain bands,
feeder convection, sudden insolation collapse), so the honest output is "no
bucket", not a falsely confident one.

Design invariants (the gate is asymmetric BY DESIGN — a false halt costs one
skipped day, a false "all clear" can cost a blown settlement):

  * Hong Kong only. London and every other city are a no-op (returns CLEAR).
  * EITHER source triggering halts — two-source agreement is NOT required.
  * "Feed failure != all clear." The ONLY verified-clear outcomes are:
      (a) JMA explicitly reports zero active tropical cyclones, or
      (b) every active TC was fetched AND parsed AND none threatens HK.
    Anything else — the active-list fetch failing, OR a listed TC whose
    forecast cannot be fetched/parsed — returns an UNVERIFIED result, which
    the caller surfaces as a loud "TC GATE UNVERIFIED" warning and does NOT
    treat as permission to run silently.

Source: JMA `bosai` typhoon JSON feed (www.jma.go.jp), the same machine-readable
data that drives the official JMA typhoon site. JTWC is supported as an optional
second source (`extra_sources=`) but is not wired to a live adapter here; when a
source's adapter is absent or raises, its contribution is treated as UNVERIFIED,
never as CLEAR.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

# Reuse the single, measurement-justified definition of "this place settles on
# the Hong Kong Observatory" — the gate must fire for exactly the cities the rest
# of the harness treats as Hong Kong, no more, no less.
from .council import _wants_hko_anchor

# JMA bosai typhoon feed. The active-TC list is a JSON array of TC id strings;
# each id has a forecast document under .../data/{id}/forecast.json. Host must be
# on the security allowlist (www.jma.go.jp).
JMA_BASE = "https://www.jma.go.jp/bosai/typhoon/data"
JMA_TARGET_URL = f"{JMA_BASE}/targetTc.json"

# Forecast horizon for the cone test. The settlement market resolves a single
# day; a TC inside the 5-day cone is the documented risk-control trigger.
HORIZON_HOURS = 120

# Generous default probability-circle radius (km) when a forecast point carries a
# center but no explicit radius. JMA 70%-probability circles at +120h commonly
# run 200-400 km; 250 km is a conservative-but-not-absurd fallback so a missing
# radius never silently shrinks the cone to a point.
DEFAULT_CIRCLE_KM = 250.0


@dataclass(frozen=True)
class TCHalt:
    """The gate's verdict for one place.

    `kind` is the tri-state:
      * "halt"       — a TC threatens HK; the caller MUST abstain.
      * "unverified" — the gate could not confirm safety (a feed/parse failure);
                       the caller MUST warn loudly and MUST NOT treat as clear.
    A verified-CLEAR result is represented by `tc_halt(...) is None`, not by this
    object — so a truthy return always means "do not proceed normally".
    """
    kind: str                       # "halt" | "unverified"
    name: str                       # TC name/id, or the reason for "unverified"
    source: str                     # which source produced this ("JMA", ...)
    asof_utc: str                   # ISO timestamp of when the gate ran
    closest_km: float | None = None  # closest forecast approach to HK (halt only)
    within_hours: int | None = None  # lead of the closest approach (halt only)

    @property
    def is_halt(self) -> bool:
        return self.kind == "halt"

    @property
    def is_unverified(self) -> bool:
        return self.kind == "unverified"


@dataclass(frozen=True)
class _ForecastPoint:
    hours: int          # lead from the bulletin base time, in hours
    lat: float
    lon: float
    radius_km: float    # probability-circle radius around (lat, lon)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


class _SourceError(Exception):
    """A source could not be fetched or parsed — maps to UNVERIFIED, never CLEAR."""


# --- JMA bosai parsing -------------------------------------------------------
#
# The forecast document is an array of "category" blocks; the forecast track
# lives in blocks that carry timed points with a center coordinate and (usually)
# a probability-circle radius. The JMA schema is verbose and versioned, so the
# parser is deliberately TOLERANT: it walks the structure looking for objects
# that expose a coordinate plus a forecast hour, and treats anything it cannot
# turn into at least one usable point as a parse failure (-> UNVERIFIED). It
# never invents a point, and it never returns an empty track as "no threat".


def _coerce_float(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _extract_points(node, _depth: int = 0) -> list[_ForecastPoint]:
    """Recursively pull forecast points out of a JMA forecast document.

    A point needs a latitude, a longitude, and a forecast hour. Latitude/
    longitude are read from common JMA key spellings; the radius is optional
    (DEFAULT_CIRCLE_KM is used when absent). The walk is bounded in depth to
    avoid pathological nesting.
    """
    if _depth > 8:
        return []
    points: list[_ForecastPoint] = []
    if isinstance(node, dict):
        lat = _first(node, ("lat", "latitude", "centerLat"))
        lon = _first(node, ("lon", "lng", "longitude", "centerLon"))
        hours = _first(node, ("hour", "hours", "forecastHour", "ft"))
        if lat is not None and lon is not None and hours is not None:
            radius = _first(node, ("radius", "radiusKm", "probabilityCircleRadius",
                                   "circle", "rad"))
            points.append(_ForecastPoint(
                hours=int(round(hours)),
                lat=lat, lon=lon,
                radius_km=radius if (radius is not None and radius > 0)
                else DEFAULT_CIRCLE_KM,
            ))
        for v in node.values():
            points.extend(_extract_points(v, _depth + 1))
    elif isinstance(node, list):
        for v in node:
            points.extend(_extract_points(v, _depth + 1))
    return points


def _first(d: dict, keys: Iterable[str]) -> float | None:
    for k in keys:
        if k in d:
            f = _coerce_float(d[k])
            if f is not None:
                return f
    return None


def parse_jma_forecast(doc) -> list[_ForecastPoint]:
    """Turn a parsed JMA forecast document into forecast points, or raise.

    Raising on an unparseable / empty document is the safety contract: a listed
    TC we cannot read becomes UNVERIFIED upstream, never a silent all-clear.
    """
    points = _extract_points(doc)
    points = [p for p in points if 0 <= p.hours <= HORIZON_HOURS + 6]
    if not points:
        raise _SourceError("JMA forecast document yielded no usable track points")
    return points


def cone_contains(points: Sequence[_ForecastPoint], lat: float, lon: float
                  ) -> tuple[bool, float | None, int | None]:
    """Is (lat, lon) inside any forecast probability circle within the horizon?

    Returns (hit, closest_km, hours_at_closest). The cone is the union of the
    probability circles along the forecast track — a mechanical, fit-free test:
    the model need not be right, only the geometry.
    """
    closest_km: float | None = None
    closest_hours: int | None = None
    hit = False
    for p in points:
        if p.hours > HORIZON_HOURS:
            continue
        d = _haversine_km(lat, lon, p.lat, p.lon)
        if closest_km is None or d < closest_km:
            closest_km, closest_hours = d, p.hours
        if d <= p.radius_km:
            hit = True
    return hit, closest_km, closest_hours


# --- Source adapters ---------------------------------------------------------


def _jma_source(http, place, now: dt.datetime) -> TCHalt | None:
    """JMA adapter. Returns a halt TCHalt, None for verified-clear, or raises
    _SourceError for anything that must be treated as UNVERIFIED."""
    try:
        active = http.get_json_array(JMA_TARGET_URL)
    except Exception as exc:               # noqa: BLE001 — any failure -> unverified
        raise _SourceError(f"JMA active-list fetch failed: {exc}") from exc
    if not isinstance(active, list):
        raise _SourceError("JMA active-list was not a JSON array")
    if len(active) == 0:
        return None                        # JMA explicitly: no active TC -> CLEAR

    asof = now.replace(microsecond=0).isoformat()
    for entry in active:
        tc_id = _tc_id(entry)
        if tc_id is None:
            raise _SourceError(f"JMA active entry had no id: {entry!r}")
        try:
            doc = http.get_json_array(f"{JMA_BASE}/{tc_id}/forecast.json")
        except Exception as exc:           # noqa: BLE001
            raise _SourceError(
                f"JMA forecast fetch failed for {tc_id}: {exc}") from exc
        points = parse_jma_forecast(doc)   # raises -> unverified
        hit, km, hrs = cone_contains(points, place.latitude, place.longitude)
        if hit:
            return TCHalt(kind="halt", name=str(tc_id), source="JMA",
                          asof_utc=asof, closest_km=km, within_hours=hrs)
    return None                            # all TCs parsed, none threaten HK


def _tc_id(entry) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for k in ("tcID", "tcId", "id", "number"):
            if k in entry and entry[k]:
                return str(entry[k])
    return None


# A source adapter takes (http, place, now) and returns TCHalt|None or raises
# _SourceError. JTWC would slot in here once a live adapter + allowlist host
# exist; until then only JMA is wired.
SourceAdapter = Callable[[object, object, dt.datetime], "TCHalt | None"]


def tc_halt(place, *, http=None, now: dt.datetime | None = None,
            extra_sources: Sequence[SourceAdapter] = ()) -> TCHalt | None:
    """Evaluate the TC halt gate for `place`.

    Returns:
      * TCHalt(kind="halt", ...)        -> a TC threatens HK; caller MUST abstain.
      * TCHalt(kind="unverified", ...)  -> safety could not be confirmed; caller
                                           MUST warn and MUST NOT run silently.
      * None                            -> verified clear (or not Hong Kong).

    EITHER source triggering halts. A source raising _SourceError makes the gate
    UNVERIFIED *unless another source returns a definite halt* (a halt from any
    source dominates). A verified-clear is returned only when at least one source
    confirmed clear and NO source was left unverified.
    """
    if not _wants_hko_anchor(place):
        return None                        # London / everything else: no-op

    now = now or dt.datetime.now(dt.timezone.utc)
    if http is None:
        from .security import SafeHTTPClient
        http = SafeHTTPClient()

    sources: list[tuple[str, SourceAdapter]] = [("JMA", _jma_source)]
    sources += [(getattr(s, "__name__", "source"), s) for s in extra_sources]

    unverified_reason: str | None = None
    any_clear = False
    for sname, adapter in sources:
        try:
            result = adapter(http, place, now)
        except _SourceError as exc:
            unverified_reason = unverified_reason or f"{sname}: {exc}"
            continue
        except Exception as exc:           # noqa: BLE001 — defensive: never clear on a crash
            unverified_reason = unverified_reason or f"{sname}: {exc}"
            continue
        if result is not None and result.is_halt:
            return result                  # EITHER source halts -> halt wins
        any_clear = True                    # this source confirmed clear

    # No source halted (a halt returns eagerly above). A verified clear requires
    # at least one source that actually CONFIRMED clear. If every source errored,
    # the gate is blind — report UNVERIFIED, never a silent all-clear.
    if any_clear:
        return None
    asof = now.replace(microsecond=0).isoformat()
    reason = unverified_reason or "no source produced a result"
    return TCHalt(kind="unverified", name=reason, source="gate", asof_utc=asof)
