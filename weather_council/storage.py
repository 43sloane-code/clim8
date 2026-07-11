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
    'tracked_forecast_scores', 'backfill_pm_resolutions', 'live_bucket_scorecard',
    'log_book_snapshots', 'book_snapshot_coverage', 'utc_now_iso'
]

import contextlib
import datetime as dt
import json
import sqlite3
from pathlib import Path

from .council import Verdict
from .failures import record_soft_failure
from .market import (MarketData, _bucket_edges, _native_reading_int,
                     resolved_event_slug)
from .sources import Place, Sources, Station

DB_PATH = Path(__file__).resolve().parent.parent / "verdicts.db"


def utc_now_iso() -> str:
    """The persisted-timestamp convention (charter: UTC is non-optional). Returns a UTC
    wall-clock instant in NAIVE ISO format (no offset), seconds precision. Naive format is
    deliberate: it keeps new rows byte-compatible with legacy `issued_at` values so the
    lexicographic ordering that `paper_pnl._load_from_db` relies on (ORDER BY … issued_at,
    first-issued = least-leaking) never breaks across the pre/post-UTC boundary. Columns store
    UTC wall-clock, naive format."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def passes_integrity(integrity_flags) -> bool:
    """Served-number remediation campaign (§1.3) — the mandatory measurement filter. A row is INCLUDED
    unless it carries a `*_SUSPECT` contamination flag. NULL/empty flags → included: this is the inert
    Phase-0 default (no row is flagged until a remediation WP runs, so shipping this changes no
    measurement). `*_CORRECTED` / `*_CONFIRMED` flags do NOT exclude (the correction is trusted).
    Unparseable flags → EXCLUDED (fail-closed: a row we can't prove clean is not measured). A
    measurement job (scorecard / p̂_corr / xref calibration) that does not gate on this once flags
    exist is METHOD-DEFECTIVE."""
    if not integrity_flags:
        return True
    try:
        flags = json.loads(integrity_flags) if isinstance(integrity_flags, str) else integrity_flags
    except Exception:
        return False
    return not any(str(f).endswith("_SUSPECT") for f in (flags or []))


def _connect_at(db_path) -> sqlite3.Connection:
    """Open a connection against an ARBITRARY db_path by temporarily swapping the module DB_PATH,
    restored in `finally` (so an exception can't leave it swapped). The single shared implementation
    of the pattern that postmortem/lessons/twc_offset/twc_independence each used to copy."""
    global DB_PATH
    _orig = DB_PATH
    DB_PATH = db_path
    try:
        return _connect()
    finally:
        DB_PATH = _orig


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
        # Issue-time PROVENANCE (Plan 3 Phase 0): the decisions behind the verdict — per-source
        # votes, applied bias, regime, spread — snapshotted at log time so a settled error can be
        # ATTRIBUTED (Phase 3) rather than retrodicted. provenance_ok=0 marks a quarantined blob
        # (stored anyway, alarmed); provenance_json IS NULL == UNATTRIBUTABLE-PREPROVENANCE.
        # Additive/nullable: never alters a served number or an existing score.
        "provenance_json": "TEXT", "provenance_ok": "INTEGER",
        # Served-number remediation campaign (§1.3): append-only contamination markers, e.g.
        # ["F2_DAYMAX_SUSPECT"] / ["F2_DAYMAX_CORRECTED"]. Corrected values land in NEW _v2 fields
        # (added per-WP); the original is never overwritten. Measurement jobs MUST filter via
        # passes_integrity(). NULL == unflagged == included (inert until a WP writes flags).
        "integrity_flags": "TEXT",
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
                "pm_resolved_label": "TEXT", "pm_resolved_at": "TEXT",
                # Served-number campaign (§1.3): contamination markers, primary target is F1
                # (pm_resolved_label settled against the wrong event). Corrected label -> a new
                # pm_resolved_label_v2 field (added in WP-1); the original stays. passes_integrity()
                # gates every measurement. NULL == included (inert until a WP flags).
                "integrity_flags": "TEXT"}
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
    # Order-book archive (Phase 3): one row per (place, target_date, issued_at,
    # token_id) capturing the LIVE CLOB order book at the SAME instant as the price
    # snapshot in market_snapshots (join on place+target_date+issued_at). This is
    # what lets executable depth-walk P&L (paper_pnl) be measured against the
    # theoretical mid the served comparison used — you cannot trade at the mid.
    #
    # READ-ONLY, ADDITIVE, and independent of the served forecast: it records what
    # the book looked like, never a model probability, vote, or trade. A token whose
    # fetch/parse failed is still written as a row with fetch_ok=0 (stats NULL,
    # error set) so a silent gap is impossible — absence of a row means "capture
    # never ran", a fetch_ok=0 row means "ran and this token's book was unavailable".
    # book_json holds the full parsed ladder (bids/asks) so P&L can re-walk it
    # offline without re-fetching. Depth in USD notional (Σ price×size) per side.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS book_snapshots (
               issued_at      TEXT NOT NULL,
               place          TEXT NOT NULL,
               target_date    TEXT NOT NULL,
               token_id       TEXT NOT NULL,
               bucket_label   TEXT,
               fetch_ok       INTEGER NOT NULL,
               best_bid       REAL,
               best_ask       REAL,
               mid            REAL,
               spread         REAL,
               bid_depth_usd  REAL,
               ask_depth_usd  REAL,
               n_bid_levels   INTEGER,
               n_ask_levels   INTEGER,
               book_ts        TEXT,
               book_json      TEXT,
               error          TEXT,
               PRIMARY KEY (place, target_date, issued_at, token_id))"""
    )
    # Post-mortems (Plan 3 Phase 3): one decomposed HIGH error per settled verdict that carried
    # provenance. components_json splits the total error into INPUT / BLEND / BIAS (which
    # telescope exactly to final−actual, identity-checked) plus a settlement_divergence
    # diagnostic. attributed_cause is the taxonomy label. Additive/read-only — a diagnosis,
    # never a served number. PK lets a re-run refine the same (place, target_date) idempotently.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS postmortems (
               place             TEXT NOT NULL,
               target_date       TEXT NOT NULL,
               attr              TEXT NOT NULL,   -- 'high'
               scored_at         TEXT NOT NULL,
               final             REAL,
               actual            REAL,
               total_error       REAL,
               attributed_cause  TEXT,
               components_json   TEXT,
               margin            REAL,            -- distance to the nearest bucket boundary
               crossed_boundary  INTEGER,
               settlement_divergence REAL,
               pipeline_version  TEXT,
               PRIMARY KEY (place, target_date, attr))"""
    )
    # Shadow forecasts (Plan 3 Phase 5): each ACTIVE candidate's transform re-applied to the SAME
    # issue-time inputs stored in provenance, scored against the SAME realized high beside the
    # served verdict — a paired counterfactual, never rendered, never served, never fed back.
    # served_logloss/shadow_logloss are a COMMON-PROXY pair (identical σ + bucket ladder, differing
    # only by the transform's mean-shift), so only their DELTA is meaningful — it is NOT the served
    # calibration edge.py measures. delta_logloss = served − shadow (>0 ⇒ the candidate helped).
    conn.execute(
        """CREATE TABLE IF NOT EXISTS shadow_forecasts (
               candidate_id    TEXT NOT NULL,
               place           TEXT NOT NULL,
               target_date     TEXT NOT NULL,
               scored_at       TEXT NOT NULL,
               served_high     REAL,
               shadow_high     REAL,
               actual          REAL,
               sigma           REAL,
               served_logloss  REAL,
               shadow_logloss  REAL,
               served_brier    REAL,
               shadow_brier    REAL,
               delta_logloss   REAL,   -- served − shadow; >0 ⇒ candidate improved bucket log-loss
               PRIMARY KEY (candidate_id, place, target_date))"""
    )
    conn.commit()
    return conn


def log_verdict(v: Verdict) -> None:
    ts = v.truth_source or {}
    station = ts.get("station") or {}
    # Issue-time provenance (Plan 3 Phase 0): snapshot the DECISIONS behind this verdict so a
    # settled error can be attributed later. Best-effort + quarantine-and-alarm — a provenance
    # bug must NEVER stop the verdict itself being logged (NULL == UNATTRIBUTABLE-PREPROVENANCE).
    prov_json, prov_ok = None, None
    try:
        from .provenance import build_provenance, validate_provenance
        prov = build_provenance(v)
        problems = validate_provenance(prov)
        prov_ok = 0 if problems else 1
        prov_json = json.dumps(prov)
        if problems:
            record_soft_failure("provenance_quarantine", ValueError("; ".join(problems)[:180]))
    except Exception as exc:
        record_soft_failure("provenance_build", exc)      # swallow: still log the verdict
    conn = _connect()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO verdicts "
            "(issued_at, place, target_date, high, low, confidence, "
            " truth_kind, station_id, station_icao, station_name, fc_lat, fc_lon, "
            " provenance_json, provenance_ok) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (utc_now_iso(),
             v.place.label(), v.target, v.high, v.low, v.confidence,
             ts.get("kind"), station.get("id") or None,
             station.get("icao") or None, station.get("name") or None,
             v.place.latitude, v.place.longitude,
             prov_json, prov_ok),
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
        icao_up = (station_icao or "").upper()
        if truth_kind == "station" and icao_up in _WU_SETTLE_TZ:
            # WU-truth city: score against the Wunderground oracle (fetch_station_daily
            # has no overlay for RPLL/WSSS). Same repair as settle_market_snapshots.
            day = dt.date.fromisoformat(target)
            try:
                actual = sources.wunderground_daily_series(
                    icao_up, day, day, _WU_SETTLE_TZ[icao_up]).get(target)
            except Exception:
                actual = None
            truth_note = " vs Wunderground"
        elif truth_kind == "station" and station_id:
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

def log_market_snapshot(v: Verdict, comparison, issued_at: str | None = None) -> str:
    """Persist one council-vs-market comparison for later realized-outcome
    grading. Stores the full bucket ladder with both probability columns; the
    realized bucket is filled in later by `settle_market_snapshots` against the
    verdict's anchor station. `comparison` is a compare.VerdictMarketComparison
    (or anything exposing `.grain`, `.market_title`, and `.buckets` of objects
    with `.label/.lo/.hi/.model_prob/.market_prob`).

    `issued_at` defaults to the current UTC instant; a caller may PASS one so the
    order-book archive (book_snapshots, Phase 4) shares the EXACT same instant and
    the two join on (place, target_date, issued_at). Returns the issued_at used."""
    issued_at = issued_at or utc_now_iso()
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
            (issued_at,
             v.place.label(), v.target, comparison.market_title, comparison.grain,
             json.dumps(buckets), ts.get("kind"), station.get("id") or None,
             station.get("icao") or None, station.get("name") or None,
             v.place.latitude, v.place.longitude,
             getattr(comparison, "market_volume", None),
             getattr(comparison, "market_liquidity", None),
             int(bool(getattr(comparison, "settles_sub_degree", False)))),
        )
    conn.close()
    return issued_at


def log_book_snapshots(place_label: str, target: str, issued_at: str,
                       rows: list[dict]) -> int:
    """Persist a batch of order-book captures into book_snapshots (Phase 4). Each
    `rows` entry is one token's capture at `issued_at` — the SAME instant passed to
    log_market_snapshot, so the book and the price snapshot join on
    (place, target_date, issued_at). Pure DB write: NO network, NO fetch (the caller
    — book_logger — does the read-only fetch and passes results here), and it never
    touches a served probability, vote, or trade.

    A `rows` entry must carry: token_id, and either fetch_ok=True with a `stats` dict
    (from clob_book.book_stats) + `book_json`, or fetch_ok=False with an `error`
    string. Missing keys default to NULL. Returns the number of rows written."""
    conn = _connect()
    with conn:
        for r in rows:
            stats = r.get("stats") or {}
            conn.execute(
                "INSERT OR REPLACE INTO book_snapshots "
                "(issued_at, place, target_date, token_id, bucket_label, fetch_ok, "
                " best_bid, best_ask, mid, spread, bid_depth_usd, ask_depth_usd, "
                " n_bid_levels, n_ask_levels, book_ts, book_json, error) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (issued_at, place_label, target, str(r.get("token_id") or ""),
                 r.get("bucket_label"), 1 if r.get("fetch_ok") else 0,
                 stats.get("best_bid"), stats.get("best_ask"), stats.get("mid"),
                 stats.get("spread"), stats.get("bid_depth_usd"),
                 stats.get("ask_depth_usd"), stats.get("n_bid_levels"),
                 stats.get("n_ask_levels"), stats.get("timestamp"),
                 r.get("book_json"), r.get("error")),
            )
    conn.close()
    return len(rows)


def book_snapshot_coverage(hours: int = 24) -> dict:
    """Read-only summary of order-book capture over the last `hours`: how many rows,
    how many fetched OK vs failed, and how many distinct (place, target, instant)
    capture batches ran. The healthcheck surfaces this so a silent capture stall is
    visible. Returns zeros when the table is empty/absent (never raises)."""
    cutoff = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
              - dt.timedelta(hours=hours)).isoformat(timespec="seconds")
    try:
        with contextlib.closing(_connect()) as conn:   # close even if a query raises (no leak)
            row = conn.execute(
                "SELECT COUNT(*), "
                "       COALESCE(SUM(fetch_ok), 0), "
                "       COUNT(DISTINCT place || '|' || target_date || '|' || issued_at), "
                "       COUNT(DISTINCT place) "
                "FROM book_snapshots WHERE issued_at >= ?", (cutoff,)).fetchone()
            by_place = conn.execute(
                "SELECT place, COUNT(*), COALESCE(SUM(fetch_ok), 0) "
                "FROM book_snapshots WHERE issued_at >= ? GROUP BY place", (cutoff,)).fetchall()
    except Exception:
        return {"rows": 0, "ok": 0, "failed": 0, "batches": 0, "places": 0, "by_place": {}}
    total, ok, batches, places = row
    return {
        "rows": int(total), "ok": int(ok), "failed": int(total) - int(ok),
        "batches": int(batches), "places": int(places),
        "by_place": {p: {"rows": int(n), "ok": int(o), "failed": int(n) - int(o)}
                     for p, n, o in by_place},
    }


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


# Airports whose REALIZED settlement truth is the Wunderground oracle (the record
# the contract pays on), NOT the Meteostat bulk + IEM/HKO overlay fetch_station_daily
# covers. icao -> tz (for local-day grouping). Both settle_market_snapshots and verify
# route these to wunderground_daily_series.
# London (EGLC) is INCLUDED as of 2026-07-07 (user directive "wunderground only"): a 17:20
# late spike hit 90°F=32 that WU published and the market settled on, while the whole-°C IEM
# METAR read 31. SETTLEMENT reads WU here; the BACKTEST anchor stays IEM (10y deep archive
# WU history can't match) — the two agree on ordinary days, diverge only on such spike days.
_WU_SETTLE_TZ = {"RPLL": "Asia/Manila", "WSSS": "Asia/Singapore",
                 "KSFO": "America/Los_Angeles",   # SF: live WU oracle, whole-°F
                 "OPKC": "Asia/Karachi",          # Karachi: settlement WU (backtest stays IEM)
                 "OEJN": "Asia/Riyadh",           # Jeddah: settlement WU (backtest stays IEM)
                 "EGLC": "Europe/London"}         # London: settlement WU (backtest stays IEM)


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
    # Broad host-clock prefilter only; the REAL readiness test is per-row below,
    # because "is the settlement day over" is a CITY-LOCAL question. A WU-oracle
    # station carries a CURRENT feed and is gradable the moment its city-local day
    # ends (T-1) — matching settle_tracked_forecasts. A blanket host today-2 cutoff
    # both stranded completed tropical days (host runs behind SGT) and needlessly
    # quarantined a settled WU day for an extra ~24h, leaving the proxy-vs-contract
    # alarm blind on the freshest resolved day (2026-07-08 London 07-07 audit).
    # Lagged-truth stations (Meteostat bulk file) keep the conservative 2-day buffer.
    today = dt.date.today()
    bulk_cutoff = (today - dt.timedelta(days=2)).isoformat()
    conn = _connect()
    rows = conn.execute(
        "SELECT issued_at, place, target_date, grain, buckets_json, "
        "       truth_kind, station_id, station_icao, station_name, "
        "       fc_lat, fc_lon, sub_degree FROM market_snapshots "
        "WHERE realized_label IS NULL AND target_date <= ?",
        (today.isoformat(),),
    ).fetchall()

    from zoneinfo import ZoneInfo
    station_cache: dict[str, dict] = {}
    report: list[str] = []
    for (issued_at, place_label, target, grain, buckets_json,
         truth_kind, station_id, station_icao, station_name,
         fc_lat, fc_lon, sub_degree) in rows:
        actual = None
        icao_up = (station_icao or "").upper()
        is_wu = truth_kind == "station" and icao_up in _WU_SETTLE_TZ
        # Per-row readiness: a WU-oracle day settles once the CITY-LOCAL day is over
        # (settling earlier — or on a naive host today-1 — could read a still-forming
        # max, e.g. a US-west day is mid-afternoon locally after the host ticks over);
        # lagged-truth stations keep the 2-day bulk-file buffer.
        if is_wu:
            if target >= dt.datetime.now(
                    ZoneInfo(_WU_SETTLE_TZ[icao_up])).date().isoformat():
                continue                   # city-local day not finished — would leak
        elif target > bulk_cutoff:
            continue                       # lagged-truth stations: conservative cutoff
        if is_wu:
            # WU-truth city (Manila RPLL / Singapore WSSS): settle on the SAME
            # Wunderground oracle the verdict + contract pay out on. fetch_station_daily
            # has no WU overlay for these airports (only EGLC/HKO), so it would leave
            # realized_label NULL — the bug this branch repairs. daily_series returns
            # the local-day (max_c, min_c) tuple settle/verify both expect.
            fetcher = sources if injected else Sources()
            day = dt.date.fromisoformat(target)
            try:
                actual = fetcher.wunderground_daily_series(
                    icao_up, day, day, _WU_SETTLE_TZ[icao_up]).get(target)
            except Exception as exc:
                record_soft_failure("settle_wu_fetch", exc)   # swallow stays; no longer silent
                actual = None
        elif truth_kind == "station" and station_id:
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
                except Exception as exc:
                    record_soft_failure("settle_station_fetch", exc)   # swallow stays; not silent
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
                (realized_high, label, utc_now_iso(),
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
        if res is not None and getattr(res, "no_match", False):
            # WP-1 fail-closed: the feed had events but none matched this slug exactly. Do NOT settle
            # (leave pm_resolved_label NULL); surface the near-miss for human repair (slug drift).
            report.append(f"{place_label} {target}: NO MATCH for the settlement slug — "
                          f"candidates: {list(res.near_miss_slugs)} (left unsettled, not poisoned)")
            continue
        if res is None or not res.resolved or not res.winning_label:
            continue                       # not finalized / not found — retry later
        now = utc_now_iso()
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
    # WU-anchored basket stations settle on the Wunderground record itself — the same
    # branch verify/settle_market_snapshots already use. Without this, a tracked row
    # whose station_id is None (e.g. the TWC forward-log rows) fell through to the
    # LAGGED ERA5 path and never settled — the 2026-07-03 audit found the TWC 40-pair
    # clock would have accrued ZERO pairs forever.
    if truth_kind == "station":
        icao_up = (station_icao or "").upper()
        if icao_up in _WU_SETTLE_TZ:
            try:
                day = dt.date.fromisoformat(target)
                return sources.wunderground_daily_series(
                    icao_up, day, day, _WU_SETTLE_TZ[icao_up]).get(target)
            except Exception:
                return None
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
            (source, utc_now_iso(),
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
    # Broad prefilter only (host clock); the REAL readiness test is per-row below, because
    # "the day is over" is a CITY-LOCAL question — the host runs a day behind SGT, so a
    # host-date cutoff left completed Singapore/Manila days permanently "not yet due"
    # (2026-07-03 audit). WU-anchored stations are gradable the moment their city-local
    # day ends (current feed, no lag); lagged-truth stations keep the 2-day cutoff.
    conn = _connect()
    rows = conn.execute(
        "SELECT source, place, target_date, truth_kind, station_id, "
        "       station_icao, station_name, fc_lat, fc_lon "
        "FROM tracked_forecasts WHERE actual_high IS NULL AND target_date <= ?",
        (dt.date.today().isoformat(),),
    ).fetchall()
    from zoneinfo import ZoneInfo
    station_cache: dict[str, dict] = {}
    report: list[str] = []
    for (source, place_label, target, truth_kind, station_id,
         station_icao, station_name, fc_lat, fc_lon) in rows:
        icao_up = (station_icao or "").upper()
        if truth_kind == "station" and icao_up in _WU_SETTLE_TZ:
            local_today = dt.datetime.now(ZoneInfo(_WU_SETTLE_TZ[icao_up])).date()
            if target >= local_today.isoformat():
                continue                   # city-local day not finished — grading would leak
        elif target > (dt.date.today() - dt.timedelta(days=2)).isoformat():
            continue                       # lagged-truth stations: conservative 2-day cutoff
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
                (a_high, a_low, utc_now_iso(),
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
