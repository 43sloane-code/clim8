#!/usr/bin/env python3
"""Multi-source weather verdict agent.

Derives daily high/low temperature forecasts for a city from several
independent weather models, backtests each model against observed (ERA5)
temperatures to learn how much to trust it, then blends them into a single
skill-weighted verdict with a confidence read from model agreement.

Data source: Open-Meteo (free, no API key).
  - Geocoding:           https://geocoding-api.open-meteo.com
  - Live forecast:       https://api.open-meteo.com
  - Historical forecast: https://historical-forecast-api.open-meteo.com  (what each model predicted)
  - Observed archive:    https://archive-api.open-meteo.com               (ERA5 reanalysis = "truth")

Storage: SQLite (verdicts.db) logs every verdict so accuracy can also be
audited going forward, not just via the historical replay.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import ssl
import statistics
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# Build an SSL context with a working CA bundle. python.org builds on macOS
# often ship without a usable system bundle, so prefer certifi when present.
try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:  # certifi not installed -> fall back to system defaults
    _SSL_CTX = ssl.create_default_context()

# Independent forecast models exposed by Open-Meteo. Each is run by a
# different meteorological agency, so blending them is a genuine ensemble.
MODELS = [
    "ecmwf_ifs025",      # ECMWF (Europe)
    "gfs_seamless",      # NOAA GFS (USA)
    "icon_seamless",     # DWD ICON (Germany)
    "gem_seamless",      # Environment Canada
    "meteofrance_seamless",  # Meteo-France
    "jma_seamless",      # Japan Meteorological Agency
]

DB_PATH = Path(__file__).with_name("verdicts.db")
HTTP_TIMEOUT = 30


# --------------------------------------------------------------------------- #
# HTTP helper
# --------------------------------------------------------------------------- #
def _get_json(base: str, params: dict) -> dict:
    url = base + "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"User-Agent": "weather-verdict/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # surface a clean message instead of a stack trace
        raise SystemExit(f"network error calling {base}: {exc}") from exc


# --------------------------------------------------------------------------- #
# Geocoding
# --------------------------------------------------------------------------- #
@dataclass
class Place:
    name: str
    country: str
    latitude: float
    longitude: float
    timezone: str

    def label(self) -> str:
        return f"{self.name}, {self.country}"


def geocode(city: str) -> Place:
    data = _get_json(
        "https://geocoding-api.open-meteo.com/v1/search",
        {"name": city, "count": 1, "language": "en", "format": "json"},
    )
    results = data.get("results")
    if not results:
        raise SystemExit(f"could not find a city named {city!r}")
    r = results[0]
    return Place(
        name=r["name"],
        country=r.get("country", "?"),
        latitude=r["latitude"],
        longitude=r["longitude"],
        timezone=r.get("timezone", "auto"),
    )


# --------------------------------------------------------------------------- #
# Backtesting: per-model mean absolute error vs observed temperatures
# --------------------------------------------------------------------------- #
@dataclass
class ModelSkill:
    model: str
    mae_max: float | None = None  # mean abs error on daily high (degC)
    mae_min: float | None = None  # mean abs error on daily low (degC)
    samples: int = 0


def _daily_series(payload: dict, key: str) -> dict[str, float]:
    """Map date -> value for one daily variable, skipping nulls."""
    daily = payload.get("daily", {})
    times = daily.get("time", [])
    vals = daily.get(key, [])
    return {t: v for t, v in zip(times, vals) if v is not None}


def backtest(place: Place, days: int) -> dict[str, ModelSkill]:
    """Compare each model's past forecasts to ERA5 observed highs/lows."""
    end = dt.date.today() - dt.timedelta(days=2)   # archive lags ~1-2 days
    start = end - dt.timedelta(days=days)
    common = {
        "latitude": place.latitude,
        "longitude": place.longitude,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "temperature_2m_max,temperature_2m_min",
        "timezone": place.timezone,
    }

    # Observed "truth" from ERA5 reanalysis.
    obs = _get_json("https://archive-api.open-meteo.com/v1/archive", common)
    obs_max = _daily_series(obs, "temperature_2m_max")
    obs_min = _daily_series(obs, "temperature_2m_min")

    skills: dict[str, ModelSkill] = {}
    for model in MODELS:
        hist = _get_json(
            "https://historical-forecast-api.open-meteo.com/v1/forecast",
            {**common, "models": model},
        )
        f_max = _daily_series(hist, "temperature_2m_max")
        f_min = _daily_series(hist, "temperature_2m_min")

        err_max = [abs(f_max[d] - obs_max[d]) for d in f_max if d in obs_max]
        err_min = [abs(f_min[d] - obs_min[d]) for d in f_min if d in obs_min]

        skills[model] = ModelSkill(
            model=model,
            mae_max=statistics.mean(err_max) if err_max else None,
            mae_min=statistics.mean(err_min) if err_min else None,
            samples=min(len(err_max), len(err_min)),
        )
    return skills


def _weights(skills: dict[str, ModelSkill], which: str) -> dict[str, float]:
    """Inverse-error weights, normalized. Models with no skill data fall back
    to the median MAE so they still contribute but aren't over-trusted."""
    attr = "mae_max" if which == "max" else "mae_min"
    known = [getattr(s, attr) for s in skills.values() if getattr(s, attr) is not None]
    fallback = statistics.median(known) if known else 1.0
    raw: dict[str, float] = {}
    for model, s in skills.items():
        mae = getattr(s, attr)
        mae = fallback if mae is None else mae
        raw[model] = 1.0 / max(mae, 0.1)  # floor avoids divide-by-zero blowups
    total = sum(raw.values()) or 1.0
    return {m: w / total for m, w in raw.items()}


