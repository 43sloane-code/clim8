"""lock_logger.py — LIVE certification ledger for the intraday lock, PER CITY (pre-registered).

The lock's "95% by 15:00" is a backtest claim; its live wins were chat anecdotes and unscored
report files. This tool gives the flagship the same treatment every other claim already gets:
each run logs the lever's CURRENT output point-in-time (never retro-computed), settles past
days against each city's WU settlement record, and prints the per-city coverage-vs-stated
table against the FROZEN bar in ledger/preregistered/singapore_lock_certification.md:

    per certification hour, at n>=20: CERTIFIED iff hit-rate >= mean(stated) - 10pp,
    else the served conviction label for that hour is DOWNGRADED to the empirical number.

MULTI-CITY (2026-07-12, executes ledger/preregistered/london_lock_instrumentation.md §1):
London serves a daily lock but could never certify — the ledger was Singapore-hardwired.
Now `CITIES` configures each certified city; Singapore's frozen bar/hours are UNCHANGED.

SCHEMA NOTE — ledger/singapore_lock.jsonl (historical name; now the ALL-city lock ledger):
one JSON row per (city, target_date, hour); rows without a "city" field predate 2026-07-12
and are migrated to "Singapore" on load. SETTLEMENT is per city, ALWAYS the WU record
(whole-°F daily max → whole-°C round-half-up). For London this supersedes the prereg's
2026-07-06 "IEM-EGLC" line: the 2026-07-07 user directive ("wunderground only") explicitly
routes the LIVE LOCK through the WU EGLC record — WU catches °F-boundary peaks IEM rounds
away (KAT: test_london_settlement_is_wunderground_backtest_is_iem).

Recommend-only: this ledger never moves a served verdict; it certifies (or downgrades) the
LABEL the system may honestly serve. Runs inside daily_verdict (4×/day) + accumulate.

Run:       PYTHONPATH=. python3 tools/lock_logger.py
Self-test: PYTHONPATH=. python3 tools/lock_logger.py --selftest
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import math
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "ledger" / "singapore_lock.jsonl"   # historical name; ALL-city ledger (schema note)
REPORTS = ROOT / "reports"
TZ = "Asia/Singapore"                            # back-compat alias = CITIES["Singapore"]["tz"]
CERT_HOURS = (12, 13, 14, 15, 16, 18)            # back-compat alias = Singapore's frozen hours
N_FLOOR = 20                    # frozen: no certified language below this
TOL = 0.10                      # frozen: -10pp tolerance vs mean stated conviction

# Certified-lock cities. Singapore's hours/bar are the FROZEN 2026-07-06 registration —
# unchanged. London's certification hours bracket its July peak (15:00, IQR 13-16, late-spike
# tail to 18:00); its bar is the same frozen (n>=20, -10pp) rule. `seed_glob` is the dated
# point-in-time report artifact pattern (Singapore only — London has no report archive; its
# rows are live-only, which is the point of this instrumentation).
CITIES: dict[str, dict] = {
    "Singapore": {
        "icao": "WSSS", "tz": "Asia/Singapore",
        "place": dict(name="Singapore", country="Singapore",
                      latitude=1.3502, longitude=103.994),
        "cert_hours": (12, 13, 14, 15, 16, 18),
        "seed_glob": "verdict-singapore-*sgt.txt",
    },
    "London": {
        "icao": "EGLC", "tz": "Europe/London",
        "place": dict(name="London", country="United Kingdom",
                      latitude=51.5053, longitude=0.0553),
        "cert_hours": (13, 14, 15, 16, 17, 18),
        "seed_glob": None,
    },
}

# report-seed regexes (the SHARPENING block daily_verdict writes)
_RE_RMAX = re.compile(r"running max by (\d+):00 local:\s*([\d.]+)\s*°C")
_RE_NRISE = re.compile(r"remaining-rise learned from (\d+) strictly-earlier days")
_RE_MODAL = re.compile(r"=>\s*(?:HIGH-CONVICTION call:|still diffuse \()\s*(\d+)\s*°C at (\d+)%")
_RE_FNAME = re.compile(r"verdict-singapore-(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})sgt\.txt$")


def _bucket_f(temp_f: float) -> int:
    """Whole-°F WU reading -> the round-half-up whole-°C settlement bucket."""
    return math.floor((temp_f - 32.0) * 5.0 / 9.0 + 0.5)


def load_rows() -> list[dict]:
    if not LOG.exists():
        return []
    rows: list[dict] = []
    with open(LOG) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue        # one truncated line must not brick the ledger's readers
            if isinstance(r, dict):
                rows.append(r)
    for r in rows:                       # migration: pre-2026-07-12 rows predate the city field
        r.setdefault("city", "Singapore")
    return rows


def save_rows(rows: list[dict]) -> None:
    # Atomic tmp+rename: this REWRITES the whole certification ledger, and it is
    # invoked from three schedulers (daily_verdict plists, accumulate, tape plist)
    # — a crash mid-"w" truncated the irreplaceable file.
    LOG.parent.mkdir(exist_ok=True)
    tmp = LOG.with_suffix(".jsonl.tmp")
    with open(tmp, "w") as f:
        for r in sorted(rows, key=lambda r: (r.get("city", "Singapore"),
                                             r["target_date"], r["hour"])):
            f.write(json.dumps(r) + "\n")
    os.replace(tmp, LOG)


def _key(row: dict) -> tuple[str, str, int]:
    return (row.get("city", "Singapore"), row["target_date"], int(row["hour"]))


def parse_report(text: str, fname: str) -> dict | None:
    """PURE: one seed row from a dated report file, or None. The lever's own printed hour
    (running-max-by line) is the row hour; the filename timestamp is the issue time."""
    m = _RE_FNAME.search(fname)
    if not m:
        return None
    date, hh, mm = m.group(1), m.group(2), m.group(3)
    rmax = _RE_RMAX.search(text)
    modal = _RE_MODAL.search(text)
    if not (rmax and modal):
        return None
    nr = _RE_NRISE.search(text)
    return {"city": "Singapore",     # the report archive is Singapore-only (daily_verdict)
            "target_date": date, "issued_ts": f"{date}T{hh}:{mm}:00+08:00",
            "hour": int(rmax.group(1)), "kind": "sharpened",
            "running_max_c": float(rmax.group(2)),
            "n_rise": int(nr.group(1)) if nr else None,
            "modal_bucket": int(modal.group(1)),
            "modal_prob": int(modal.group(2)) / 100.0,
            "pmf_top4": None, "source": "seed:report"}


def seed_from_reports(rows: list[dict]) -> int:
    seen = {_key(r) for r in rows}
    added = 0
    for path in sorted(glob.glob(str(REPORTS / "verdict-singapore-*sgt.txt"))):
        try:
            row = parse_report(open(path).read(), os.path.basename(path))
        except OSError:
            continue
        if row and _key(row) not in seen:
            rows.append(row)
            seen.add(_key(row))
            added += 1
    return added


def log_now(rows: list[dict], city: str = "Singapore") -> str:
    """Append the lever's CURRENT output (point-in-time). Idempotent per (city, date, hour)."""
    from weather_council.intraday_ceiling import intraday_ceiling
    from weather_council.sources import Place, Sources
    cfg = CITIES[city]
    now = _dt.datetime.now(ZoneInfo(cfg["tz"]))
    place = Place(timezone=cfg["tz"], **cfg["place"])
    c = intraday_ceiling(place, now.date(), sources=Sources())
    hour = int(c.hour) if c.hour is not None else now.hour
    row = {"city": city, "target_date": now.date().isoformat(),
           "issued_ts": now.isoformat(timespec="seconds"), "hour": hour,
           "kind": c.kind, "running_max_c": c.running_max_c, "n_rise": c.n_rise,
           "modal_bucket": c.modal_bucket, "modal_prob": c.modal_prob,
           "pmf_top4": [list(t) for t in (c.pmf or ())[:4]] or None,
           "day_state": getattr(c, "day_state", None),
           "state_late_risk": getattr(c, "state_late_risk", None),
           "live_cur_f": getattr(c, "live_cur_f", None),
           "live_max24_f": getattr(c, "live_max24_f", None),
           "feed": getattr(c, "feed", "v1"),
           "source": "live:intraday_ceiling"}
    if _key(row) in {_key(r) for r in rows}:
        return f"{city}: already logged (date {row['target_date']}, hour {hour}) — idempotent"
    rows.append(row)
    return (f"{city}: logged {row['target_date']} {hour:02d}:00 kind={c.kind} "
            f"modal={c.modal_bucket} @ {c.modal_prob if c.modal_prob is None else round(c.modal_prob, 2)}")


