"""Data access layer — the only module that touches the network.

All weather figures in the system originate here, from Open-Meteo:

  * geocode               -> city -> coordinates
  * fetch_live            -> a model's forecast for a future day
  * fetch_history_series  -> what a model forecast across a past window
  * fetch_archive_series  -> ERA5 reanalysis = observed "truth" for backtesting

Every call is mediated by SafeHTTPClient, and every response is range- and
type-checked before any value is handed upward. A missing/typo'd field yields
an empty result, never a fabricated number.
"""

from __future__ import annotations

__all__ = [
    'Place', 'Station', 'place_today', 'quantize_to_grain', 'Sources'
]

import datetime as dt
import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from .security import SafeHTTPClient, RateLimitError, SecurityError, validate_city
from . import failures

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
LIVE_URL = "https://api.open-meteo.com/v1/forecast"
HISTORY_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
# Meteostat bulk archive — real station daily observations with decades of
# history. The lite station list (~0.8 MiB gz) plus one per-station daily file.
STATIONS_URL = "https://bulk.meteostat.net/v2/stations/lite.json.gz"
STATION_DAILY_URL = "https://bulk.meteostat.net/v2/daily/{id}.csv.gz"
# Iowa Environmental Mesonet ASOS archive — raw airport METAR, the feed that
# Weather Underground (and thus market settlement) ultimately reads. Returned
# in the sensor's native unit (whole °F for US ASOS, whole °C internationally).
METAR_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
# The Weather Company / Wunderground backend — the EXACT record the Polymarket
# city markets settle on. WU stores the airport history in WHOLE °F; the contract
# converts that to whole °C, which can round to a different bucket than the IEM
# whole-°C METAR at a °F/°C boundary (e.g. true 30.4°C -> IEM 30°C but WU 87°F ->
# 30.6 -> 31°C). So this is the settlement-grade anchor; IEM stays a cross-check.
# apiKey is the wunderground.com site web key, carried in PARAMS (never the path).
WU_HISTORY_URL = "https://api.weather.com/v1/location/{loc}/observations/historical.json"
# The free wunderground.com site web key. It is the settlement spine's single point
# of failure (CLAUDE.md HARD RULE 7): if it dies, that is continuity work, not an
# accuracy lever. Env-first so the key can be rotated without a code change (and so
# CI / a fresh checkout can inject its own) — the literal is the current working
# default when WU_API_KEY is unset in the environment.
WU_API_KEY = os.environ.get("WU_API_KEY", "e1f10a1e78da46f5b10a1e78da96f525")
# Settlement-station geocodes for the v3 current-conditions feed (same host + key).
WU_GEO = {"WSSS": (1.3502, 103.994), "RPLL": (14.5086, 121.0198), "EGLC": (51.5053, 0.0553),
          "KSFO": (37.6189, -122.375), "OPKC": (24.9008, 67.1681),
          "OEJN": (21.6796, 39.1565), "KAUS": (30.1945, -97.6699),
          "KSEA": (47.4502, -122.3088)}
# Airports whose stale/distant Meteostat bulk file is overlaid with the live IEM ASOS METAR
# record (icao -> tz for local-day extremes). EGLC's Abbey Wood file is ~17km and weeks stale.
# fetch_station_daily gates on this; one-line extensible per city instead of an is_<city> method.
# (KSFO is NOT here — SF anchors on its live Wunderground oracle feed, like RPLL/WSSS.)
# OPKC (Karachi/Jinnah) IS here: its Meteostat bulk file lags ~110 days (stale to March in July),
# so without the live IEM overlay the backtest scores on the wrong season entirely.
# KAUS (Austin Bergstrom) IS here: Kalshi KXHIGHAUS settles on the NWS CLI, which is built from
# the same ASOS/METAR feed the IEM archive ingests; overlaying it keeps the council's Austin
# backtest on the settlement instrument instead of stale KATT Meteostat data.
# KSEA (Seattle-Tacoma Intl) IS here: Kalshi Seattle high-temperature market settles on the
# NWS CLI for KSEA; the IEM overlay keeps the backtest on the same ASOS/METAR source.
_IEM_OVERLAY_TZ = {"EGLC": "Europe/London", "OPKC": "Asia/Karachi",
                   "OEJN": "Asia/Riyadh", "KAUS": "America/Chicago",
                   "KSEA": "America/Los_Angeles"}
V3_CURRENT_URL = "https://api.weather.com/v3/wx/observations/current"
# The Weather Company's own daily FORECAST product — same host + same public web key as the WU
# observation feeds above. Used ONLY as a cross-reference (Sources.twc_forecast_daily); TWC never
# votes and never settles. Its consumer forecast rides here behind the same WU_API_KEY.
TWC_FORECAST_URL = "https://api.weather.com/v3/wx/forecast/daily/5day"
WU_LOCATION = {"EGLC": "EGLC:9:GB", "RPLL": "RPLL:9:PH",
               "WSSS": "WSSS:9:SG",
               "KSFO": "KSFO:9:US",
               "OPKC": "OPKC:9:PK",
               "OEJN": "OEJN:9:SA",
               "KAUS": "KAUS:9:US",
               "KSEA": "KSEA:9:US"}  # ICAO -> Weather Company location id
# Minimum hourly obs in a WU local day before its max/min are trustworthy as a
# settlement-truth extreme — a partial final day would understate the peak. A
# complete RPLL/EGLC day reports ~24 hourly observations.
WU_MIN_DAY_OBS = 12
# Hong Kong Observatory official open data — recent daily climate records direct
# from the Observatory. The Meteostat archive for the HKO station ends in 1992,
# far too old to measure a *current* settlement-vs-airport offset; this API is
# the only source of the recent HKO daily record. Read-only, keyless, CSV.
HKO_OPENDATA_URL = "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php"
# Hong Kong Observatory headquarters (Tsim Sha Tsui) — the point the "observatory"
# settlement record reports from. Used to recognise a matched station as the HKO
# by geography (combined with a name-token check) rather than a hardcoded table.
HKO_HQ_LAT, HKO_HQ_LON = 22.302, 114.174
HKO_MATCH_RADIUS_KM = 15.0       # how close a station must sit to count as HKO
# Hong Kong Observatory real-time "regional weather" open data (rhrread) — the
# live HKO instrument reading. The daily CLMMAXT/CLMMINT files settle the record;
# this is the live "now" temperature at the Observatory HQ, so a Hong Kong
# verdict's current-conditions reading is the HKO instrument itself, not an
# Open-Meteo grid-cell proxy that can sit ~2 °C away. Keyless JSON, whole-degree.
HKO_RHRREAD_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"
HKO_RHRREAD_PLACE = "Hong Kong Observatory"   # the station label inside rhrread
# Hong Kong Observatory "Daily Extract of Meteorological Observations" — one JSON
# file per civil month at the Observatory HQ. Its "Absolute Daily Maximum Air
# Temperature" column IS the figure the Hong Kong market settles on, and it
# publishes the prior day within ~a day (weeks fresher than the CLMMAXT/CLMMINT
# monthly climate API). dayData rows are positional:
#   [day, pressure, abs_max_c, mean_c, abs_min_c, dewpoint_c, rh, cloud, rainfall]
# Served with a .xml suffix but a JSON body. {ym} is YYYYMM.
HKO_DAILY_EXTRACT_URL = ("https://www.hko.gov.hk/cis/dailyExtract/"
                         "dailyExtract_{ym}.xml")
HKO_DX_ABS_MAX_COL = 2               # "Absolute Daily Maximum Air Temperature"
HKO_DX_ABS_MIN_COL = 4               # "Absolute Daily Minimum Air Temperature"
# Finer live feed: HKO's 1-minute mean air-temperature CSV reports the SAME
# Observatory instrument to 0.1 °C at a ~1-minute cadence (rhrread above only
# publishes whole degrees, so it reads e.g. 28 where the gauge is 28.4). Used to
# give the live "now" reading tenths precision and minute-level freshness; falls
# back to the whole-degree rhrread value when the CSV is unavailable. Same
# already-allowlisted host. Header row, then "YYYYMMDDHHMM,<station>,<tempC>".
HKO_1MIN_TEMP_URL = ("https://data.weather.gov.hk/weatherAPI/hko_data/"
                     "regional-weather/latest_1min_temperature.csv")
HKO_1MIN_PLACE = "HK Observatory"   # the Observatory's label inside the 1-min CSV
# Weatherbit daily forecast — a non-Open-Meteo forecaster added as a RECOMMEND-
# ONLY tracked source (logged and scored prospectively, never voted into the live
# blend until it earns >= MIN_SAMPLES paired days). Keyed: the API key is read
# from the WEATHERBIT_API_KEY env var and passed as a request PARAMETER, so it is
# never embedded in a logged base_url. Metric units (°C) requested explicitly.
WEATHERBIT_FORECAST_URL = "https://api.weatherbit.io/v2.0/forecast/daily"
WEATHERBIT_MAX_DAYS = 16            # Weatherbit's daily forecast horizon

# Plausibility band: any temperature outside this is treated as corrupt and
# dropped, so a bad upstream value can never enter a verdict.
TEMP_MIN_C = -90.0
TEMP_MAX_C = 60.0

DailySeries = dict[str, tuple[float, float]]  # date -> (high, low)