# --------------------------------------------------------------------------- #
# Live forecast + verdict
# --------------------------------------------------------------------------- #
@dataclass
class Verdict:
    place: Place
    date: str
    high: float
    low: float
    high_spread: float
    low_spread: float
    confidence: str
    per_model: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)


def _confidence(spread: float) -> str:
    if spread <= 1.5:
        return "high"
    if spread <= 3.0:
        return "medium"
    return "low"


def forecast(place: Place, skills: dict[str, ModelSkill], lead: int) -> Verdict:
    target = dt.date.today() + dt.timedelta(days=lead)
    data = _get_json(
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": place.latitude,
            "longitude": place.longitude,
            "daily": "temperature_2m_max,temperature_2m_min",
            "models": MODELS,
            "timezone": place.timezone,
            "forecast_days": max(lead + 1, 1),
        },
    )
    daily = data.get("daily", {})
    times = daily.get("time", [])
    try:
        idx = times.index(target.isoformat())
    except ValueError:
        raise SystemExit(f"forecast does not cover {target.isoformat()}")

    per_model: dict[str, tuple[float | None, float | None]] = {}
    highs: dict[str, float] = {}
    lows: dict[str, float] = {}
    for model in MODELS:
        hv = daily.get(f"temperature_2m_max_{model}", [])
        lv = daily.get(f"temperature_2m_min_{model}", [])
        h = hv[idx] if idx < len(hv) else None
        l = lv[idx] if idx < len(lv) else None
        per_model[model] = (h, l)
        if h is not None:
            highs[model] = h
        if l is not None:
            lows[model] = l

    if not highs or not lows:
        raise SystemExit("no model returned data for the target day")

    w_max = _weights(skills, "max")
    w_min = _weights(skills, "min")

    def blended(vals: dict[str, float], weights: dict[str, float]) -> float:
        wsum = sum(weights[m] for m in vals)
        return sum(vals[m] * weights[m] for m in vals) / (wsum or 1.0)

    high = blended(highs, w_max)
    low = blended(lows, w_min)
    high_spread = max(highs.values()) - min(highs.values())
    low_spread = max(lows.values()) - min(lows.values())

    return Verdict(
        place=place,
        date=target.isoformat(),
        high=high,
        low=low,
        high_spread=high_spread,
        low_spread=low_spread,
        confidence=_confidence(max(high_spread, low_spread)),
        per_model=per_model,
    )


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def log_verdict(v: Verdict) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS verdicts (
               issued_at TEXT, place TEXT, target_date TEXT,
               high REAL, low REAL, confidence TEXT,
               UNIQUE(place, target_date, issued_at))"""
    )
    conn.execute(
        "INSERT OR REPLACE INTO verdicts VALUES (?,?,?,?,?,?)",
        (
            dt.datetime.now().isoformat(timespec="seconds"),
            v.place.label(),
            v.date,
            round(v.high, 1),
            round(v.low, 1),
            v.confidence,
        ),
    )
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------- #
# Presentation
# --------------------------------------------------------------------------- #
def _fmt(x: float | None) -> str:
    return f"{x:5.1f}" if x is not None else "   - "


def render(v: Verdict, skills: dict[str, ModelSkill]) -> str:
    lines = []
    lines.append(f"Weather verdict for {v.place.label()}  ({v.date})")
    lines.append("=" * 52)
    lines.append(f"  HIGH : {v.high:5.1f} degC   (model spread {v.high_spread:.1f})")
    lines.append(f"  LOW  : {v.low:5.1f} degC   (model spread {v.low_spread:.1f})")
    lines.append(f"  Confidence: {v.confidence.upper()}")
    lines.append("")
    lines.append("  Per-model forecast vs backtested error (MAE, lower=better):")
    lines.append(f"    {'model':22} {'high':>6} {'low':>6}  {'MAEhi':>6} {'MAElo':>6}  n")
    for model in MODELS:
        h, l = v.per_model.get(model, (None, None))
        s = skills.get(model, ModelSkill(model))
        mae_hi = f"{s.mae_max:.2f}" if s.mae_max is not None else "  -"
        mae_lo = f"{s.mae_min:.2f}" if s.mae_min is not None else "  -"
        lines.append(
            f"    {model:22} {_fmt(h)} {_fmt(l)}  {mae_hi:>6} {mae_lo:>6}  {s.samples}"
        )
    lines.append("")
    lines.append("  Verdict is a skill-weighted blend: models with lower backtested")
    lines.append("  error count more. Confidence reflects how tightly models agree.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Multi-source backtested weather verdict.")
    ap.add_argument("city", help="city name, e.g. 'Tokyo' or 'Paris'")
    ap.add_argument("--lead", type=int, default=1,
                    help="days ahead to forecast (0=today, 1=tomorrow). default 1")
    ap.add_argument("--backtest-days", type=int, default=45,
                    help="history window for scoring each model. default 45")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a report")
    args = ap.parse_args(argv)

    place = geocode(args.city)
    skills = backtest(place, args.backtest_days)
    verdict = forecast(place, skills, args.lead)
    log_verdict(verdict)

    if args.json:
        print(json.dumps({
            "place": place.label(),
            "date": verdict.date,
            "high": round(verdict.high, 1),
            "low": round(verdict.low, 1),
            "confidence": verdict.confidence,
            "high_spread": round(verdict.high_spread, 1),
            "low_spread": round(verdict.low_spread, 1),
            "models": {
                m: {
                    "high": verdict.per_model[m][0],
                    "low": verdict.per_model[m][1],
                    "mae_high": skills[m].mae_max,
                    "mae_low": skills[m].mae_min,
                    "samples": skills[m].samples,
                } for m in MODELS
            },
        }, indent=2))
    else:
        print(render(verdict, skills))
    return 0


if __name__ == "__main__":
    sys.exit(main())
