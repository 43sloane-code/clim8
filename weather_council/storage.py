"""Verdict logging and forward verification.

Logging every verdict lets the system be audited against reality as days pass
(complementing the in-run historical backtest). `verify` revisits past
verdicts whose target day is now observable and records the actual error.
All SQL is parameterized.

A second, parallel ledger (`market_snapshots`) persists each council-vs-market
comparison so the C7 realized-outcome scorer (`edge.py`) can grade both
forecasters once the day settles. Settlement resolves the realized high against
the verdict's OWN anchor station — the record the market pays out on — exactly
as `verify` does, so the edge is scored where the verdict is anchored, not at a
face-value grid reading.
"""

from __future__ import annotations

__all__ = [
    'log_verdict', 'verify', 'log_market_snapshot', 'settle_market_snapshots',
    'fetch_settled_snapshots', 'log_tracked_forecast', 'settle_tracked_forecasts',
    'tracked_forecast_scores', 'backfill_pm_resolutions', 'live_bucket_scorecard'
]

import datetime as dt
import json
import sqlite3
from pathlib import Path

from .council import Verdict
from .market import (MarketData, _bucket_edges, _native_reading_int,
                     resolved_event_slug)
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
        # station_icao/station_name persist the anchor's IDENTITY (not just its id)
        # so verify() can rebuild the exact Station the verdict used — the modern
        # settlement overlays in fetch_station_daily gate on these (EGLC by icao,
        # the HKO Observatory by name + geography). Without them verify saw only the
        # stale bulk Meteostat file (HKO ends 1992 -> never settles; EGLC -> the
        # Abbey Wood gauge weeks away), so every station-anchored basket-city verdict
        # stayed unsettled. Same latent bug, same fix, as market_snapshots and
        # tracked_forecasts below. Additive/nullable: never alters an existing score.
        "station_icao": "TEXT", "station_name": "TEXT",
    }
    for col, typ in added.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE verdicts ADD COLUMN {col} {typ}")
    # C7 ledger: one row per logged council-vs-market comparison. `buckets_json`
    # carries the full ladder (label, lo, hi, model_prob, market_prob); the
    # realized_* columns stay NULL until the day settles against the anchor
    # station. Truth provenance mirrors the verdicts table so settlement uses the
    # same anchored record the verdict was scored on.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS market_snapshots (
               issued_at      TEXT NOT NULL,
               place          TEXT NOT NULL,
               target_date    TEXT NOT NULL,
               market_title   TEXT,
               grain          TEXT NOT NULL,
               buckets_json   TEXT NOT NULL,
               truth_kind     TEXT,
               station_id     TEXT,
               station_icao   TEXT,
               station_name   TEXT,
               fc_lat         REAL,
               fc_lon         REAL,
               realized_high  REAL,
               realized_label TEXT,
               settled_at     TEXT,
               PRIMARY KEY (place, target_date, issued_at))"""
    )
    # Migrate market_snapshots tables that predate the read-only market-depth
    # columns. event_volume/event_liquidity record HOW REAL the market a snapshot
    # was scored against was (thin/one-sided books vs deep two-sided ones), so C7
    # can later condition on quote quality. Additive and nullable: never changes a
    # model probability or an existing score.
    # station_icao/station_name persist the anchor's IDENTITY (not just its id), so
    # settlement can reconstruct the exact Station the verdict used — the modern
    # truth overlays gate on these fields (EGLC by icao, the HKO Observatory by a
    # name token + geography). Without them settlement saw only the stale bulk
    # Meteostat file and never settled. Additive/nullable: never alters a score.
    # sub_degree persists whether the market settles FINER than its whole-degree
    # bucket labels (HK Observatory, 0.1°C). Its settlement rule is range-
    # containment (floor: 28.6°C -> the 28°C bucket), NOT round-half-up, so the
    # realized-bucket map must know it at settle time. Additive/nullable; legacy
    # rows are backfilled below from the anchor identity (only the HKO Observatory
    # settles sub-degree among the basket cities).
    # pm_resolved_label / pm_resolved_at persist the contract's OWN settled bucket
    # (read authoritatively from the Gamma event by `backfill_pm_resolutions`),
    # SEPARATE from realized_label (which is our anchor-station PROXY). Keeping both
    # lets the audit tool (a) score served buckets against the TRUE payout, and
    # (b) ALARM when proxy and contract diverge — the alignment gap that no amount
    # of internal CRPS can catch. Additive/nullable: never alters a model prob or
    # an existing score; the served distribution is untouched.
    ms_existing = {row[1] for row in conn.execute("PRAGMA table_info(market_snapshots)")}
    ms_added = {"market_volume": "REAL", "market_liquidity": "REAL",
                "station_icao": "TEXT", "station_name": "TEXT",
                "sub_degree": "INTEGER",
                "pm_resolved_label": "TEXT", "pm_resolved_at": "TEXT"}
    for col, typ in ms_added.items():
        if col not in ms_existing:
            conn.execute(f"ALTER TABLE market_snapshots ADD COLUMN {col} {typ}")
    if "sub_degree" not in ms_existing:
        # One-time backfill for rows logged before this column existed: the HK
        # Observatory (id 45005 / name carrying "Observatory") is the only basket
        # anchor that settles at 0.1°C; everything else settles whole-degree.
        conn.execute(
            "UPDATE market_snapshots SET sub_degree = "
            "CASE WHEN station_id = '45005' OR station_name LIKE '%Observatory%' "
            "THEN 1 ELSE 0 END")
    # Tracked-forecaster ledger: one row per (source, place, target_date) logging
    # a NON-council forecaster's predicted high/low ALONGSIDE the council's own
    # forecast for the identical day, so the two can be graded head-to-head once
    # the day settles against the SAME anchored truth. This is how a forecaster
    # with no backtestable forecast archive (e.g. Weatherbit) earns a measured
    # track record PROSPECTIVELY before it is ever considered for the live blend.
    # Recommend-only: nothing here ever feeds a vote, a verdict, or a trade.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tracked_forecasts (
               source        TEXT NOT NULL,
               issued_at     TEXT NOT NULL,
               place         TEXT NOT NULL,
               target_date   TEXT NOT NULL,
               fc_high       REAL NOT NULL,
               fc_low        REAL NOT NULL,
               council_high  REAL,
               council_low   REAL,
               truth_kind    TEXT,
               station_id    TEXT,
               station_icao  TEXT,
               station_name  TEXT,
               fc_lat        REAL,
               fc_lon        REAL,
               actual_high   REAL,
               actual_low    REAL,
               settled_at    TEXT,
               PRIMARY KEY (source, place, target_date))"""
    )
    # Migrate tracked_forecasts tables that predate the anchor-identity columns.
    # station_icao/station_name persist the anchor's IDENTITY (not just its id) so
    # settlement can rebuild the exact Station the verdict used — the modern truth
    # overlays gate on these fields (EGLC by icao, the HKO Observatory by name +
    # geography). Without them settlement saw only the stale bulk Meteostat file
    # and never graded recent days. Additive/nullable: never alters a score. Same
    # latent bug, same fix, as market_snapshots above.
    tf_existing = {row[1] for row in conn.execute("PRAGMA table_info(tracked_forecasts)")}
    tf_added = {"station_icao": "TEXT", "station_name": "TEXT"}
    for col, typ in tf_added.items():
        if col not in tf_existing:
            conn.execute(f"ALTER TABLE tracked_forecasts ADD COLUMN {col} {typ}")
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
            " truth_kind, station_id, station_icao, station_name, fc_lat, fc_lon) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (dt.datetime.now().isoformat(timespec="seconds"),
             v.place.label(), v.target, v.high, v.low, v.confidence,
             ts.get("kind"), station.get("id") or None,
             station.get("icao") or None, station.get("name") or None,
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
        "       truth_kind, station_id, station_icao, station_name, "
        "       fc_lat, fc_lon FROM verdicts "
        "WHERE actual_high IS NULL AND target_date <= ?",
        (cutoff,),
    ).fetchall()

    station_cache: dict[str, dict] = {}
    report: list[str] = []
    for (issued_at, place_label, target, high, low,
         truth_kind, station_id, station_icao, station_name,
         fc_lat, fc_lon) in rows:
        actual, truth_note = None, ""
        if truth_kind == "station" and station_id:
            series = station_cache.get(station_id)
            if series is None:
                # Rebuild the verdict's EXACT anchor so fetch_station_daily's modern
                # settlement overlays fire (EGLC by icao, the HKO Observatory by
                # name + geography). Prefer the identity persisted at log time;
                # for rows logged before that was stored, recover it from the
                # station inventory by id. A blank id-only Station skipped the
                # overlays entirely — the bug that left every station-anchored
                # basket-city verdict unsettled.
                station = None
                if not (station_icao or station_name):
                    station = sources.station_by_id(station_id)
                if station is None:
                    station = Station(
                        id=station_id, name=station_name or "", wmo=None,
                        icao=station_icao or None,
                        latitude=fc_lat or 0.0, longitude=fc_lon or 0.0,
                        elevation=None, distance_km=0.0)
                try:
                    series = sources.fetch_station_daily(station)
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


