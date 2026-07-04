"""lock_logger.py — LIVE certification ledger for the Singapore intraday lock (pre-registered).

The lock's "95% by 15:00" is a backtest claim; its live wins were chat anecdotes and unscored
report files. This tool gives the flagship the same treatment every other claim already gets:
each run logs the lever's CURRENT output point-in-time (never retro-computed) to
`ledger/singapore_lock.jsonl`, seeds rows from existing dated `reports/verdict-*.txt` files
(themselves point-in-time artifacts — parsed, never re-computed), settles past days against the
WU/Changi settlement bucket, and prints the coverage-vs-stated-conviction table against the
FROZEN bar in ledger/preregistered/singapore_lock_certification.md:

    per hour in {12,13,14,15,16,18}, at n>=20: CERTIFIED iff hit-rate >= mean(stated) - 10pp,
    else the served conviction label for that hour is DOWNGRADED to the empirical number.

Recommend-only: this ledger never moves a served verdict; it certifies (or downgrades) the
LABEL the system may honestly serve. Runs inside daily_verdict (09:00/15:00 SGT) + accumulate.

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
LOG = ROOT / "ledger" / "singapore_lock.jsonl"
REPORTS = ROOT / "reports"
TZ = "Asia/Singapore"
CERT_HOURS = (12, 13, 14, 15, 16, 18)
N_FLOOR = 20                    # frozen: no certified language below this
TOL = 0.10                      # frozen: -10pp tolerance vs mean stated conviction

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
    with open(LOG) as f:
        return [json.loads(l) for l in f if l.strip()]


def save_rows(rows: list[dict]) -> None:
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "w") as f:
        for r in sorted(rows, key=lambda r: (r["target_date"], r["hour"])):
            f.write(json.dumps(r) + "\n")


def _key(row: dict) -> tuple[str, int]:
    return (row["target_date"], int(row["hour"]))


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
    return {"target_date": date, "issued_ts": f"{date}T{hh}:{mm}:00+08:00",
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


def log_now(rows: list[dict]) -> str:
    """Append the lever's CURRENT output (point-in-time). Idempotent per (date, hour)."""
    from weather_council.intraday_ceiling import intraday_ceiling
    from weather_council.sources import Place, Sources
    now = _dt.datetime.now(ZoneInfo(TZ))
    place = Place(name="Singapore", country="Singapore",
                  latitude=1.3502, longitude=103.994, timezone=TZ)
    c = intraday_ceiling(place, now.date(), sources=Sources())
    hour = int(c.hour) if c.hour is not None else now.hour
    row = {"target_date": now.date().isoformat(),
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
        return f"already logged (date {row['target_date']}, hour {hour}) — idempotent"
    rows.append(row)
    return (f"logged {row['target_date']} {hour:02d}:00 kind={c.kind} "
            f"modal={c.modal_bucket} @ {c.modal_prob if c.modal_prob is None else round(c.modal_prob, 2)}")


def settle_rows(rows: list[dict], settled_map: dict[str, int]) -> int:
    """PURE: fill settled_bucket/hit for rows whose date is in settled_map. Returns #filled."""
    n = 0
    for r in rows:
        if r.get("settled_bucket") is None and r["target_date"] in settled_map:
            r["settled_bucket"] = settled_map[r["target_date"]]
            if r["kind"] == "sharpened" and r.get("modal_bucket") is not None:
                r["hit"] = (r["modal_bucket"] == r["settled_bucket"])
            n += 1
    return n


def fetch_settled(dates: list[str]) -> dict[str, int]:
    """WU/Changi settled bucket for each date (whole-°F daily max -> settlement °C bucket)."""
    from weather_council.sources import Sources, WU_LOCATION, WU_HISTORY_URL, WU_API_KEY
    if not dates:
        return {}
    src = Sources()
    zone = ZoneInfo(TZ)
    lo, hi = min(dates), max(dates)
    try:
        d = src.http.get_json(
            WU_HISTORY_URL.format(loc=WU_LOCATION["WSSS"]),
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
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("settled_bucket") is not None and r.get("running_max_c") is not None:
            by_day.setdefault(r["target_date"], []).append(r)
    for day, rs in sorted(by_day.items()):
        reg = max(math.floor(r["running_max_c"] + 0.5) for r in rs)
        settled = rs[0]["settled_bucket"]
        if reg > settled:
            for r in rs:
                r["register_bucket"] = reg
            warnings.append(f"SETTLE DIVERGENCE {day}: banked register floor implies {reg} "
                            f"but half-hourly settle recorded {settled} — verify against the "
                            f"WU daily-summary before trusting this settlement")
    return warnings


def coverage(rows: list[dict], hours=CERT_HOURS) -> dict[int, dict]:
    """PURE: per-hour {n, mean_stated, hit_rate, gap} over settled SHARPENED rows."""
    out: dict[int, dict] = {}
    for h in hours:
        g = [r for r in rows if int(r["hour"]) == h and r.get("hit") is not None
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
    cov = coverage(rows)
    if not cov:
        print("  no settled certification-hour rows yet — accruing")
        return
    stat = certify(cov)
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
    # settle + hit
    rows = [{"target_date": "2026-06-30", "hour": 18, "kind": "sharpened",
             "modal_bucket": 28, "modal_prob": 1.0},
            {"target_date": "2026-06-30", "hour": 4, "kind": "unavailable",
             "modal_bucket": None, "modal_prob": None}]
    assert settle_rows(rows, {"2026-06-30": 28}) == 2
    assert rows[0]["hit"] is True and "hit" not in rows[1]
    # settlement bucket math: 90F=32.2C -> 32 ; 82F=27.8C -> 28
    assert _bucket_f(90) == 32 and _bucket_f(82) == 28
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

    rows = load_rows()
    print(f"  {log_now(rows)}")
    seeded = seed_from_reports(rows)
    if seeded:
        print(f"  seeded {seeded} row(s) from dated report files (point-in-time artifacts)")
    today = _dt.datetime.now(ZoneInfo(TZ)).date().isoformat()
    unsettled = sorted({r["target_date"] for r in rows
                        if r.get("settled_bucket") is None and r["target_date"] < today})
    if unsettled:
        n = settle_rows(rows, fetch_settled(unsettled))
        print(f"  settled {n} row(s) against the WU/Changi record")
    for w in settle_cross_check(rows):
        print(f"  !! {w}")
    save_rows(rows)
    report(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
