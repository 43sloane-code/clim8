"""Verdict logging and forward verification.

Logging every verdict lets the system be audited against reality as days pass
(complementing the in-run historical backtest). `verify` revisits past
verdicts whose target day is now observable and records the actual error.
All SQL is parameterized.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

from .council import Verdict
from .sources import Place, Sources, Station

DB_PATH = Path(__file__).resolve().parent.parent / "verdicts.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS verdicts (
               issued_at    TEXT NOT NULL,
               place        TEXT NOT NULL,
               target_date  TEXT NOT NULL,
               high         REAL NOT NULL,
               low          REAL NOT NULL,
               confidence   TEXT NOT NULL,
               actual_high  REAL,
               actual_low   REAL,
               err_high     REAL,
               err_low      REAL,
               PRIMARY KEY (place, target_date, issued_at))"""
    )
    # Migrate older tables that predate later columns.
    existing = {row[1] for row in conn.execute("PRAGMA table_info(verdicts)")}
    added = {
        "actual_high": "REAL", "actual_low": "REAL",
        "err_high": "REAL", "err_low": "REAL",
        # Provenance so verification scores against the *same* truth the verdict
        # was anchored on (the station), at the exact point it was forecast for.
        "truth_kind": "TEXT", "station_id": "TEXT",
        "fc_lat": "REAL", "fc_lon": "REAL",
    }
    for col, typ in added.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE verdicts ADD COLUMN {col} {typ}")
    conn.commit()
    return conn


def log_verdict(v: Verdict) -> None:
    ts = v.truth_source or {}
    station = ts.get("station") or {}
    conn = _connect()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO verdicts "
            "(issued_at, place, target_date, high, low, confidence, "
            " truth_kind, station_id, fc_lat, fc_lon) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (dt.datetime.now().isoformat(timespec="seconds"),
             v.place.label(), v.target, v.high, v.low, v.confidence,
             ts.get("kind"), station.get("id") or None,
             v.place.latitude, v.place.longitude),
        )
    conn.close()


def verify(sources: Sources | None = None) -> list[str]:
    """Score logged verdicts against the *same* truth they were anchored on.

    Station-anchored verdicts are scored against that station's own daily
    observations (the point a record settles on) — these lag real time by a few
    weeks on the free bulk feed, so a recent target simply stays unscored until
    the station data catches up. ERA5-grid verdicts are scored against ERA5 at
    the exact coordinates they were forecast for. Either way the comparison is
    truth-matched, never a city-centroid stand-in."""
    sources = sources or Sources()
    cutoff = (dt.date.today() - dt.timedelta(days=2)).isoformat()
    conn = _connect()
    rows = conn.execute(
        "SELECT issued_at, place, target_date, high, low, "
        "       truth_kind, station_id, fc_lat, fc_lon FROM verdicts "
        "WHERE actual_high IS NULL AND target_date <= ?",
        (cutoff,),
    ).fetchall()

    station_cache: dict[str, dict] = {}
    report: list[str] = []
    for (issued_at, place_label, target, high, low,
         truth_kind, station_id, fc_lat, fc_lon) in rows:
        actual, truth_note = None, ""
        if truth_kind == "station" and station_id:
            series = station_cache.get(station_id)
            if series is None:
                try:
                    series = sources.fetch_station_daily(
                        Station(id=station_id, name="", wmo=None, icao=None,
                                latitude=fc_lat or 0.0, longitude=fc_lon or 0.0,
                                elevation=None, distance_km=0.0))
                except Exception:
                    series = {}
                station_cache[station_id] = series
            actual = series.get(target)
            truth_note = " vs station"
        else:
            place = _coord_place(place_label, fc_lat, fc_lon, sources)
            if place is None:
                continue
            day = dt.date.fromisoformat(target)
            actual = sources.fetch_archive_series(place, day, day).get(target)
            truth_note = " vs ERA5"
        if not actual:
            continue                          # truth not yet available — retry later
        a_high, a_low = actual
        e_high, e_low = abs(high - a_high), abs(low - a_low)
        with conn:
            conn.execute(
                "UPDATE verdicts SET actual_high=?, actual_low=?, err_high=?, err_low=? "
                "WHERE issued_at=? AND place=? AND target_date=?",
                (a_high, a_low, e_high, e_low, issued_at, place_label, target),
            )
        report.append(
            f"{place_label} {target}: predicted {high:.1f}/{low:.1f}, "
            f"actual {a_high:.1f}/{a_low:.1f}  (err {e_high:.1f}/{e_low:.1f}){truth_note}"
        )
    conn.close()
    return report


def _coord_place(label: str, fc_lat, fc_lon, sources: Sources):
    """Prefer the exact forecast coordinates; fall back to re-geocoding the
    stored city label for verdicts logged before coordinates were recorded."""
    city = label.split(",")[0].strip()
    if isinstance(fc_lat, (int, float)) and isinstance(fc_lon, (int, float)):
        return Place(name=city, country="", latitude=float(fc_lat),
                     longitude=float(fc_lon), timezone="auto")
    return _reverse_place(sources, label)


def _reverse_place(sources: Sources, label: str):
    """Re-geocode from the stored 'City, Country' label."""
    city = label.split(",")[0].strip()
    try:
        return sources.geocode(city)
    except Exception:
        return None