def settle_rows(rows: list[dict], settled_map: dict[str, int],
                city: str = "Singapore") -> int:
    """PURE: fill settled_bucket/hit for THIS city's rows whose date is in settled_map.
    City-scoped — a Singapore settlement map must never settle a same-date London row."""
    n = 0
    for r in rows:
        if r.get("city", "Singapore") != city:
            continue
        if r.get("settled_bucket") is None and r["target_date"] in settled_map:
            r["settled_bucket"] = settled_map[r["target_date"]]
            if r["kind"] == "sharpened" and r.get("modal_bucket") is not None:
                r["hit"] = (r["modal_bucket"] == r["settled_bucket"])
            n += 1
    return n


def fetch_settled(dates: list[str], city: str = "Singapore") -> dict[str, int]:
    """The city's WU settled bucket for each date (whole-°F daily max -> settlement °C
    bucket, round-half-up). London included: the lock settles on the WU EGLC record per the
    2026-07-07 directive (supersedes the prereg's IEM line — see module docstring)."""
    from weather_council.sources import Sources, WU_LOCATION, WU_HISTORY_URL, WU_API_KEY
    if not dates:
        return {}
    cfg = CITIES[city]
    src = Sources()
    zone = ZoneInfo(cfg["tz"])
    lo, hi = min(dates), max(dates)
    try:
        d = src.http.get_json(
            WU_HISTORY_URL.format(loc=WU_LOCATION[cfg["icao"]]),
            {"apiKey": WU_API_KEY, "units": "e",
             "startDate": lo.replace("-", ""), "endDate": hi.replace("-", "")})
    except Exception:
        return {}
    highs: dict[str, float] = {}
    for o in (d.get("observations") or []):
        t, vt = o.get("temp"), o.get("valid_time_gmt")
        if isinstance(t, (int, float)) and isinstance(vt, (int, float)):
            day = _dt.datetime.fromtimestamp(vt, tz=_dt.timezone.utc).astimezone(zone).date().isoformat()
            highs[day] = max(highs.get(day, -999.0), float(t))
    return {day: _bucket_f(f) for day, f in highs.items() if day in set(dates)}