# --------------------------------------------------------------------------- #
# C7 — market-snapshot ledger and realized-outcome settlement.                #
# --------------------------------------------------------------------------- #

def log_market_snapshot(v: Verdict, comparison) -> None:
    """Persist one council-vs-market comparison for later realized-outcome
    grading. Stores the full bucket ladder with both probability columns; the
    realized bucket is filled in later by `settle_market_snapshots` against the
    verdict's anchor station. `comparison` is a compare.VerdictMarketComparison
    (or anything exposing `.grain`, `.market_title`, and `.buckets` of objects
    with `.label/.lo/.hi/.model_prob/.market_prob`)."""
    ts = v.truth_source or {}
    station = ts.get("station") or {}
    # Persist the read-only market microstructure alongside the two probability
    # columns. getattr-with-default keeps this tolerant of any object exposing
    # only .label/.lo/.hi/.model_prob/.market_prob (the documented duck type), so
    # older callers/tests keep working while richer comparisons record depth.
    buckets = [
        {"label": b.label, "lo": b.lo, "hi": b.hi,
         "model_prob": b.model_prob, "market_prob": b.market_prob,
         "market_yes": getattr(b, "market_yes", None),
         "liquidity": getattr(b, "market_liquidity", None),
         "volume": getattr(b, "market_volume", None),
         "best_bid": getattr(b, "best_bid", None),
         "best_ask": getattr(b, "best_ask", None),
         "last_trade": getattr(b, "last_trade", None),
         "two_sided": getattr(b, "two_sided", None)}
        for b in comparison.buckets
    ]
    conn = _connect()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO market_snapshots "
            "(issued_at, place, target_date, market_title, grain, buckets_json, "
            " truth_kind, station_id, station_icao, station_name, fc_lat, fc_lon, "
            " market_volume, market_liquidity, sub_degree) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (dt.datetime.now().isoformat(timespec="seconds"),
             v.place.label(), v.target, comparison.market_title, comparison.grain,
             json.dumps(buckets), ts.get("kind"), station.get("id") or None,
             station.get("icao") or None, station.get("name") or None,
             v.place.latitude, v.place.longitude,
             getattr(comparison, "market_volume", None),
             getattr(comparison, "market_liquidity", None),
             int(bool(getattr(comparison, "settles_sub_degree", False)))),
        )
    conn.close()