# Transparent on-disk cache for forecast-history fetches ONLY. The
# historical-forecast-api is a keyless endpoint that aggressively throttles
# bursts (HTTP 429); a cold run that needs ~8 members' history can be starved
# into "no eligible member" purely by rate limiting. Past forecast history for a
# fixed date window is immutable, so caching the exact JSON response and replaying
# it is faithful — the council, eligibility, weighting and scoring see byte-for-
# byte what the network would have returned. The cache NEVER fabricates: a miss
# during a throttle re-raises rather than inventing data. It only ever replays a
# response we genuinely fetched before.
HISTORY_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "history"
HISTORY_CACHE_TTL = dt.timedelta(days=7)
# Station-observation (WU / IEM) range fetches: the past is immutable so it is
# cached, but anything within this margin of "now" is ALWAYS re-fetched. The
# margin (not 0) absorbs host-vs-city timezone skew and same-day settlement, so a
# live intraday running max is never served stale from cache. 2 days is safe for
# any ±1-day skew while still caching ~99% of a 160-day backtest window.
OBS_CACHE_MARGIN = dt.timedelta(days=2)


def _history_cache_key(url: str, params: dict) -> str:
    blob = url + "?" + json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _history_cache_read(key: str):
    """Return (data, age) for a cached history response, or (None, None)."""
    f = HISTORY_CACHE_DIR / f"{key}.json"
    try:
        raw = f.read_text(encoding="utf-8")
        age = (dt.datetime.now(dt.timezone.utc)
               - dt.datetime.fromtimestamp(f.stat().st_mtime, tz=dt.timezone.utc))  # UTC-aware (charter)
    except OSError:
        return None, None
    try:
        return json.loads(raw), age
    except ValueError:
        return None, None


def _fuse_live_floor(runmax_c, cur_f, max24_f, yesterday_max_c, wu_record_max_f=None,
                     cap_fallback_f=None):
    """FLOOR-RAISE-ONLY fusion of the settlement station's freshest evidence into the
    running max. Returns (floor_c, note|None). Rules (the 07-04 lesson — 91°F posted ~45min
    late and the 24h register read 92°F while the half-hourly rows topped at 91):
      * the CURRENT reading always counts — it IS a station reading, just fresher;
      * temperatureMax24Hour counts ONLY when it EXCEEDS yesterday's daily max AND sits within
        a between-obs spike of today's OWN freshest evidence AND does not exceed WU's own
        authoritative daily-max — the register can lead the lagging hourly ROWS, but it can
        never legitimately exceed the daily-max endpoint that already aggregates those peaks;
      * the fusion can only RAISE the floor, never lower it, and never invents readings.
    Pure — KAT'd in tests/test_live_floor.py."""
    floor_c = runmax_c
    note = None

    def f2c(f):
        return (f - 32.0) * 5.0 / 9.0

    # PHANTOM GUARD (2026-07-09 Jeddah defect, user-caught). The v3 max24 register read 102°F
    # while WU's OWN daily-max endpoint, the daily-series AND every hourly ob topped at 100°F
    # (peak passed at 10-11:00, declining since) — a phantom that served a 39 the contract paid
    # at 38. The register may catch a between-obs peak the hourly ROWS miss, but it can NEVER
    # exceed the freshest AUTHORITATIVE evidence of how hot it actually got today.
    #
    # That ceiling is the HIGHER of WU's daily-max endpoint AND the current reading (cur_f) — NOT
    # the endpoint alone. The v1 daily-max endpoint LAGS the live feed ~1-2h (2026-07-11 Jeddah,
    # user-caught: it read 97°F while cur_f held 98°F sustained and the market settled 37), so
    # capping the register at the lagging endpoint alone would suppress a real re-heat the current
    # reading corroborates. cur_f is a fresh station reading, not a rolling carryover, so a register
    # up to cur_f is attributable. (cur_f also raises the floor directly below — this keeps the
    # register path CONSISTENT: it is never capped beneath a current reading the tool already
    # trusts. Only the max24 rolling register is capped; cur_f itself is never capped.)
    #
    # WP-3 (served-number campaign): on a daily-max endpoint OUTAGE (wu_record_max_f is None) this cap
    # must NOT silently vanish — that re-opens the 07-09 phantom in a failure regime. The cap reference
    # is the daily-max endpoint when present, ELSE a caller-supplied RECENT daily max (cap_fallback_f,
    # e.g. yesterday's peak) — a declared degraded cap. cur_f can only RAISE the ceiling (it never caps
    # the register, which legitimately leads cur_f — the 07-04 lesson). When NEITHER reference exists
    # the register stays uncapped, but that is DECLARED (ABSENT_OUTAGE in the note) so it is a
    # watchdog-visible alarm, never a silent degradation.
    cap_ref = wu_record_max_f if isinstance(wu_record_max_f, (int, float)) else cap_fallback_f
    outage_uncapped = False
    # The docstring calls the fallback cap "a declared degraded cap" — declare it.
    degraded_cap = (isinstance(max24_f, (int, float))
                    and not isinstance(wu_record_max_f, (int, float))
                    and isinstance(cap_ref, (int, float)))
    if isinstance(max24_f, (int, float)):
        if isinstance(cap_ref, (int, float)):
            ceiling = cap_ref
            if isinstance(cur_f, (int, float)) and cur_f > ceiling:
                ceiling = cur_f
            max24_f = min(max24_f, ceiling)
        elif not isinstance(wu_record_max_f, (int, float)):
            outage_uncapped = True      # endpoint down + no fallback — declared below, not silent

    if isinstance(cur_f, (int, float)) and (floor_c is None or f2c(cur_f) > floor_c):
        floor_c = f2c(cur_f)
        note = f"live now {cur_f:.0f}°F"
    # ATTRIBUTION GATE (2026-07-09 Singapore pre-dawn defect). "Exceeds yesterday's max" alone
    # is NOT enough: the register carries yesterday's TRUE peak, which can clear a whole-°F-
    # rounded yesterday row by pure granularity (89°F register vs an 88°F daily row) at an hour
    # when today has barely warmed (current 81°F). Floored onto today, remaining-rise then
    # projected an impossible ~37°C for a 30°C day. So also require the register to sit within a
    # real between-obs spike (~3°F; observed 07-04/07-07 gaps were 1°F) of today's OWN freshest
    # evidence (floor_c = obs run-max + current). A register far above that is an unattributable
    # carryover; a register close to it is genuinely today's peak the lagging rows missed.
    _REG_ATTR_MARGIN_C = 3.0 * 5.0 / 9.0        # 3 °F
    if isinstance(max24_f, (int, float)) and yesterday_max_c is not None \
            and f2c(max24_f) > yesterday_max_c + 1e-9 \
            and (floor_c is None or f2c(max24_f) > floor_c) \
            and (floor_c is None or f2c(max24_f) - floor_c <= _REG_ATTR_MARGIN_C):
        floor_c = f2c(max24_f)
        note = f"live 24h-register {max24_f:.0f}°F"
        if outage_uncapped:                # WP-3: an uncapped register raised the floor on an outage
            note += " [ABSENT_OUTAGE: daily-max endpoint down, register uncapped — verify]"
        elif degraded_cap:                 # endpoint down; register capped at the RECENT-day fallback
            note += " [DEGRADED_CAP: daily-max endpoint down, capped at recent-day fallback]"
    return floor_c, note


def _obs_days_covered(rows) -> int:
    """Distinct calendar days present in a parsed obs list. Every cached arity puts a
    'YYYY-MM-DD...' string first ((ts,c) or (date,max,min)), so day = first 10 chars.
    The sanity metric behind the cache poisoning guard in `_cached_range_obs`."""
    try:
        return len({str(r[0])[:10] for r in rows})
    except Exception:
        return 0


def _history_cache_write(key: str, data: dict) -> None:
    """Best-effort persist; a cache write must never break a live fetch."""
    try:
        HISTORY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (HISTORY_CACHE_DIR / f"{key}.json").write_text(
            json.dumps(data), encoding="utf-8")
    except OSError:
        pass


@dataclass(frozen=True)
class Place:
    name: str
    country: str
    latitude: float
    longitude: float
    timezone: str

    def label(self) -> str:
        return f"{self.name}, {self.country}"


@dataclass(frozen=True)
class Station:
    """A real surface observing station from the Meteostat archive — the kind of
    point (an airport METAR / national observatory) that temperature records and
    settlement markets actually report."""
    id: str
    name: str
    wmo: str | None
    icao: str | None
    latitude: float
    longitude: float
    elevation: float | None
    distance_km: float

    def label(self) -> str:
        ident = self.icao or (f"WMO {self.wmo}" if self.wmo else f"id {self.id}")
        return f"{self.name} ({ident})"


def place_today(place: Place) -> dt.date:
    """The current civil date in the *place's own* timezone — the anchor for
    "today" and forecast lead.

    Open-Meteo indexes its forecast grid by the place's local day, not the
    host's. Anchoring the target to the machine clock makes a same-day verdict
    for a city in another timezone ask for a day the forecast feed doesn't carry
    — e.g. on a UTC-1 host, Hong Kong (UTC+8) is already "tomorrow", so a lead-0
    request looks up a date the API never returns and every member's live value
    comes back None, collapsing the whole verdict. Resolving "today" in the
    place's zone keeps the target, the lead, and the returned grid aligned.
    Falls back to the host date when the timezone is unknown/unset."""
    tz = getattr(place, "timezone", None)
    if tz and tz != "auto":
        try:
            return dt.datetime.now(ZoneInfo(tz)).date()
        except Exception:
            pass
    return dt.date.today()


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _clean_temp(value) -> float | None:
    # bool is an int subclass: a JSON `true` in a temp field would pass a bare
    # isinstance gate and become 1.0 °C (or f2c(True) ≈ −17 °C on °F paths).
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    if v != v or not (TEMP_MIN_C <= v <= TEMP_MAX_C):  # NaN or out of band
        return None
    return v