def settle_cross_check(rows: list[dict]) -> list[str]:
    """PURE: the settlement TRUTH-SPINE guard. The settled bucket comes from max(half-hourly
    obs) — but the day's logged rows carry the fused live-register floor (running_max_c), and
    07-04 proved the register can exceed the listed rows (92°F vs 91). If any settled day's
    banked floor implies a HIGHER bucket than its settled bucket, that settlement is suspect:
    warn loudly and stamp `register_bucket` on the rows. Never silently rewrites a settlement."""
    import math
    warnings = []
    by_day: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        if r.get("settled_bucket") is not None and r.get("running_max_c") is not None:
            by_day.setdefault((r.get("city", "Singapore"), r["target_date"]), []).append(r)
    for (city, day), rs in sorted(by_day.items()):
        reg = max(math.floor(r["running_max_c"] + 0.5) for r in rs)
        settled = rs[0]["settled_bucket"]
        if reg > settled:
            for r in rs:
                r["register_bucket"] = reg
            warnings.append(f"SETTLE DIVERGENCE {city} {day}: banked register floor implies "
                            f"{reg} but half-hourly settle recorded {settled} — verify against "
                            f"the WU daily-summary before trusting this settlement")
    return warnings


def coverage(rows: list[dict], hours=CERT_HOURS, city: str = "Singapore") -> dict[int, dict]:
    """PURE: per-hour {n, mean_stated, hit_rate, gap} over THIS city's settled SHARPENED
    rows. Default city keeps every pre-existing caller (eval_harness) on Singapore's frozen
    table even after London rows accrue in the same ledger."""
    out: dict[int, dict] = {}
    for h in hours:
        g = [r for r in rows if r.get("city", "Singapore") == city
             and int(r["hour"]) == h and r.get("hit") is not None
             and r.get("modal_prob") is not None]
        if not g:
            continue
        n = len(g)
        stated = sum(r["modal_prob"] for r in g) / n
        hit = sum(1 for r in g if r["hit"]) / n
        out[h] = {"n": n, "mean_stated": stated, "hit_rate": hit, "gap": hit - stated}
    return out