def _bucket_for_reading(buckets: list[dict], reading_int: int) -> str | None:
    """Which stored bucket a whole-degree native reading lands in. Mirrors
    market.MarketBucket.contains over the persisted lo/hi edges (None = open
    tail). Returns the bucket label, or None if the reading hits no bucket."""
    for b in buckets:
        lo, hi = b.get("lo"), b.get("hi")
        if lo is None and hi is None:
            continue                       # unparseable label never matches
        if lo is not None and reading_int < lo:
            continue
        if hi is not None and reading_int > hi:
            continue
        return b.get("label")
    return None


def settle_market_snapshots(sources: Sources | None = None) -> list[str]:
    """Resolve each unsettled snapshot's realized bucket against the SAME anchor
    record the verdict was scored on, then mark it settled.

    The realized high is read from the verdict's own anchor station (the record
    the market pays out on), exactly as `verify` does — never a city-centroid or
    face-value grid reading. The high is then snapped to the market's native
    whole-degree reading and mapped into the stored bucket ladder, giving the
    realized bucket `edge.score_snapshot` grades both forecasters against.

    Each `fetch_station_daily` pulls the weeks-stale Meteostat bulk file PLUS a
    modern recent-day overlay (HKO open-data for the Observatory, IEM ASOS METAR
    for EGLC), and the overlay alone costs many requests against the shared per-run
    request budget. Reusing ONE `Sources` across every station let a heavy early
    fetch exhaust that budget, after which a LATER station's overlay silently
    failed and the series fell back to the bulk file — the target day was then
    absent, `series.get(target)` was None, and that station's recent rows never
    settled (leaving `realized_label` NULL). So on the production path (no injected
    `Sources`) we build a FRESH `Sources` per unique station, giving each its own
    request budget; an injected `Sources` (tests / explicit callers) is used as-is."""
    injected = sources is not None
    sources = sources or Sources()
    cutoff = (dt.date.today() - dt.timedelta(days=2)).isoformat()
    conn = _connect()
    rows = conn.execute(
        "SELECT issued_at, place, target_date, grain, buckets_json, "
        "       truth_kind, station_id, station_icao, station_name, "
        "       fc_lat, fc_lon, sub_degree FROM market_snapshots "
        "WHERE realized_label IS NULL AND target_date <= ?",
        (cutoff,),
    ).fetchall()

    station_cache: dict[str, dict] = {}
    report: list[str] = []
    for (issued_at, place_label, target, grain, buckets_json,
         truth_kind, station_id, station_icao, station_name,
         fc_lat, fc_lon, sub_degree) in rows:
        actual = None
        if truth_kind == "station" and station_id:
            series = station_cache.get(station_id)
            if series is None:
                # Fresh Sources per unique station on the production path so each
                # station fetch gets its own request budget (see docstring); an
                # injected Sources is honored as-is for tests/explicit callers.
                fetcher = sources if injected else Sources()
                try:
                    # Reconstruct the verdict's EXACT anchor — carrying icao+name so
                    # fetch_station_daily's modern overlays fire (EGLC by icao, the
                    # HKO Observatory by name+geography). A blank station here was the
                    # bug that left every snapshot reading only the stale bulk file.
                    series = fetcher.fetch_station_daily(
                        Station(id=station_id, name=station_name or "", wmo=None,
                                icao=station_icao or None,
                                latitude=fc_lat or 0.0, longitude=fc_lon or 0.0,
                                elevation=None, distance_km=0.0))
                except Exception:
                    series = {}
                station_cache[station_id] = series
            actual = series.get(target)
        else:
            place = _coord_place(place_label, fc_lat, fc_lon, sources)
            if place is None:
                continue
            day = dt.date.fromisoformat(target)
            actual = sources.fetch_archive_series(place, day, day).get(target)
        if not actual:
            continue                       # anchor truth not yet available — retry later
        realized_high = actual[0]
        buckets = json.loads(buckets_json)
        # Sub-degree markets (HK Observatory, 0.1°C) settle by range-containment
        # (floor: 28.6°C -> 28°C bucket), not round-half-up. _native_reading_int
        # applies the right rule given the persisted sub_degree flag.
        reading = _native_reading_int(realized_high, grain, bool(sub_degree))
        label = _bucket_for_reading(buckets, reading)
        if label is None:
            continue                       # realized high outside the ladder — leave open
        with conn:
            conn.execute(
                "UPDATE market_snapshots SET realized_high=?, realized_label=?, "
                "settled_at=? WHERE issued_at=? AND place=? AND target_date=?",
                (realized_high, label, dt.datetime.now().isoformat(timespec="seconds"),
                 issued_at, place_label, target),
            )
        report.append(
            f"{place_label} {target}: realized high {realized_high:.1f}°C "
            f"settled in bucket \"{label}\""
        )
    conn.close()
    return report


