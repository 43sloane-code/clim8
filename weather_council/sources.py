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

import datetime as dt
import json
import math
from dataclasses import dataclass

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

# Plausibility band: any temperature outside this is treated as corrupt and
# dropped, so a bad upstream value can never enter a verdict.
TEMP_MIN_C = -90.0
TEMP_MAX_C = 60.0

DailySeries = dict[str, tuple[float, float]]  # date -> (high, low)


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
        lead = (target - dt.date.today()).days
        data = self.http.get_json(
            LIVE_URL,
            {**self._common(place), "models": model, "forecast_days": max(lead + 1, 1)},
        )
        series = _pair_series(data.get("daily", {}), model, self.qc)
        return series.get(target.isoformat())

    def fetch_history_series(self, model: str, place: Place,
                             start: dt.date, end: dt.date) -> DailySeries:
        data = self.http.get_json(
            HISTORY_URL,
            {**self._common(place),
             "models": model,
             "start_date": start.isoformat(),
             "end_date": end.isoformat()},
        )
        return _pair_series(data.get("daily", {}), model, self.qc)

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
        return out

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
        lead = (target - dt.date.today()).days
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
        lead = (target - dt.date.today()).days
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