def certify(cov: dict[int, dict], n_floor=N_FLOOR, tol=TOL) -> dict[int, str]:
    """PURE: the FROZEN bar — CERTIFIED iff hit >= stated - tol at n >= n_floor."""
    return {h: ("ACCRUING" if c["n"] < n_floor else
                "CERTIFIED" if c["hit_rate"] >= c["mean_stated"] - tol else
                "OVERCONFIDENT -> serve the empirical rate")
            for h, c in cov.items()}


def report(rows: list[dict]) -> None:
    sharp = [r for r in rows if r["kind"] == "sharpened"]
    settled = [r for r in sharp if r.get("hit") is not None]
    print(f"  LOCK LEDGER: {len(rows)} rows ({len(sharp)} sharpened, {len(settled)} settled) "
          f"— certification at n>={N_FLOOR}/hour, tol -{TOL:.0%} (frozen)")
    for city, cfg in CITIES.items():
        cov = coverage(rows, hours=cfg["cert_hours"], city=city)
        if not cov:
            print(f"  {city}: no settled certification-hour rows yet — accruing")
            continue
        stat = certify(cov)
        print(f"  {city} (local hours {cfg['cert_hours'][0]}–{cfg['cert_hours'][-1]}):")
        print(f"  {'hour':>6}{'n':>4}{'stated':>9}{'hit':>7}{'gap':>7}   status")
        for h in sorted(cov):
            c = cov[h]
            print(f"  {h:>4}:00{c['n']:>4}{c['mean_stated']:>9.0%}{c['hit_rate']:>7.0%}"
                  f"{c['gap']:>+7.0%}   {stat[h]}")