def backfill_pm_resolutions(market_data: "MarketData | None" = None,
                            *, cutoff_days: int = 1) -> list[str]:
    """Fill `pm_resolved_label` for snapshot days that have settled, reading the
    AUTHORITATIVE outcome straight from the Gamma contract (not our proxy truth).

    Idempotent: only rows where `pm_resolved_label IS NULL` and the target day is
    at least `cutoff_days` old are considered, and one resolution is fetched per
    (place, target) — then written to every snapshot row for that day. An
    unresolved or unfound event is left NULL to retry later. Read-only against the
    market; the only writes are the additive pm_resolved_* columns. Returns one
    human line per (place, target) newly resolved."""
    md = market_data or MarketData()
    cutoff = (dt.date.today() - dt.timedelta(days=cutoff_days)).isoformat()
    conn = _connect()
    pairs = conn.execute(
        "SELECT DISTINCT place, target_date FROM market_snapshots "
        "WHERE pm_resolved_label IS NULL AND target_date <= ? "
        "ORDER BY target_date, place",
        (cutoff,),
    ).fetchall()

    report: list[str] = []
    for place_label, target in pairs:
        try:
            day = dt.date.fromisoformat(target)
        except ValueError:
            continue
        res = md.fetch_resolution(resolved_event_slug(place_label, day))
        if res is None or not res.resolved or not res.winning_label:
            continue                       # not finalized / not found — retry later
        now = dt.datetime.now().isoformat(timespec="seconds")
        with conn:
            conn.execute(
                "UPDATE market_snapshots SET pm_resolved_label=?, pm_resolved_at=? "
                "WHERE place=? AND target_date=? AND pm_resolved_label IS NULL",
                (res.winning_label, now, place_label, target),
            )
        report.append(
            f"{place_label} {target}: contract settled \"{res.winning_label}\" "
            f"(source: {res.source or 'n/a'})"
        )
    conn.close()
    return report


