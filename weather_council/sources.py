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

from .security import SafeHTTPClient, SecurityError, validate_city

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


def _history_cache_key(url: str, params: dict) -> str:
    blob = url + "?" + json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _history_cache_read(key: str):
    """Return (data, age) for a cached history response, or (None, None)."""
    f = HISTORY_CACHE_DIR / f"{key}.json"
    try:
        raw = f.read_text(encoding="utf-8")
        age = dt.datetime.now() - dt.datetime.fromtimestamp(f.stat().st_mtime)
    except OSError:
        return None, None
    try:
        return json.loads(raw), age
    except ValueError:
        return None, None


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
    if not isinstance(value, (int, float)):
        return None
    v = float(value)
    if v != v or not (TEMP_MIN_C <= v <= TEMP_MAX_C):  # NaN or out of band
        return None
    return v


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

    def fetch_station_daily(self, station: Station) -> DailySeries:
        """Full daily (high, low) history for one station from its bulk CSV.
        CSV columns: date,tavg,tmin,tmax,prcp,snow,wdir,wspd,wpgt,pres,tsun.
        Values are plausibility-screened (bad ones dropped) but — like the bulk
        climatology and neighbour fetches — are *not* added to the operational QC
        tally: the file spans decades and several candidate stations may be
        probed, which would swamp the per-run anomaly count."""
        txt = self.http.get_gzip_text(STATION_DAILY_URL.format(id=station.id))
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
        elif self.is_london_eglc(station):
            modern = self.london_eglc_truth_series(dt.date.today())
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

    def fetch_hko_daily_max(self, years: list[int]) -> dict[str, float]:
        """Recent daily maximum temperature (date -> high_c) at the Hong Kong
        Observatory HQ, from the HKO open-data API. The Meteostat file for the HKO
        station stops in 1992, so it cannot supply a *modern* record; this does."""
        return self._fetch_hko_dataset("CLMMAXT", years)

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

    def hko_truth_series(self, target: dt.date, back_years: int = 4) -> DailySeries:
        """Modern daily (high, low) record at the Hong Kong Observatory HQ from the
        HKO open-data API — the settlement-grade truth the council anchors a Hong
        Kong verdict on. Daily high from CLMMAXT, low from CLMMINT; only dates with
        BOTH a complete reading are returned. Refreshed monthly (lags real time by
        ~weeks — fresher than the airport's Meteostat bulk file)."""
        years = list(range(target.year - back_years, target.year + 1))
        highs = self._fetch_hko_dataset("CLMMAXT", years)
        lows = self._fetch_hko_dataset("CLMMINT", years)
        return {d: (highs[d], lows[d]) for d in highs.keys() & lows.keys()}

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

    def is_london_eglc(self, station: Station) -> bool:
        """True iff this station is London City Airport (EGLC) — the airport the
        London temperature market settles on. Recognised by ICAO alone (the
        Meteostat 'EGLC0' Abbey Wood file carries this code), never a hardcoded
        city/station table. This is the gate for overlaying the modern IEM ASOS
        METAR record in place of that stale, distant bulk file."""
        return (station.icao or "").upper() == "EGLC"

    def london_eglc_truth_series(self, target: dt.date,
                                 back_years: int = 2) -> DailySeries:
        """Modern daily (high, low) at London City Airport (EGLC) reconstructed
        from raw IEM ASOS METAR over the local calendar day — the same
        settlement-grade airport sensor the London market resolves on, and the
        identical record run.py uses for its settlement reference. Spans the last
        `back_years` calendar years up to `target`; days with too few obs are
        dropped by fetch_metar_daily and simply fall back to the Meteostat value.
        One cached request. Returns {} on any failure so truth resolution never
        aborts — the council then keeps the Meteostat base."""
        start = dt.date(target.year - back_years, 1, 1)
        try:
            res = self.fetch_metar_daily("EGLC", start, target, "Europe/London")
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
        unobserved, never interpolated into fabricated readings."""
        tz = timezone if "/" in (timezone or "") else "Etc/UTC"
        txt = self.http.get_text(METAR_URL, {
            "station": icao,
            "data": ["tmpc"],
            "year1": start.year, "month1": start.month, "day1": start.day,
            "year2": end.year, "month2": end.month, "day2": end.day,
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

    def eglc_current(self) -> dict | None:
        """Live current air temperature at London City Airport (EGLC) — the most
        recent raw IEM ASOS METAR, i.e. the settlement sensor's own latest
        reading, so a London verdict's 'now' is the airport gauge the market
        resolves on rather than an Open-Meteo grid-cell proxy. Returns
        {temperature_2m, record_time} or None on any failure (caller then keeps
        the grid 'current'). METAR air temperature is whole-degree °C and the
        routine cadence is ~30 min (plus SPECIs), so this updates each time EGLC
        reports. The window end is a day ahead because the IEM archive treats the
        end date as exclusive — without it the feed cuts off before today."""
        today = dt.date.today()
        try:
            obs = self.fetch_metar_observations(
                "EGLC", today - dt.timedelta(days=1),
                today + dt.timedelta(days=1), "Europe/London")
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
        tz = timezone if "/" in (timezone or "") else "Etc/UTC"
        txt = self.http.get_text(METAR_URL, {
            "station": icao,
            "data": ["tmpf", "tmpc"],
            "year1": start.year, "month1": start.month, "day1": start.day,
            "year2": end.year, "month2": end.month, "day2": end.day,
            "tz": tz, "format": "onlycomma", "latlon": "no",
            "missing": "empty", "trace": "empty",
            "report_type": [3, 4],
        })
        by_day: dict[str, list[tuple[float, float]]] = {}
        n_f = n_c = total = 0
        for line in txt.splitlines()[1:]:           # skip header
            p = line.split(",")
            if len(p) < 4:
                continue
            day = p[1][:10]
            if len(day) != 10 or day[4] != "-" or day[7] != "-":
                continue
            f, c = _clean_temp_cell(p[2]), _clean_temp_cell(p[3])
            if c is None:
                continue
            by_day.setdefault(day, []).append((c, f if f is not None else c))
            total += 1
            if abs(c - round(c)) < 0.05:
                n_c += 1
            if f is not None and abs(f - round(f)) < 0.05:
                n_f += 1
        daily: DailySeries = {}
        for day, obs in by_day.items():
            if len(obs) < 12:          # too few obs to trust a daily extreme
                continue
            cs = [c for c, _ in obs]
            daily[day] = (max(cs), min(cs))
        frac_c = n_c / total if total else 0.0
        frac_f = n_f / total if total else 0.0
        grain = "F" if (frac_f >= 0.9 and frac_f > frac_c) else "C"
        return {"daily": daily, "grain": grain,
                "grain_evidence": {"C": round(frac_c, 3), "F": round(frac_f, 3)}}

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