def _selftest() -> int:
    # seed parser: locked + diffuse report snippets
    locked = ("    running max by 18:00 local: 27.8°C (Changi WSSS ...)\n"
              "    remaining-rise learned from 160 strictly-earlier days (leak-free)\n"
              "    => HIGH-CONVICTION call: 28°C at 100% (vs ~56% day-ahead)")
    r = parse_report(locked, "verdict-singapore-2026-06-30-1809sgt.txt")
    assert r and r["hour"] == 18 and r["modal_bucket"] == 28 and r["modal_prob"] == 1.0 and r["n_rise"] == 160
    diffuse = ("    running max by 04:00 local: 28.9°C (...)\n"
               "    => still diffuse (34°C at 26%) — too early")
    r2 = parse_report(diffuse, "verdict-singapore-2026-07-02-0449sgt.txt")
    assert r2 and r2["hour"] == 4 and r2["modal_bucket"] == 34 and abs(r2["modal_prob"] - 0.26) < 1e-9
    assert parse_report("no lock lines here", "verdict-singapore-2026-07-02-0449sgt.txt") is None
    assert parse_report(locked, "not-a-verdict.txt") is None
    # seed rows are Singapore (the report archive is Singapore-only)
    assert r["city"] == "Singapore"
    # settle + hit, CITY-SCOPED: a Singapore map must not settle a same-date London row.
    rows = [{"target_date": "2026-06-30", "hour": 18, "kind": "sharpened",
             "modal_bucket": 28, "modal_prob": 1.0},                       # legacy (no city)
            {"target_date": "2026-06-30", "hour": 4, "kind": "unavailable",
             "modal_bucket": None, "modal_prob": None},
            {"city": "London", "target_date": "2026-06-30", "hour": 15,
             "kind": "sharpened", "modal_bucket": 18, "modal_prob": 0.9}]
    assert settle_rows(rows, {"2026-06-30": 28}) == 2                      # Singapore only
    assert rows[0]["hit"] is True and "hit" not in rows[1]
    assert rows[2].get("settled_bucket") is None                           # London untouched
    assert settle_rows(rows, {"2026-06-30": 18}, city="London") == 1
    assert rows[2]["hit"] is True
    # settlement bucket math: 90F=32.2C -> 32 ; 82F=27.8C -> 28 ; London 64F=17.8C -> 18
    assert _bucket_f(90) == 32 and _bucket_f(82) == 28 and _bucket_f(64) == 18
    # per-city coverage isolation: London rows never pollute Singapore's frozen table
    iso = [{"city": "London", "target_date": f"L{i}", "hour": 15, "kind": "sharpened",
            "modal_bucket": 18, "modal_prob": 0.9, "hit": False} for i in range(25)]
    assert coverage(iso, hours=(15,)) == {}                                # Singapore view
    assert coverage(iso, hours=(15,), city="London")[15]["n"] == 25
    # city-keyed idempotence: same (date, hour) in two cities are DISTINCT rows
    assert _key({"city": "London", "target_date": "d", "hour": 15}) != \
           _key({"target_date": "d", "hour": 15})
    # coverage + the frozen bar
    many = [{"target_date": f"d{i}", "hour": 15, "kind": "sharpened", "modal_bucket": 30,
             "modal_prob": 0.95, "hit": i < 18} for i in range(20)]          # 18/20 = 90%
    cov = coverage(many, hours=(15,))
    assert cov[15]["n"] == 20 and abs(cov[15]["hit_rate"] - 0.90) < 1e-9
    assert certify(cov)[15] == "CERTIFIED"                                    # 90 >= 95-10
    for r_ in many[:3]:
        r_["hit"] = False                                                     # 15/20 = 75%
    assert certify(coverage(many, hours=(15,)))[15].startswith("OVERCONFIDENT")
    assert certify(coverage(many[:5], hours=(15,)))[15] == "ACCRUING"         # n<20
    print("lock_logger selftest PASS (report parse locked+diffuse, settle/hit, bucket math, "
          "coverage, frozen certify bar incl. downgrade + accruing)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()

    # Three schedulers invoke this (daily_verdict plists, accumulate, tape plist)
    # with no shared lock — a concurrent load→modify→rewrite raced away rows.
    # Serialize the whole read-modify-write on a sidecar lock file.
    import fcntl
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG.with_suffix(".lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        rows = load_rows()
        for city in CITIES:
            try:
                print(f"  {log_now(rows, city)}")
            except Exception as exc:      # one city's feed failure must not starve the others
                print(f"  {city}: log_now failed (non-fatal): {exc}")
        seeded = seed_from_reports(rows)
        if seeded:
            print(f"  seeded {seeded} row(s) from dated report files (point-in-time artifacts)")
        for city, cfg in CITIES.items():
            today = _dt.datetime.now(ZoneInfo(cfg["tz"])).date().isoformat()
            unsettled = sorted({r["target_date"] for r in rows
                                if r.get("city", "Singapore") == city
                                and r.get("settled_bucket") is None
                                and r["target_date"] < today})
            if unsettled:
                n = settle_rows(rows, fetch_settled(unsettled, city), city)
                print(f"  {city}: settled {n} row(s) against the WU/{cfg['icao']} record")
        for w in settle_cross_check(rows):
            print(f"  !! {w}")
        save_rows(rows)
    report(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