def live_bucket_scorecard(place_label: str, max_days: int = 60) -> dict:
    """The HONEST realized hit-rate: served bucket vs the contract's OWN settled
    bucket (`pm_resolved_label`) over recent settled days — no revisable backtest.

    The backtest scores on the Open-Meteo historical-forecast archive, which is
    revised toward truth after the fact, so it OVERSTATES live skill. This reads
    only what actually happened: the served verdict's high (latest logged for the
    day) snapped to the settlement bucket, compared to the bucket the market paid
    out. Returns {n, hits, rate, recent:[(date, served_bucket, true_bucket, hit)]}.
    No network. n=0 when no settled day has both a resolution and a served verdict."""
    conn = _connect()
    rows = conn.execute(
        "SELECT DISTINCT target_date, grain, sub_degree, pm_resolved_label "
        "FROM market_snapshots WHERE place=? AND pm_resolved_label IS NOT NULL "
        "ORDER BY target_date DESC LIMIT ?",
        (place_label, max_days),
    ).fetchall()
    hits = 0
    recent: list[tuple[str, int, int, bool]] = []
    for target, grain, sub_degree, pm_label in rows:
        lo, hi = _bucket_edges(pm_label or "")
        true_b = lo if lo is not None else hi
        if true_b is None:
            continue
        vrow = conn.execute(
            "SELECT high FROM verdicts WHERE place=? AND target_date=? "
            "AND high IS NOT NULL ORDER BY issued_at DESC LIMIT 1",
            (place_label, target)).fetchone()
        if not vrow:
            continue
        served_b = _native_reading_int(vrow[0], grain or "C", bool(sub_degree))
        hit = served_b == true_b
        hits += 1 if hit else 0
        recent.append((target, served_b, true_b, hit))
    conn.close()
    n = len(recent)
    return {"n": n, "hits": hits, "rate": (hits / n if n else None),
            "recent": recent}