def _wu_temp_f(value) -> float | None:
    """One WU whole-°F observation, screened: bool-excluded numeric AND inside
    the plausibility band after °F→°C. The WU settlement spine previously only
    TYPE-checked its temps (contrary to the module contract's 'every response is
    range- and type-checked'), so a corrupt 9999 could have become the day's
    settling max or the live-floor cap reference. Returns the °F float or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if _clean_temp((float(value) - 32.0) * 5.0 / 9.0) is None:
        return None
    return float(value)


def _clean_temp_cell(cell: str) -> float | None:
    """Parse one CSV temperature cell through the plausibility band; '' or a
    non-numeric token yields None (treated as not reported)."""
    cell = cell.strip()
    if cell == "":
        return None
    try:
        return _clean_temp(float(cell))
    except ValueError:
        return None


def _parse_hko_rhrread(data: dict) -> dict | None:
    """Extract the Hong Kong Observatory HQ live reading from an rhrread payload.

    rhrread groups readings by field (`temperature`, `humidity`), each carrying a
    `recordTime` and a `data` list of {place, value, unit}. We pull the row whose
    place is the Observatory HQ. Temperature runs through the plausibility band;
    humidity is optional (0–100%). Returns
    {temperature_2m, relative_humidity_2m, record_time} or None when the
    Observatory temperature is absent or corrupt — the caller then keeps the grid
    reading rather than inventing one."""
    if not isinstance(data, dict):
        return None
    temp_block = data.get("temperature") or {}
    temp_c = None
    for row in temp_block.get("data", []) or []:
        if isinstance(row, dict) and row.get("place") == HKO_RHRREAD_PLACE:
            val = row.get("value")
            if isinstance(val, (int, float)):
                temp_c = _clean_temp(float(val))
            break
    if temp_c is None:
        return None
    rh = None
    for row in (data.get("humidity") or {}).get("data", []) or []:
        if isinstance(row, dict) and row.get("place") == HKO_RHRREAD_PLACE:
            val = row.get("value")
            if isinstance(val, (int, float)) and 0 <= val <= 100:
                rh = float(val)
            break
    return {"temperature_2m": temp_c, "relative_humidity_2m": rh,
            "record_time": temp_block.get("recordTime")}


def _parse_hko_1min_temp(text: str) -> dict | None:
    """Pull the HK Observatory HQ row from HKO's 1-minute mean temperature CSV
    (header: 'Date time,Automatic Weather Station,Air Temperature(degree Celsius)';
    e.g. '202606072330,HK Observatory,28.4'). Returns {temperature_2m,
    record_time} at 0.1 °C, or None when the Observatory row is absent or its
    value is implausible — the caller then keeps the whole-degree rhrread value
    rather than inventing one. record_time is the CSV's own YYYYMMDDHHMM stamp as
    a +08:00 ISO string (HKO local time)."""
    for line in (text or "").splitlines()[1:]:        # skip the header row
        p = [c.strip() for c in line.split(",")]
        if len(p) < 3 or p[1] != HKO_1MIN_PLACE:
            continue
        temp = _clean_temp_cell(p[2])
        if temp is None:
            return None
        ts, record_time = p[0], None
        if len(ts) == 12 and ts.isdigit():
            record_time = (f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}T"
                           f"{ts[8:10]}:{ts[10:12]}:00+08:00")
        return {"temperature_2m": temp, "record_time": record_time}
    return None


def _round_half_up(x: float) -> int:
    """Settlement-style rounding (half rounds up, toward +inf) rather than
    Python's banker's rounding, matching how an integer record is read off."""
    return math.floor(x + 0.5)


def quantize_to_grain(value_c: float, grain: str) -> tuple[float, str]:
    """Snap a continuous °C value onto the station's native reporting grain — the
    granularity the settlement record is actually written in — and return
    (equivalent °C, native-unit label). For a US ASOS the record is whole °F, so
    the value round-trips through whole Fahrenheit; elsewhere it is whole °C."""
    if grain == "F":
        f_int = _round_half_up(value_c * 9 / 5 + 32)
        return (f_int - 32) * 5 / 9, f"{f_int}°F"
    c_int = _round_half_up(value_c)
    return float(c_int), f"{c_int}°C"


def _column(daily: dict, var: str, model: str | None) -> list:
    """Open-Meteo returns suffixed keys for multi-model and plain keys for a
    single model; accept whichever is present."""
    if model and f"{var}_{model}" in daily:
        return daily[f"{var}_{model}"]
    return daily.get(var, [])


def _pair_series(daily: dict, model: str | None, qc: dict | None = None) -> DailySeries:
    times = daily.get("time", [])
    highs = _column(daily, "temperature_2m_max", model)
    lows = _column(daily, "temperature_2m_min", model)
    out: DailySeries = {}
    for i, day in enumerate(times):
        rh = highs[i] if i < len(highs) else None
        rl = lows[i] if i < len(lows) else None
        h, l = _clean_temp(rh), _clean_temp(rl)
        if qc is not None:                       # quality-control accounting
            for raw, cleaned in ((rh, h), (rl, l)):
                if raw is not None:              # a value was actually reported
                    qc["screened"] += 1
                    if cleaned is None:          # ...and it failed the sanity band
                        qc["rejected"] += 1
        if h is not None and l is not None:
            out[day] = (h, l)
    return out


class Sources:
    def __init__(self, http: SafeHTTPClient | None = None) -> None:
        self.http = http or SafeHTTPClient()
        # Data-assimilation quality-control tally (anomaly screening).
        self.qc = {"screened": 0, "rejected": 0}
        self._stations: list[dict] | None = None   # cached Meteostat station list

    def geocode(self, city: str) -> Place:
        name = validate_city(city)
        data = self.http.get_json(
            GEOCODE_URL,
            {"name": name, "count": 1, "language": "en", "format": "json"},
        )
        results = data.get("results")
        if not results or not isinstance(results, list):
            raise SecurityError(f"no city found matching {name!r}")
        r = results[0]
        try:
            return Place(
                name=str(r["name"]),
                country=str(r.get("country") or r.get("country_code") or "?"),
                latitude=float(r["latitude"]),
                longitude=float(r["longitude"]),
                timezone=str(r.get("timezone", "auto")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SecurityError("geocoding response missing expected fields") from exc

    def _common(self, place: Place) -> dict:
        return {
            "latitude": place.latitude,
            "longitude": place.longitude,
            "daily": "temperature_2m_max,temperature_2m_min",
            "timezone": place.timezone,
        }

    def fetch_live(self, model: str, place: Place, target: dt.date) -> tuple[float, float] | None:
        lead = (target - place_today(place)).days
        data = self.http.get_json(
            LIVE_URL,
            {**self._common(place), "models": model, "forecast_days": max(lead + 1, 1)},
        )
        series = _pair_series(data.get("daily", {}), model, self.qc)
        return series.get(target.isoformat())

    def fetch_weatherbit_forecast(self, place: Place,
                                  target: dt.date) -> tuple[float, float] | None:
        """Weatherbit daily forecast (high, low) °C for `target`, or None.

        RECOMMEND-ONLY tracked forecaster. Weatherbit is NOT an Open-Meteo model
        and exposes no free archive of its PAST forecasts, so its skill cannot be
        backtested instantly the way every council member's is; it is only logged
        and scored prospectively and must NEVER be voted into the live blend until
        it has earned >= MIN_SAMPLES paired days on real data.

        Key handling (the repo is public): the API key is read from the
        WEATHERBIT_API_KEY environment variable and passed as a request PARAMETER
        — it never appears in a base_url, and SafeHTTPClient logs only the bare
        host, so the key cannot leak into reports or logs. If the variable is
        unset/blank the source SILENTLY yields None, so the repo and the test
        suite stay runnable without a key.

        Honest-or-nothing: returns None on a missing field, an unparseable value,
        or any transport/security error, exactly like the Open-Meteo fetchers —
        it never fabricates a number, and never aborts the caller's run."""
        key = (os.environ.get("WEATHERBIT_API_KEY") or "").strip()
        if not key:
            return None
        lead = (target - place_today(place)).days
        days = min(max(lead + 1, 1), WEATHERBIT_MAX_DAYS)
        try:
            data = self.http.get_json(
                WEATHERBIT_FORECAST_URL,
                {"lat": place.latitude, "lon": place.longitude,
                 "days": days, "units": "M", "key": key},
            )
        except SecurityError:
            return None                     # optional source: degrade, never raise
        rows = data.get("data")
        if not isinstance(rows, list):
            return None
        want = target.isoformat()
        for row in rows:
            if not isinstance(row, dict) or row.get("valid_date") != want:
                continue
            hi = _clean_temp(row.get("max_temp"))
            lo = _clean_temp(row.get("min_temp"))
            return (hi, lo) if hi is not None and lo is not None else None
        return None

    def fetch_history_series(self, model: str, place: Place,
                             start: dt.date, end: dt.date) -> DailySeries:
        params = {**self._common(place),
                  "models": model,
                  "start_date": start.isoformat(),
                  "end_date": end.isoformat()}
        data = self._history_json_cached(params)
        return _pair_series(data.get("daily", {}), model, self.qc)

    def _history_json_cached(self, params: dict) -> dict:
        """Fetch the history JSON, transparently cached on disk. A fresh cache
        hit skips the network entirely (so a warmed city dodges the throttle);
        on a rate-limit/transport error we replay real cached history if we have
        it, else re-raise. Never fabricates — a cold miss under throttle fails."""
        key = _history_cache_key(HISTORY_URL, params)
        cached, age = _history_cache_read(key)
        if cached is not None and age is not None and age <= HISTORY_CACHE_TTL:
            return cached
        try:
            data = self.http.get_json(HISTORY_URL, params)
        except SecurityError:
            if cached is not None:        # real (possibly stale) beats nothing
                return cached
            raise
        _history_cache_write(key, data)
        return data

    def _cached_range_obs(self, tag: str, icao: str, start: dt.date, end: dt.date,
                          tz: str, raw_fetch):
        """Return parsed observations for [start, end], caching the IMMUTABLE past
        on disk and ALWAYS fetching the recent tail fresh.

        Station archives (WU, IEM) for a settled day never change, so re-fetching a
        160-day window on every run/gate/intraday-refresh is pure waste. This caches
        [start, today-OBS_CACHE_MARGIN] (one keyed blob of parsed rows) and fetches
        [.. , end] live, then merges and time-sorts. The margin guarantees a live
        running max is never served stale. `raw_fetch(a, b) -> list[tuple]` is the
        un-cached fetch-and-parse core; rows are JSON-round-tripped, so any tuple
        arity ((ts,c) or (date,max,min)) is preserved. Cache write is best-effort —
        a failure degrades to a live fetch, never an error."""
        cutoff = dt.date.today() - OBS_CACHE_MARGIN     # everything <= cutoff is immutable
        out: list = []
        past_end = min(end, cutoff)
        if start <= past_end:
            key = _history_cache_key("obs:" + tag, {
                "icao": (icao or "").upper(), "tz": tz or "",
                "start": start.isoformat(), "end": past_end.isoformat()})
            # POISONING GUARD: raw_fetch returns [] (or a thin partial) on network failure,
            # and caching that starves every consumer for the 7-day TTL — the 2026-07-02
            # dead-DNS launchd window cached an EMPTY blob and silently broke the WU-native
            # validation gate ("insufficient history"). A blob is only trusted (and only
            # WRITTEN) if it covers >=25% of the requested days; anything thinner is a miss
            # and is refetched, so a poisoned key self-heals on the next healthy run.
            expected_days = (past_end - start).days + 1
            floor = max(1, expected_days // 4)
            cached, age = _history_cache_read(key)
            rows = ([tuple(r) for r in cached.get("obs", [])]
                    if cached is not None and age is not None and age <= HISTORY_CACHE_TTL
                    else None)
            if rows is not None and _obs_days_covered(rows) >= floor:
                out.extend(rows)
            else:
                past = list(raw_fetch(start, past_end))
                if _obs_days_covered(past) >= floor:
                    _history_cache_write(key, {"obs": [list(x) for x in past]})
                out.extend(past)
        fresh_start = max(start, cutoff + dt.timedelta(days=1))   # recent tail, never cached
        if fresh_start <= end:
            out.extend(raw_fetch(fresh_start, end))
        out.sort()
        return out

    def fetch_archive_series(self, place: Place,
                             start: dt.date, end: dt.date) -> DailySeries:
        data = self.http.get_json(
            ARCHIVE_URL,
            {**self._common(place),
             "start_date": start.isoformat(),
             "end_date": end.isoformat()},
        )
        return _pair_series(data.get("daily", {}), None, self.qc)

    def fetch_ensemble_history_means(self, model: str, place: Place,
                                     start: dt.date, end: dt.date) -> DailySeries:
        """Per-day mean of all perturbed members' (high, low) over a past
        window, so the ensemble mean can be backtested against ERA5 exactly like
        a deterministic member. Days with no member values are simply absent —
        on the free tier the ensemble archive is sparse, which is itself the
        signal that the ensemble cannot be trusted into the blended number."""
        data = self.http.get_json(
            ENSEMBLE_URL,
            {**self._common(place), "models": model,
             "start_date": start.isoformat(), "end_date": end.isoformat()},
        )
        daily = data.get("daily", {})
        times = daily.get("time", [])
        max_cols = [v for k, v in daily.items()
                    if isinstance(v, list) and k.startswith("temperature_2m_max")]
        min_cols = [v for k, v in daily.items()
                    if isinstance(v, list) and k.startswith("temperature_2m_min")]
        out: DailySeries = {}
        for i, day in enumerate(times):
            hs = [_clean_temp(c[i]) for c in max_cols if i < len(c)]
            ls = [_clean_temp(c[i]) for c in min_cols if i < len(c)]
            hs = [x for x in hs if x is not None]
            ls = [x for x in ls if x is not None]
            if hs and ls:
                out[day] = (sum(hs) / len(hs), sum(ls) / len(ls))
        return out

    # -- Stage 1: Observation ------------------------------------------------
    def fetch_current(self, place: Place) -> dict:
        """Current assimilated surface conditions plus 24h pressure tendency."""
        data = self.http.get_json(
            LIVE_URL,
            {"latitude": place.latitude, "longitude": place.longitude,
             "timezone": place.timezone,
             "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,surface_pressure",
             "hourly": "surface_pressure", "past_days": 1, "forecast_days": 1},
        )
        cur = data.get("current", {})
        out = {"time": cur.get("time")}
        for k in ("temperature_2m", "relative_humidity_2m",
                  "wind_speed_10m", "surface_pressure"):
            v = cur.get(k)
            out[k] = float(v) if isinstance(v, (int, float)) else None

        # Barometric tendency: falling pressure signals approaching storms.
        out["pressure_change_24h"] = None
        hourly = data.get("hourly", {})
        series = {t: v for t, v in zip(hourly.get("time", []),
                                       hourly.get("surface_pressure", []))
                  if isinstance(v, (int, float))}
        now, p_now = cur.get("time"), out.get("surface_pressure")
        if now and p_now is not None:
            try:
                key = (dt.datetime.fromisoformat(now)
                       - dt.timedelta(hours=24)).strftime("%Y-%m-%dT%H:00")
                if key in series:
                    out["pressure_change_24h"] = round(p_now - series[key], 1)
            except ValueError:
                pass
        return out

    # -- Spatial representativeness: how much the grid field varies locally ---
    def fetch_grid_neighbors(self, place: Place, start: dt.date, end: dt.date,
                             offset_deg: float = 0.25) -> list[DailySeries]:
        """Daily max/min at the centre grid cell and its four cardinal
        neighbours, each offset by `offset_deg` (≈ one ERA5 grid step). The
        across-cell spread is a measurable proxy for how far a point station
        inside the cell (e.g. an official observatory) can sit from the value
        the grid reports — large over coast/terrain/urban edges, small over
        homogeneous terrain. Bad values are dropped by the plausibility band;
        not counted in the operational QC tally."""
        deltas = [(0.0, 0.0), (offset_deg, 0.0), (-offset_deg, 0.0),
                  (0.0, offset_deg), (0.0, -offset_deg)]
        out: list[DailySeries] = []
        for dlat, dlon in deltas:
            data = self.http.get_json(
                ARCHIVE_URL,
                {"latitude": place.latitude + dlat,
                 "longitude": place.longitude + dlon,
                 "daily": "temperature_2m_max,temperature_2m_min",
                 "timezone": place.timezone,
                 "start_date": start.isoformat(), "end_date": end.isoformat()},
            )
            out.append(_pair_series(data.get("daily", {}), None, None))
        return out

    # -- Station observations (Meteostat): point truth a record settles on ----
    def _load_stations(self) -> list[dict]:
        if self._stations is None:
            txt = self.http.get_gzip_text(STATIONS_URL)
            try:
                data = json.loads(txt)
            except ValueError as exc:
                raise SecurityError("meteostat station list was not valid JSON") from exc
            self._stations = data if isinstance(data, list) else []
        return self._stations

    def nearest_stations(self, place: Place, max_deg: float = 0.75,
                         limit: int = 5) -> list[Station]:
        """Stations within `max_deg` of the point that have *some* daily history,
        nearest first. We do not trust the (lagging) inventory dates to decide
        recency here — the caller probes the actual daily file and lets real
        overlap with the backtest window decide eligibility."""
        cand: list[Station] = []
        for s in self._load_stations():
            loc = s.get("location") or {}
            lat, lon = loc.get("latitude"), loc.get("longitude")
            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue
            daily_inv = ((s.get("inventory") or {}).get("daily") or {})
            if not daily_inv.get("start"):        # never carried daily data
                continue
            if abs(lat - place.latitude) > max_deg or abs(lon - place.longitude) > max_deg:
                continue
            ids = s.get("identifiers") or {}
            elev = loc.get("elevation")
            cand.append(Station(
                id=str(s.get("id")),
                name=str((s.get("name") or {}).get("en") or s.get("id")),
                wmo=(str(ids["wmo"]) if ids.get("wmo") else None),
                icao=(str(ids["icao"]) if ids.get("icao") else None),
                latitude=float(lat), longitude=float(lon),
                elevation=float(elev) if isinstance(elev, (int, float)) else None,
                distance_km=_haversine_km(place.latitude, place.longitude, lat, lon),
            ))
        cand.sort(key=lambda st: st.distance_km)
        return cand[:limit]

    def station_by_id(self, station_id: str) -> Station | None:
        """Full Station (name + ICAO + real coordinates) for a stored station_id,
        from the Meteostat inventory, or None if absent. verify() uses this to
        recover the IDENTITY of verdict rows logged before that identity was
        persisted — the settlement overlays in fetch_station_daily gate on it (the
        HKO Observatory by a name token + geography, London City by the EGLC ICAO),
        so an id-only Station silently skips them. Not a city->station guess: it
        resolves the exact station the verdict already anchored on, by its own id."""
        for s in self._load_stations():
            if str(s.get("id")) != str(station_id):
                continue
            loc = s.get("location") or {}
            ids = s.get("identifiers") or {}
            lat, lon = loc.get("latitude"), loc.get("longitude")
            elev = loc.get("elevation")
            return Station(
                id=str(s.get("id")),
                name=str((s.get("name") or {}).get("en") or s.get("id")),
                wmo=(str(ids["wmo"]) if ids.get("wmo") else None),
                icao=(str(ids["icao"]) if ids.get("icao") else None),
                latitude=float(lat) if isinstance(lat, (int, float)) else 0.0,
                longitude=float(lon) if isinstance(lon, (int, float)) else 0.0,
                elevation=float(elev) if isinstance(elev, (int, float)) else None,
                distance_km=0.0,
            )
        return None

    def fetch_station_daily(self, station: Station) -> DailySeries:
        """Full daily (high, low) history for one station from its bulk CSV.
        CSV columns: date,tavg,tmin,tmax,prcp,snow,wdir,wspd,wpgt,pres,tsun.
        Values are plausibility-screened (bad ones dropped) but — like the bulk
        climatology and neighbour fetches — are *not* added to the operational QC
        tally: the file spans decades and several candidate stations may be
        probed, which would swamp the per-run anomaly count."""
        # station.id arrives from the fetched Meteostat station list — a hostile
        # or drifted id ("../", "?", control chars) would rewrite the URL path
        # or raise outside the SecurityError taxonomy. Alphanumeric or nothing.
        sid = str(station.id or "")
        if not sid.isalnum() or len(sid) > 12:
            return {}
        txt = self.http.get_gzip_text(STATION_DAILY_URL.format(id=sid))
        out: DailySeries = {}
        for line in txt.splitlines():
            cols = line.split(",")
            if len(cols) < 4:
                continue
            day = cols[0]
            if len(day) != 10 or day[4] != "-" or day[7] != "-":
                continue
            low = _clean_temp_cell(cols[2])      # tmin
            high = _clean_temp_cell(cols[3])     # tmax
            if high is not None and low is not None:
                out[day] = (high, low)
        # The Hong Kong Observatory's Meteostat file ends in 1992. Overlay the
        # modern HKO open-data record (high+low) so this station is current enough
        # to anchor a verdict on — the airport is then only a cross-reference.
        # Recent open-data days win over any stale Meteostat overlap.
        if self.is_hko_observatory(station):
            modern = self.hko_truth_series(dt.date.today())
            if modern:
                out = {**out, **modern}
        # London City Airport (EGLC): the Meteostat "EGLC0" file is the Abbey Wood
        # gauge ~17 km away and weeks-to-months stale, yet it carries the EGLC ICAO
        # and is what the London market settles on by *name*. Overlay the modern
        # IEM ASOS METAR record reconstructed from the airport's own sensor — the
        # identical settlement-grade record run.py uses for its reference — so the
        # council backtests against the same instrument the market resolves on.
        # Recent METAR days win; older days keep the Meteostat value.
        else:
            # Airports whose Meteostat bulk file is stale/distant but which carry a live
            # IEM ASOS METAR record we can overlay (the EGLC pattern, generalised) —
            # membership is _IEM_OVERLAY_TZ (EGLC/OPKC/OEJN). KSFO is deliberately NOT
            # overlaid: SF anchors on its live Wunderground oracle feed (see line ~65).
            # Recent METAR days win; older days keep the Meteostat value.
            ov_tz = _IEM_OVERLAY_TZ.get((station.icao or "").upper())
            if ov_tz:
                modern = self.iem_overlay_truth_series((station.icao or "").upper(),
                                                       ov_tz, dt.date.today())
                if modern:
                    out = {**out, **modern}
        return out

    def _fetch_hko_dataset(self, data_type: str, years: list[int]) -> dict[str, float]:
        """Recent daily values (date -> °C) for one HKO open-data climate dataset
        at the Observatory HQ: CLMMAXT (daily max), CLMMINT (daily min), or
        CLMTEMP (daily mean). One CSV per year; the file has two title lines, a
        header, then rows `YYYY,M,D,value,flag`. Only rows whose completeness flag
        is 'C' are kept; '#'/'***' (incomplete / unavailable) are dropped. Values
        are °C, plausibility-screened. Not added to the operational QC tally."""
        out: dict[str, float] = {}
        for year in years:
            try:
                txt = self.http.get_text(HKO_OPENDATA_URL, {
                    "dataType": data_type, "rformat": "csv",
                    "station": "HKO", "year": year,
                })
            except SecurityError:
                continue                       # a missing year must not abort
            for line in txt.splitlines():
                cols = line.split(",")
                if len(cols) < 5:
                    continue
                y, m, d = cols[0].strip(), cols[1].strip(), cols[2].strip()
                if not (y.isdigit() and m.isdigit() and d.isdigit()):
                    continue                   # title/header lines
                if cols[4].strip().strip('"') != "C":
                    continue                   # keep only complete days
                val = _clean_temp_cell(cols[3])
                if val is None:
                    continue
                out[f"{int(y):04d}-{int(m):02d}-{int(d):02d}"] = val
        return out

    def is_hko_observatory(self, station: Station) -> bool:
        """True iff this station is the Hong Kong Observatory HQ — recognised by a
        name token *and* geography (within HKO_MATCH_RADIUS_KM of the Observatory
        HQ), never a hardcoded city/station table. The nearby VHHH airport is
        therefore excluded. This is the gate for serving the modern HKO open-data
        record in place of the station's 1992-truncated Meteostat file."""
        if "observatory" not in (station.name or "").lower():
            return False
        return _haversine_km(station.latitude, station.longitude,
                             HKO_HQ_LAT, HKO_HQ_LON) <= HKO_MATCH_RADIUS_KM

    def hko_daily_extract_series(self, target: dt.date,
                                 back_months: int = 1) -> DailySeries:
        """Most-recent daily (high, low) at the Hong Kong Observatory HQ from the
        official "Daily Extract of Meteorological Observations" — the table whose
        "Absolute Daily Maximum" column the Hong Kong market settles on. One JSON
        file per civil month; we read the target month plus `back_months` prior
        for month-boundary coverage. Only days with BOTH a numeric abs-max and
        abs-min are returned. Any fetch/parse failure for a month is skipped
        silently so a missing file never aborts settlement (the open-data record
        still backs the long history)."""
        out: DailySeries = {}
        y, m = target.year, target.month
        for _ in range(back_months + 1):
            try:
                data = self.http.get_json(
                    HKO_DAILY_EXTRACT_URL.format(ym=f"{y:04d}{m:02d}"), {})
            except Exception:
                data = None
            for block in ((data or {}).get("stn") or {}).get("data", []):
                month = block.get("month")
                mo = int(month) if str(month).isdigit() else m
                if mo != m:
                    # A block for another month (payload drift, or a December
                    # block inside a January file) would be keyed with THIS
                    # loop iteration's year — a mismatched date. Skip it.
                    continue
                for row in block.get("dayData", []):
                    if not row or not str(row[0]).isdigit() \
                            or len(row) <= HKO_DX_ABS_MIN_COL:
                        continue
                    high = _clean_temp_cell(str(row[HKO_DX_ABS_MAX_COL]))
                    low = _clean_temp_cell(str(row[HKO_DX_ABS_MIN_COL]))
                    if high is None or low is None:
                        continue
                    out[f"{y:04d}-{mo:02d}-{int(row[0]):02d}"] = (high, low)
            m -= 1
            if m == 0:
                m, y = 12, y - 1
        return out

    def hko_truth_series(self, target: dt.date, back_years: int = 4) -> DailySeries:
        """Modern daily (high, low) record at the Hong Kong Observatory HQ — the
        settlement-grade truth the council anchors a Hong Kong verdict on. The long
        history comes from the HKO open-data API (daily high CLMMAXT, low CLMMINT;
        only dates with BOTH complete). The official Daily Extract is then overlaid
        on top so the most recent settled days — which the monthly climate API lags
        by ~weeks — are present and carry the exact "Absolute Daily Maximum" the
        market settles on. Fresher Daily-Extract days win; its fetch is best-effort
        so a failure leaves the open-data record intact."""
        years = list(range(target.year - back_years, target.year + 1))
        highs = self._fetch_hko_dataset("CLMMAXT", years)
        lows = self._fetch_hko_dataset("CLMMINT", years)
        series = {d: (highs[d], lows[d]) for d in highs.keys() & lows.keys()}
        try:
            extract = self.hko_daily_extract_series(target)
        except Exception:
            extract = {}
        return {**series, **extract}

    def recent_station_series(self, station: Station, target: dt.date,
                              back_years: int = 3) -> DailySeries | None:
        """Recent daily series for a settlement station when — and only when — that
        station is the Hong Kong Observatory, whose Meteostat archive ends in 1992.
        Returns {date -> (high, low)} from the HKO open-data API, or None when the
        station is not the Observatory or the API yields nothing."""
        if not self.is_hko_observatory(station):
            return None
        series = self.hko_truth_series(target, back_years)
        return series or None

    def hko_current(self) -> dict | None:
        """Live current conditions at the Hong Kong Observatory HQ from the HKO
        real-time open-data feed (rhrread) — the same settlement-grade instrument
        the daily HKO record settles on. Returns the parsed reading
        ({temperature_2m, relative_humidity_2m, record_time}) or None on any
        failure, so a caller can fall back to the grid 'current' without raising.
        One extra request, made only for the HKO-anchored city."""
        try:
            data = self.http.get_json(
                HKO_RHRREAD_URL, {"dataType": "rhrread", "lang": "en"})
        except Exception:
            return None
        parsed = _parse_hko_rhrread(data)
        if parsed is None:
            return None
        parsed["temperature_source"] = (
            "Hong Kong Observatory (live rhrread, whole-degree)")
        # Prefer the finer 1-minute 0.1 °C reading from the SAME Observatory gauge
        # (rhrread rounds to whole degrees). Humidity stays from rhrread — the
        # 1-minute feed is temperature-only. One extra request to the same
        # allowlisted host; on any failure we silently keep the whole-degree value.
        try:
            fine = _parse_hko_1min_temp(self.http.get_text(HKO_1MIN_TEMP_URL))
        except Exception:
            fine = None
        if fine is not None:
            parsed["temperature_2m"] = fine["temperature_2m"]
            parsed["record_time"] = fine.get("record_time") or parsed.get("record_time")
            parsed["temperature_source"] = (
                "Hong Kong Observatory (live 1-minute mean, 0.1°C)")
        return parsed

    def iem_overlay_truth_series(self, icao: str, timezone: str, target: dt.date,
                                 back_years: int = 2) -> DailySeries:
        """Modern daily (high, low) at an IEM-overlay airport (_IEM_OVERLAY_TZ:
        EGLC/OPKC/OEJN — KSFO deliberately excluded, it anchors on WU) reconstructed
        from raw IEM ASOS METAR over the local calendar day — the settlement-grade sensor
        the market resolves on, replacing the stale/distant Meteostat bulk file. Spans the
        last `back_years` up to `target`; days with too few obs fall back to Meteostat. One
        cached request; returns {} on any failure so truth resolution never aborts."""
        start = dt.date(target.year - back_years, 1, 1)
        try:
            res = self.fetch_metar_daily(icao, start, target, timezone)
        except Exception:
            return {}
        return dict(res.get("daily", {}))

    def fetch_metar_observations(self, icao: str, start: dt.date, end: dt.date,
                                 timezone: str) -> list[tuple[str, float]]:
        """Raw timestamped air-temperature obs (°C) from the IEM ASOS METAR
        archive — the sub-daily record underneath fetch_metar_daily. Returns a
        time-sorted list of (local_timestamp_iso, temp_c).

        The native cadence is the station's *reporting interval* (ASOS routine
        METAR ~hourly, plus SPECIs) — emphatically NOT per-second or per-minute.
        A caller asking for a finer timescale than this cadence is asking for
        truth that was never measured; such scales must be reported as
        unobserved, never interpolated into fabricated readings.

        The immutable past is served from the on-disk obs cache; the recent tail
        (within OBS_CACHE_MARGIN of now) is always re-fetched, so a live intraday
        running max is never stale."""
        return self._cached_range_obs(
            "metar", icao, start, end, timezone or "",
            lambda a, b: self._fetch_metar_raw(icao, a, b, timezone))

    def _fetch_metar_raw(self, icao: str, start: dt.date, end: dt.date,
                         timezone: str) -> list[tuple[str, float]]:
        # The IEM archive treats day2 as EXCLUSIVE. Compensate HERE, once, so
        # every caller's (start, end) is end-INCLUSIVE — previously only
        # eglc compensated, which left a structural one-day hole at the
        # obs-cache cutoff (past segment ended at cutoff−1, fresh tail started
        # at cutoff+1) and truncated every other caller's final day.
        tz = timezone if "/" in (timezone or "") else "Etc/UTC"
        iem_end = end + dt.timedelta(days=1)
        txt = self.http.get_text(METAR_URL, {
            "station": icao,
            "data": ["tmpc"],
            "year1": start.year, "month1": start.month, "day1": start.day,
            "year2": iem_end.year, "month2": iem_end.month, "day2": iem_end.day,
            "tz": tz, "format": "onlycomma", "latlon": "no",
            "missing": "empty", "trace": "empty",
            "report_type": [3, 4],
        })
        obs: list[tuple[str, float]] = []
        for line in txt.splitlines()[1:]:           # skip header
            p = line.split(",")
            if len(p) < 3:
                continue
            ts = p[1].strip()
            if len(ts) < 16 or ts[4] != "-" or ts[7] != "-":
                continue
            c = _clean_temp_cell(p[2])
            if c is None:
                continue
            obs.append((ts, c))
        obs.sort()
        return obs

    def wunderground_daily_max(self, icao: str, target: dt.date,
                               timezone: str | None = None) -> dict | None:
        """The day's max from the Weather Company / Wunderground record for an
        airport — the ACTUAL market settlement oracle (the contract names this feed).

        WU stores the station history in whole °F, so we read °F, take the day's max,
        and convert to °C — the value the contract rounds to whole °C. Returns
        {max_f, max_c, n_obs} or None on any failure (caller then falls back to the
        IEM METAR cross-reference). Read-only; apiKey rides in params.

        WP-2 (served-number campaign): the max is taken over the station's LOCAL civil day — the day
        the contract settles on — NOT over whatever the endpoint's UTC-ish `startDate` window returns.
        All active WU stations are off-UTC, so a naive max() over the endpoint window can pick up a
        temperature from the ADJACENT local day (a straddle), and this value is the phantom-cap ceiling
        feeding `_fuse_live_floor`. So: fetch ±1 UTC day to fully cover the local day whatever the
        offset, then regroup each obs onto its local day via `valid_time_gmt` + ZoneInfo (mirroring
        `_wu_daily_raw`) and max over only `target`'s local obs. `timezone=None` degrades to UTC."""
        loc = WU_LOCATION.get((icao or "").upper())
        if loc is None:
            return None
        try:
            zone = ZoneInfo(timezone) if timezone else ZoneInfo("UTC")
        except Exception:
            zone = ZoneInfo("UTC")
        try:
            data = self.http.get_json(
                WU_HISTORY_URL.format(loc=loc),
                {"apiKey": WU_API_KEY, "units": "e",
                 "startDate": (target - dt.timedelta(days=1)).strftime("%Y%m%d"),
                 "endDate": (target + dt.timedelta(days=1)).strftime("%Y%m%d")})
        except Exception:
            return None
        tgt_iso = target.isoformat()
        temps = []
        for o in (data.get("observations") or []):
            vt = o.get("valid_time_gmt")
            t = _wu_temp_f(o.get("temp"))               # screened, bool-excluded
            if t is None or isinstance(vt, bool) or not isinstance(vt, (int, float)):
                continue
            local = dt.datetime.fromtimestamp(vt, tz=dt.timezone.utc).astimezone(zone)
            if local.date().isoformat() == tgt_iso:      # LOCAL civil day only (no straddle)
                temps.append(t)
        if not temps:
            return None
        max_f = max(temps)
        return {"max_f": float(max_f), "max_c": (max_f - 32) * 5.0 / 9.0,
                "n_obs": len(temps)}

    def wunderground_current_v3(self, icao: str) -> dict | None:
        """Freshest read of the settlement station from the oracle's v3 current-conditions
        feed: {'cur_f','max24_f','valid_local'}. ~10-min latency vs ~30-45min for the v1
        history rows, and max24_f is the station's own running 24h register — it sees
        BETWEEN-obs spikes the half-hourly listing misses (07-04: register 92°F, rows 91).
        Consumed ONLY through _fuse_live_floor (floor-raise-only). None on any failure."""
        geo = WU_GEO.get((icao or "").upper())
        if geo is None:
            return None
        try:
            d = self.http.get_json(V3_CURRENT_URL,
                                   {"geocode": f"{geo[0]},{geo[1]}", "units": "e",
                                    "language": "en-US", "format": "json",
                                    "apiKey": WU_API_KEY})
        except Exception:
            return None
        t = _wu_temp_f(d.get("temperature"))            # screened, bool-excluded
        m = _wu_temp_f(d.get("temperatureMax24Hour"))
        if t is None:
            return None
        return {"cur_f": t, "max24_f": m,
                "valid_local": d.get("validTimeLocal")}

    def twc_forecast_daily(self, lat: float, lon: float, target: dt.date,
                           tz: str, grain: str) -> dict | None:
        """The Weather Company's OWN published daily forecast for a station, requested at the
        SETTLEMENT ANCHOR's coordinates (fc_lat/fc_lon — NOT the city centroid, so the reading
        isolates forecast bias vs the oracle rather than smuggling in an urban-gradient location
        mismatch). Returns {'fc_high','fc_low','raw_day_label','grain'} in the market's native
        grain for `target` (that city's LOCAL calendar day), or None if unavailable.

        A CROSS-REFERENCE ONLY: this is the settlement oracle (WU/TWC) forecasting its own
        station — never a vote, never settlement. Fetched in whole-°F (units='e', matching the
        WU record's °F-native settlement grain) and converted once at the edge — °C for basket
        cities, °F where the market settles °F — so TWC and the oracle compare on ONE basis (no
        silent grain mixing). The day is matched UTC-independently on validTimeLocal[:10] (the
        Phase-0 probe verified each element carries the station's LOCAL offset).

        KEY DEPENDENCY: rides the SAME public web key as the WU truth path (WU_API_KEY); a key
        rotation silently kills BOTH truth reads AND this cross-reference — so the soft-failure
        tag here is 'twc_forecast', DISTINCT from the truth path's 'wu_key_or_endpoint', letting
        healthcheck tell which product broke. A transport error or a structurally malformed
        response records that soft failure and returns None; a well-formed response that simply
        does not yet cover `target` returns None WITHOUT a soft failure (not published yet).
        Never guesses a temperature."""
        try:
            d = self.http.get_json(
                TWC_FORECAST_URL,
                {"geocode": f"{lat},{lon}", "format": "json", "units": "e",
                 "language": "en-US", "apiKey": WU_API_KEY})
        except Exception as exc:
            failures.record_soft_failure("twc_forecast", exc)
            return None
        valid = d.get("validTimeLocal") if isinstance(d, dict) else None
        highs = d.get("calendarDayTemperatureMax") if isinstance(d, dict) else None
        lows = d.get("calendarDayTemperatureMin") if isinstance(d, dict) else None
        if not (isinstance(valid, list) and isinstance(highs, list) and isinstance(lows, list)):
            failures.record_soft_failure(
                "twc_forecast", ValueError("TWC response missing calendar-day arrays"))
            return None
        # Convert once at the edge: °F stays °F where the market settles °F; else whole-°F → °C.
        to_native = ((lambda f: f) if str(grain).upper().startswith("F")
                     else (lambda f: (f - 32.0) * 5.0 / 9.0))
        tgt = target.isoformat()
        for i, v in enumerate(valid):
            if isinstance(v, str) and v[:10] == tgt and i < len(highs):
                hF = highs[i]
                if not isinstance(hF, (int, float)) or isinstance(hF, bool):
                    return None                       # day present but max not set — not a guess
                lF = lows[i] if i < len(lows) else None
                return {
                    "fc_high": to_native(float(hF)),
                    "fc_low": (to_native(float(lF)) if isinstance(lF, (int, float))
                               and not isinstance(lF, bool) else None),
                    "raw_day_label": v, "grain": grain,
                }
        return None                                   # target not in the forecast horizon yet

    def wunderground_daily_series(self, icao: str, start: dt.date, end: dt.date,
                                  timezone: str) -> DailySeries:
        """Daily (max_c, min_c) from the Wunderground / Weather Company station
        record over [start, end], grouped by the station's LOCAL calendar day —
        the exact feed the market settles on, used as Manila's backtest TRUTH.

        Unlike the Meteostat bulk archive (which trails real time by ~3 months and
        forces the backtest window out of season) this feed is CURRENT, so the
        window stays in-season and every member is scored against the settlement
        oracle itself. WU stores whole °F; each local day's max/min are taken in °F
        then converted to °C, matching how the contract reads the record. Days with
        fewer than WU_MIN_DAY_OBS obs (a partial final day) are dropped so an
        incomplete day never understates the peak. The range is chunked to stay
        within one API window per call. Returns {} on total failure, so the caller
        falls back to the station/grid truth rather than anchoring on nothing.
        Immutable past from the obs cache; the recent tail is always re-fetched."""
        rows = self._cached_range_obs(
            "wu_daily", icao, start, end, timezone or "",
            lambda a, b: self._wu_daily_raw(icao, a, b, timezone))
        return {d: (mx, mn) for d, mx, mn in rows}

    def _wu_chunked_obs(self, icao: str, start: dt.date, end: dt.date,
                        timezone: str):
        """Shared WU station-history core behind _wu_daily_raw/_wu_hourly_raw:
        chunked-fetch (one 30-day API window per call), local-zone regroup, and
        the _wu_temp_f plausibility screen with qc accounting. Yields
        (local datetime, screened temp °F) in feed order. Unknown zone degrades
        to UTC; a dead chunk is a recorded soft failure and yields nothing;
        a throttled key (RateLimitError) fails LOUD so the council never
        silently re-anchors on a lagged fallback feed."""
        loc = WU_LOCATION.get((icao or "").upper())
        if loc is None:
            return
        try:
            zone = ZoneInfo(timezone)
        except Exception:
            zone = ZoneInfo("UTC")
        cur = start
        while cur <= end:
            chunk_end = min(cur + dt.timedelta(days=30), end)
            try:
                data = self.http.get_json(
                    WU_HISTORY_URL.format(loc=loc),
                    {"apiKey": WU_API_KEY, "units": "e",
                     "startDate": cur.strftime("%Y%m%d"),
                     "endDate": chunk_end.strftime("%Y%m%d")})
            except RateLimitError:
                raise                       # retryable throttle — never swallow
            except Exception as exc:
                failures.record_soft_failure("wu_history_chunk", exc)
                data = {}
            for o in (data.get("observations") or []):
                raw_t = o.get("temp")
                vt = o.get("valid_time_gmt")
                t = _wu_temp_f(raw_t)
                if t is None or isinstance(vt, bool) or not isinstance(vt, (int, float)):
                    if isinstance(raw_t, (int, float)) and not isinstance(raw_t, bool):
                        self.qc["rejected"] += 1     # numeric but implausible
                    continue
                self.qc["screened"] += 1
                local = dt.datetime.fromtimestamp(vt, tz=dt.timezone.utc).astimezone(zone)
                yield local, t
            cur = chunk_end + dt.timedelta(days=1)

    def _wu_daily_raw(self, icao: str, start: dt.date, end: dt.date,
                      timezone: str) -> list[tuple[str, float, float]]:
        by_date: dict[str, list[float]] = {}
        for local, t in self._wu_chunked_obs(icao, start, end, timezone):
            by_date.setdefault(local.date().isoformat(), []).append(t)
        rows: list[tuple[str, float, float]] = []
        for d, temps in by_date.items():
            if len(temps) < WU_MIN_DAY_OBS:       # incomplete day — untrustworthy extreme
                continue
            rows.append((d, (max(temps) - 32.0) * 5.0 / 9.0,
                         (min(temps) - 32.0) * 5.0 / 9.0))
        return rows

    def wunderground_current(self, icao: str, timezone: str) -> dict | None:
        """The latest Wunderground / Weather Company observation for an airport —
        the settlement sensor's own most recent reading, so a WU-anchored verdict's
        live 'now' comes from the oracle feed rather than an Open-Meteo grid cell
        that can sit ~2 °C away. WU temps are whole °F → °C. Returns
        {temperature_2m, record_time} or None on any failure (caller keeps grid)."""
        loc = WU_LOCATION.get((icao or "").upper())
        if loc is None:
            return None
        try:
            zone = ZoneInfo(timezone)
            today = dt.datetime.now(zone).date()
        except Exception:
            return None
        try:
            data = self.http.get_json(
                WU_HISTORY_URL.format(loc=loc),
                {"apiKey": WU_API_KEY, "units": "e",
                 "startDate": today.strftime("%Y%m%d")})
        except Exception:
            return None
        obs = [o for o in (data.get("observations") or [])
               if _wu_temp_f(o.get("temp")) is not None
               and isinstance(o.get("valid_time_gmt"), (int, float))
               and not isinstance(o.get("valid_time_gmt"), bool)]
        if not obs:
            return None
        latest = max(obs, key=lambda o: o["valid_time_gmt"])
        rt = dt.datetime.fromtimestamp(latest["valid_time_gmt"],
                                       tz=dt.timezone.utc).astimezone(zone)
        return {"temperature_2m": (float(latest["temp"]) - 32.0) * 5.0 / 9.0,
                "record_time": rt.isoformat()}

    def nws_current(self, icao: str) -> dict | None:
        """Latest ASOS/METAR observation directly from the National Weather Service
        API. This is the same sensor the IEM archive ingests, but the NWS endpoint
        updates faster (~minutes) than the IEM historical archive. Returns
        {temperature_2m, record_time} or None on any failure."""
        if not icao:
            return None
        url = f"https://api.weather.gov/stations/{icao.upper()}/observations/latest"
        try:
            data = self.http.get_json(url, {})
        except Exception:
            return None
        props = data.get("properties", {})
        ts = props.get("timestamp")
        temp = props.get("temperature", {})
        if not isinstance(ts, str) or temp.get("value") is None:
            return None
        try:
            c = float(temp["value"])
        except (TypeError, ValueError):
            return None
        return {"temperature_2m": c, "record_time": ts}

    def wunderground_hourly_observations(self, icao: str, start: dt.date,
                                         end: dt.date, timezone: str) -> list[tuple[str, float]]:
        """Sub-daily air-temperature obs (°C) from the Wunderground / Weather
        Company station record, shaped EXACTLY like fetch_metar_observations —
        a time-sorted list of (local 'YYYY-MM-DD HH:MM', temp_c) — so the
        intraday-ceiling lever can read the running max, learn the remaining-rise,
        AND settle on ONE feed: the settlement oracle itself.

        Why this exists: the lever was backtested on IEM whole-°C METAR but the
        market settles on WU whole-°F → °C. The coarser °C grain HIDES the °F
        boundary fragility that actually flips buckets, so the IEM gate ran ~12pts
        optimistic at the peak hour (Singapore 14:00: IEM 91% vs WU-faithful 78%).
        Reading the running max on the same feed it settles on removes that gap.
        WU cadence ~30 min; whole °F → °C. Range chunked to one API window per
        call. Returns [] on total failure so the caller can fall back to IEM.
        Immutable past is served from the obs cache; the recent tail is always
        re-fetched so the live running max is never stale."""
        return self._cached_range_obs(
            "wu_hourly", icao, start, end, timezone or "",
            lambda a, b: self._wu_hourly_raw(icao, a, b, timezone))

    def _wu_hourly_raw(self, icao: str, start: dt.date, end: dt.date,
                       timezone: str) -> list[tuple[str, float]]:
        out: list[tuple[str, float]] = [
            (local.strftime("%Y-%m-%d %H:%M"), (t - 32.0) * 5.0 / 9.0)
            for local, t in self._wu_chunked_obs(icao, start, end, timezone)]
        out.sort()
        return out

    def iem_metar_current(self, icao: str, timezone: str) -> dict | None:
        """Live current air temperature at any IEM-overlay airport — the most
        recent raw IEM ASOS METAR, i.e. the settlement sensor's own latest
        reading, so a verdict's 'now' is the airport gauge the market resolves on
        rather than an Open-Meteo grid-cell proxy. Returns {temperature_2m,
        record_time} or None on any failure (caller then keeps the grid
        'current'). End dates are inclusive (the raw fetcher compensates for the
        IEM archive's exclusive day2 — no per-caller +1 anymore)."""
        today = dt.date.today()
        try:
            obs = self.fetch_metar_observations(
                icao, today - dt.timedelta(days=1),
                today, timezone)
        except Exception:
            return None
        if not obs:
            return None
        ts, c = obs[-1]                              # most recent observation
        temp = _clean_temp(c)
        if temp is None:
            return None
        record_time = ts.replace(" ", "T", 1) if " " in ts else ts
        return {"temperature_2m": temp, "record_time": record_time}

    def fetch_metar_daily(self, icao: str, start: dt.date, end: dt.date,
                          timezone: str) -> dict:
        """Daily max/min reconstructed from raw airport METAR (IEM ASOS archive)
        — the settlement-grade record. Extremes are taken over the *local*
        calendar day (the boundary Weather Underground / markets use), so the
        station's timezone is required; an unknown zone falls back to UTC.

        Returns {"daily": {date -> (high_c, low_c)}, "grain": "C"|"F",
        "grain_evidence": {"C": frac_integral, "F": frac_integral}}. The native
        reporting grain is *detected*, not assumed: whichever unit the sensor
        emits as whole integers is its native one (US ASOS → °F, most of the
        world → °C). Daily extremes are reconstructed from hourly + special
        reports, so a peak between routine obs that fell in a SPECI is captured;
        a sub-minute spike that produced no report is not (a known limitation,
        the same one the public record has)."""
        # day2 is EXCLUSIVE on the IEM archive — compensate here so the caller's
        # end date is inclusive (same convention as _fetch_metar_raw).
        tz = timezone if "/" in (timezone or "") else "Etc/UTC"
        iem_end = end + dt.timedelta(days=1)
        txt = self.http.get_text(METAR_URL, {
            "station": icao,
            "data": ["tmpf", "tmpc"],
            "year1": start.year, "month1": start.month, "day1": start.day,
            "year2": iem_end.year, "month2": iem_end.month, "day2": iem_end.day,
            "tz": tz, "format": "onlycomma", "latlon": "no",
            "missing": "empty", "trace": "empty",
            "report_type": [3, 4],
        })
        # tmpf is Fahrenheit; tmpc is Celsius. Parse each through its own
        # plausibility band so a hot US day (tmpf > 60 as a raw number) is
        # not dropped and can vote for the whole-°F native grain.
        def _parse_f(cell: str) -> float | None:
            try:
                return _wu_temp_f(float(cell.strip()))
            except (ValueError, TypeError):
                return None
        by_day: dict[str, list[tuple[float, float]]] = {}
        n_f = n_c = total = 0
        for line in txt.splitlines()[1:]:           # skip header
            p = line.split(",")
            if len(p) < 4:
                continue
            day = p[1][:10]
            if len(day) != 10 or day[4] != "-" or day[7] != "-":
                continue
            f = _parse_f(p[2])
            c = _clean_temp_cell(p[3])
            if c is None and f is None:
                continue
            # Prefer the Celsius column for daily extremes; fall back to a
            # Fahrenheit-derived value only when tmpc is missing.
            if c is not None:
                by_day.setdefault(day, []).append(c)
            elif f is not None:
                by_day.setdefault(day, []).append((f - 32.0) * 5.0 / 9.0)
            total += 1
            if c is not None and abs(c - round(c)) < 0.05:
                n_c += 1
            if f is not None and abs(f - round(f)) < 0.05:
                n_f += 1
        daily: DailySeries = {}
        for day, cs in by_day.items():
            if len(cs) < WU_MIN_DAY_OBS:   # too few obs to trust a daily extreme
                continue
            daily[day] = (max(cs), min(cs))
        frac_c = n_c / total if total else 0.0
        frac_f = n_f / total if total else 0.0
        # Native grain = whichever unit the sensor emits as whole integers MORE OFTEN,
        # with a floor so weak evidence defaults to the international °C standard. US ASOS
        # reports a mix of whole-°F and 0.1-°C, so frac_f sits ~0.5-0.8 and never reaches the
        # old 0.9 bar — KSFO was misread as °C, garbling its whole-°F settlement. °C stations
        # are ~1.0 integral-in-C (verified EGLC/WSSS/RPLL 1.00 vs frac_f ≤ 0.09), so
        # frac_f > frac_c never fires for them: this flips ONLY the genuine °F stations.
        grain = "F" if (frac_f > frac_c and frac_f >= 0.4) else "C"
        return {"daily": daily, "grain": grain,
                "grain_evidence": {"C": round(frac_c, 3), "F": round(frac_f, 3)}}

    def nws_cli_daily(self, icao: str, start: dt.date,
                      end: dt.date) -> dict[str, dict]:
        """Daily NWS CLI (climatological report) highs from the IEM parsed-CLI
        archive — the record Kalshi's US high-temperature contracts settle on
        (kalshi_sf_seam.md: "Kalshi truth = the FINAL NWS CLI, never WU"). The
        CLI maximum ingests the 6-hourly METAR max groups, so it reads at or
        above the hourly-table max (the between-obs spike channel).

        Returns {date -> {"high_f": float|None, "high_time": str|None}} for
        start..end inclusive. A CLI "high" can be the non-numeric sentinel "M"
        (missing) — surfaced as None, never as a number (the kalshi_logger bug:
        "M" passed an is-not-None check and TypeError'd downstream). Only days
        the CLI has been ISSUED for appear; the current day is absent until the
        report publishes (~early next morning local)."""
        out: dict[str, dict] = {}
        for year in range(start.year, end.year + 1):
            data = self.http.get_json(
                "https://mesonet.agron.iastate.edu/json/cli.py",
                {"station": icao, "year": str(year)},
            )
            for row in data.get("results", []) or []:
                day = row.get("valid")
                if not isinstance(day, str) or day < start.isoformat() or day > end.isoformat():
                    continue
                high = row.get("high")
                out[day] = {
                    "high_f": float(high) if isinstance(high, (int, float))
                    and not isinstance(high, bool) else None,
                    "high_time": row.get("high_time"),
                }
        return out

    # -- Climatological records: how this date compares historically ---------
    def fetch_climatology(self, place: Place, start: dt.date,
                          end: dt.date) -> DailySeries:
        """Long-span daily max/min (date -> (high, low)) for record and normal
        context. Not counted in the operational QC tally; bad values are still
        dropped by the plausibility band."""
        data = self.http.get_json(
            ARCHIVE_URL,
            {**self._common(place),
             "start_date": start.isoformat(), "end_date": end.isoformat()},
        )
        return _pair_series(data.get("daily", {}), None, None)

    # -- Diurnal profile: when the peak/trough actually land ------------------
    def fetch_hourly_consensus(self, place: Place, target: dt.date,
                               models: list[str]) -> list[tuple[str, float]]:
        """Multi-model mean hourly 2 m temperature for the target day, in the
        location's local time. Returns [(iso_hour, mean_temp), ...]."""
        lead = (target - place_today(place)).days
        data = self.http.get_json(
            LIVE_URL,
            {"latitude": place.latitude, "longitude": place.longitude,
             "timezone": place.timezone, "hourly": "temperature_2m",
             "models": ",".join(models), "forecast_days": max(lead + 1, 1)},
        )
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        cols = [v for k, v in hourly.items()
                if isinstance(v, list)
                and (k == "temperature_2m" or k.startswith("temperature_2m_"))]
        target_s = target.isoformat()
        out: list[tuple[str, float]] = []
        for i, t in enumerate(times):
            if not isinstance(t, str) or not t.startswith(target_s):
                continue
            vals = []
            for col in cols:
                if i >= len(col):
                    continue
                raw = col[i]
                v = _clean_temp(raw)
                if raw is not None:
                    self.qc["screened"] += 1
                    if v is None:
                        self.qc["rejected"] += 1
                if v is not None:
                    vals.append(v)
            if vals:
                out.append((t, sum(vals) / len(vals)))
        return out

    def fetch_hourly_archive(self, place: Place, start: dt.date,
                             end: dt.date) -> dict[str, list[tuple[int, float]]]:
        """ERA5 observed hourly temps over the window, grouped by local date:
        date -> [(hour, temp), ...]. Used to backtest when peaks/troughs land."""
        data = self.http.get_json(
            ARCHIVE_URL,
            {"latitude": place.latitude, "longitude": place.longitude,
             "timezone": place.timezone, "hourly": "temperature_2m",
             "start_date": start.isoformat(), "end_date": end.isoformat()},
        )
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        out: dict[str, list[tuple[int, float]]] = {}
        for t, raw in zip(times, temps):
            if not isinstance(t, str) or "T" not in t:
                continue
            v = _clean_temp(raw)
            if raw is not None:
                self.qc["screened"] += 1
                if v is None:
                    self.qc["rejected"] += 1
            if v is None:
                continue
            date_s, _, hh = t.partition("T")
            if not hh[:2].isdigit():
                continue
            out.setdefault(date_s, []).append((int(hh[:2]), v))
        return out

    # -- Stage 2: Computation (true ensemble) --------------------------------
    def fetch_ensemble_members(self, model: str, place: Place,
                               target: dt.date) -> tuple[list[float], list[float]]:
        """All perturbed members' (highs, lows) for the target day."""
        lead = (target - place_today(place)).days
        data = self.http.get_json(
            ENSEMBLE_URL,
            {**self._common(place), "models": model,
             "forecast_days": max(lead + 1, 1)},
        )
        daily = data.get("daily", {})
        times = daily.get("time", [])
        try:
            idx = times.index(target.isoformat())
        except ValueError:
            return [], []
        highs, lows = [], []
        for key, col in daily.items():
            if not isinstance(col, list) or idx >= len(col):
                continue
            is_max = key.startswith("temperature_2m_max")
            is_min = key.startswith("temperature_2m_min")
            if not (is_max or is_min):
                continue
            raw = col[idx]
            v = _clean_temp(raw)
            if raw is not None:                  # quality-control accounting
                self.qc["screened"] += 1
                if v is None:
                    self.qc["rejected"] += 1
            if v is None:
                continue
            (highs if is_max else lows).append(v)
        return highs, lows