def fetch_settled_snapshots() -> list[dict]:
    """Settled market snapshots, ONE canonical row per (place, target_date),
    shaped for `edge.score_snapshot`: each dict carries `place`, `target_date`,
    `realized_label`, and `buckets` (list of {label, model_prob, market_prob}).

    Why dedup here, at the read/scoring boundary: C7 grades DISTINCT SETTLED
    DAYS. Its `n`, its proper-score means, and especially its paired bootstrap
    all assume ONE independent observation per city per day. But several writers
    can leave more than one row for the same (place, target_date): the
    accumulator LaunchAgent fires twice daily for wake-robustness, the
    healthcheck and ad-hoc manual runs also snapshot, and a crashed re-run can
    leave a partial row. Scoring every row would inflate `n` with
    intraday-correlated observations and falsely narrow the bootstrap CI — i.e.
    manufacture a non-existent edge. Defending at the writer (idempotency guards)
    is necessary but not sufficient: it cannot retract rows other writers already
    laid down. So we collapse here to the FIRST-issued snapshot per
    (place, target_date) — the most genuinely day-ahead, least settlement-leaking
    row — chosen deterministically by ascending issued_at."""
    conn = _connect()
    rows = conn.execute(
        "SELECT place, target_date, realized_label, buckets_json, issued_at "
        "FROM market_snapshots WHERE realized_label IS NOT NULL "
        "ORDER BY place, target_date, issued_at",
    ).fetchall()
    conn.close()
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for place_label, target, realized_label, buckets_json, _issued in rows:
        key = (place_label, target)
        if key in seen:
            continue                       # earlier issued_at already kept for this day
        seen.add(key)
        out.append({
            "place": place_label,
            "target_date": target,
            "realized_label": realized_label,
            "buckets": json.loads(buckets_json),
        })
    return out


# --------------------------------------------------------------------------- #
# Tracked-forecaster ledger — prospective, head-to-head, recommend-only.       #
# --------------------------------------------------------------------------- #

def _anchored_actual(sources: Sources, truth_kind, station_id,
                     station_icao, station_name, fc_lat, fc_lon,
                     place_label: str, target: str,
                     station_cache: dict[str, dict]):
    """Realized (high, low) for `target` from the SAME anchored truth a verdict
    uses: the station's own daily record where the verdict was station-anchored,
    else ERA5 at the exact forecast coordinates. None until the truth is in.
    Mirrors the resolution `verify`/`settle_market_snapshots` already use."""
    if truth_kind == "station" and station_id:
        series = station_cache.get(station_id)
        if series is None:
            try:
                # Rebuild the verdict's EXACT anchor — carrying icao+name so
                # fetch_station_daily's modern overlays fire (EGLC by icao, the
                # HKO Observatory by name+geography). A blank station here was the
                # bug that left every tracked row reading only the stale bulk file.
                series = sources.fetch_station_daily(
                    Station(id=station_id, name=station_name or "", wmo=None,
                            icao=station_icao or None,
                            latitude=fc_lat or 0.0, longitude=fc_lon or 0.0,
                            elevation=None, distance_km=0.0))
            except Exception:
                series = {}
            station_cache[station_id] = series
        return series.get(target)
    place = _coord_place(place_label, fc_lat, fc_lon, sources)
    if place is None:
        return None
    day = dt.date.fromisoformat(target)
    return sources.fetch_archive_series(place, day, day).get(target)


def log_tracked_forecast(source: str, place: Place, target: str,
                         fc_high: float, fc_low: float,
                         council_high: float | None, council_low: float | None,
                         truth_source: dict | None) -> None:
    """Persist one tracked (non-council) forecaster's high/low for `target`
    alongside the council's own forecast for the same day, for later head-to-head
    grading against anchored truth.

    INSERT OR IGNORE keeps the FIRST forecast seen for a (source, place, target),
    so the comparison is pinned to one lead rather than silently re-based on every
    later run. Recommend-only: this ledger never feeds the live blend, a verdict,
    or a trade — it exists solely to let a forecaster with no backtestable archive
    earn a measured record before a human ever considers promoting it."""
    ts = truth_source or {}
    station = ts.get("station") or {}
    conn = _connect()
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO tracked_forecasts "
            "(source, issued_at, place, target_date, fc_high, fc_low, "
            " council_high, council_low, truth_kind, station_id, "
            " station_icao, station_name, fc_lat, fc_lon) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (source, dt.datetime.now().isoformat(timespec="seconds"),
             place.label(), target, fc_high, fc_low,
             council_high, council_low, ts.get("kind"),
             station.get("id") or None,
             station.get("icao") or None, station.get("name") or None,
             place.latitude, place.longitude),
        )
    conn.close()


def settle_tracked_forecasts(sources: Sources | None = None) -> list[str]:
    """Fill realized high/low for tracked forecasts whose day has passed (>2 days,
    matching the station-lag cutoff used elsewhere), scored against the SAME
    anchored truth the council verdict uses. Leak-free and truth-matched: a row
    is only graded once its anchored record is available. Returns a settlement
    note per newly graded row."""
    sources = sources or Sources()
    cutoff = (dt.date.today() - dt.timedelta(days=2)).isoformat()
    conn = _connect()
    rows = conn.execute(
        "SELECT source, place, target_date, truth_kind, station_id, "
        "       station_icao, station_name, fc_lat, fc_lon "
        "FROM tracked_forecasts WHERE actual_high IS NULL AND target_date <= ?",
        (cutoff,),
    ).fetchall()
    station_cache: dict[str, dict] = {}
    report: list[str] = []
    for (source, place_label, target, truth_kind, station_id,
         station_icao, station_name, fc_lat, fc_lon) in rows:
        actual = _anchored_actual(sources, truth_kind, station_id,
                                  station_icao, station_name, fc_lat, fc_lon,
                                  place_label, target, station_cache)
        if not actual:
            continue                       # anchored truth not yet available
        a_high, a_low = actual
        with conn:
            conn.execute(
                "UPDATE tracked_forecasts SET actual_high=?, actual_low=?, settled_at=? "
                "WHERE source=? AND place=? AND target_date=?",
                (a_high, a_low, dt.datetime.now().isoformat(timespec="seconds"),
                 source, place_label, target),
            )
        report.append(f"{source}/{place_label} {target}: "
                      f"actual {a_high:.1f}/{a_low:.1f}")
    conn.close()
    return report


def tracked_forecast_scores(source: str) -> dict:
    """Head-to-head summary for one tracked forecaster over its SETTLED days where
    the council also recorded a forecast — both scored on the identical day set so
    the comparison is apples-to-apples. Each settled day contributes its high and
    low error. Returns {'n', 'source_mae', 'council_mae'}; n is settled days, and
    the MAEs are None until at least one day settles. Read-only."""
    conn = _connect()
    rows = conn.execute(
        "SELECT fc_high, fc_low, council_high, council_low, actual_high, actual_low "
        "FROM tracked_forecasts "
        "WHERE source=? AND actual_high IS NOT NULL AND council_high IS NOT NULL",
        (source,),
    ).fetchall()
    conn.close()
    src_errs: list[float] = []
    cou_errs: list[float] = []
    for fc_h, fc_l, co_h, co_l, a_h, a_l in rows:
        src_errs += [abs(fc_h - a_h), abs(fc_l - a_l)]
        cou_errs += [abs(co_h - a_h), abs(co_l - a_l)]
    return {
        "n": len(rows),
        "source_mae": (sum(src_errs) / len(src_errs)) if src_errs else None,
        "council_mae": (sum(cou_errs) / len(cou_errs)) if cou_errs else None,
    }
