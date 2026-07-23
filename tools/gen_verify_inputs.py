"""gen_verify_inputs.py — build the two inputs verify_skill.py consumes, from real data.

  season_base_rates.json  — per-city climatology = frequency of the daily-max settlement bucket
                            over the INDEPENDENT WU record (180 days), NOT the small verdict sample
                            (fixes the LOO-degeneracy defect). WU stations only (WSSS/RPLL/EGLC);
                            cities without a WU station are simply absent → verify_skill skips them.
                            The file carries a `_meta` provenance block: generation date, window,
                            source endpoint, and the daily-max/min-obs rule.
  records.jsonl           — one settled city-day per line: date, city, lead (day_ahead if the
                            snapshot was issued before the settlement day, same_day if issued on
                            the settlement day, post_peak if after), the council model_prob pmf,
                            the settled bucket, the smallest top-k band reaching 0.80
                            (band_lo/band_hi/conviction), and the issue hour.

Read-only. Deduped one snapshot per (city, date), earliest kept (day-ahead-first). Never imputes.
A failed WU chunk aborts the run and leaves the old files in place.
Run:  PYTHONPATH=. python3 tools/gen_verify_inputs.py
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

from weather_council.sources import (
    Sources, WU_LOCATION, WU_HISTORY_URL, WU_API_KEY, WU_MIN_DAY_OBS,
    _round_half_up,
)

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "verdicts.db"
SPAN_TARGET = 0.80
BASE_DAYS = 180
_WU = {"singapore": ("WSSS", "Asia/Singapore"), "manila": ("RPLL", "Asia/Manila"),
       "london": ("EGLC", "Europe/London")}
_TZ = {"Singapore": "Asia/Singapore", "Manila": "Asia/Manila", "Hong Kong": "Asia/Hong_Kong",
       "London": "Europe/London", "Chicago": "America/Chicago", "Tokyo": "Asia/Tokyo"}


_b = _round_half_up                     # canonical settlement rounding (whole °C)


def _bucket_f(f):
    return _round_half_up((f - 32.0) * 5.0 / 9.0)      # whole-°F -> round-half-up °C bucket


def _int(label):
    m = re.search(r"-?\d+", str(label))
    return int(m.group()) if m else None


def _tz(place):
    for k, v in _TZ.items():
        if k in place:
            return ZoneInfo(v)
    return ZoneInfo("UTC")


def _fetch_daily_max_range(src, icao: str, start: _dt.date, end: _dt.date,
                           timezone: str) -> tuple[list[float], int]:
    """Fetch the WU on-hour history in chunks, group onto LOCAL days, drop incomplete
    days, and return the list of daily max °F values plus the number of failed chunks.

    This is the same daily-max construction the market settles on; failures are counted
    so a holed archive cannot silently become the climo reference."""
    loc = WU_LOCATION.get((icao or "").upper())
    if loc is None:
        return [], 1
    try:
        zone = ZoneInfo(timezone)
    except Exception:
        zone = ZoneInfo("UTC")
    by_date: dict[str, list[float]] = defaultdict(list)
    failures = 0
    cur = start
    while cur <= end:
        chunk_end = min(cur + _dt.timedelta(days=30), end)
        try:
            data = src.http.get_json(
                WU_HISTORY_URL.format(loc=loc),
                {"apiKey": WU_API_KEY, "units": "e",
                 "startDate": cur.strftime("%Y%m%d"),
                 "endDate": chunk_end.strftime("%Y%m%d")})
        except Exception as exc:
            print(f"    WU chunk {cur.isoformat()}..{chunk_end.isoformat()} failed: {exc}",
                  file=sys.stderr)
            failures += 1
            data = {}
        for o in (data.get("observations") or []):
            t, vt = o.get("temp"), o.get("valid_time_gmt")
            if not isinstance(t, (int, float)) or isinstance(t, bool) \
                    or not isinstance(vt, (int, float)) or isinstance(vt, bool):
                continue
            local = _dt.datetime.fromtimestamp(vt, tz=_dt.timezone.utc).astimezone(zone)
            by_date[local.date().isoformat()].append(float(t))
        cur = chunk_end + _dt.timedelta(days=1)
    out = []
    for d in sorted(by_date):
        temps = by_date[d]
        if len(temps) >= WU_MIN_DAY_OBS:
            out.append(max(temps))
        else:
            print(f"    {d}: dropped (only {len(temps)} obs, need {WU_MIN_DAY_OBS})",
                  file=sys.stderr)
    return out, failures


def season_base_rates(src, end: _dt.date | None = None):
    if end is None:
        # The most recent settled day may still be publishing; stop two days back.
        end = _dt.date.today() - _dt.timedelta(days=2)
    start = end - _dt.timedelta(days=BASE_DAYS)
    climo = {}
    all_failures = 0
    for city, (icao, tzn) in _WU.items():
        max_f_list, failures = _fetch_daily_max_range(src, icao, start, end, tzn)
        all_failures += failures
        if not max_f_list:
            print(f"  climo {city}: NO DATA", file=sys.stderr)
            continue
        bk = [_bucket_f(f) for f in max_f_list]
        cnt = Counter(bk)
        climo[city] = {str(b): cnt[b] / len(bk) for b in sorted(cnt)}
        print(f"  climo {city}: {len(bk)} WU days")
    climo["_meta"] = {
        "generated": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": BASE_DAYS},
        "source": "WU_HISTORY_URL (on-hour observations grouped by station-local day; "
                  f"daily max retained only when ≥{WU_MIN_DAY_OBS} obs/day)",
        "note": "Cross-seasonal for temperate cities — read RPSS city-by-city.",
    }
    return climo, all_failures


def records():
    con = sqlite3.connect(DB)
    try:
        rows = con.execute("SELECT place, target_date, issued_at, buckets_json, realized_label, "
                           "pm_resolved_label FROM market_snapshots ORDER BY issued_at").fetchall()
    finally:
        con.close()
    out, seen = [], set()
    for place, td, iss, bj, rl, pm in rows:
        res = pm or rl
        if not (bj and res):
            continue
        city = place.split(",")[0].strip().lower()
        if (city, td) in seen:
            continue
        try:
            t = _dt.datetime.fromisoformat(iss)
            t = t if t.tzinfo else t.replace(tzinfo=_dt.timezone.utc)
            issue_local = t.astimezone(_tz(place))
            issue_date = issue_local.date()
            target_date = _dt.date.fromisoformat(td)
            if issue_date < target_date:
                lead = "day_ahead"
            elif issue_date == target_date:
                lead = "same_day"
            else:
                lead = "post_peak"
            issue_hour = issue_local.hour + issue_local.minute / 60.0
        except Exception:
            lead = "unknown"
            issue_hour = None
        d = json.loads(bj)
        items = d if isinstance(d, list) else d.get("buckets", [])
        pmf = defaultdict(float)
        for b in items:
            bi, v = _int(b.get("label")), b.get("model_prob")
            if bi is not None and isinstance(v, (int, float)):
                pmf[bi] += v
        if not pmf:
            continue
        s = sum(pmf.values()) or 1.0
        order = sorted(pmf.items(), key=lambda kv: -kv[1])
        cum, chosen = 0.0, []
        for b, p in order:
            chosen.append(b)
            cum += p / s
            if cum >= SPAN_TARGET:
                break
        seen.add((city, td))
        out.append({"date": td, "city": city, "lead": lead, "issue_hour": issue_hour,
                    "probs": {str(k): v / s for k, v in pmf.items()},
                    "settled": _int(res), "band_lo": min(chosen), "band_hi": max(chosen),
                    "conviction": round(cum, 2)})
    return out


def main() -> int:
    src = Sources()
    climo, failures = season_base_rates(src)
    if failures:
        print(f"Aborted: {failures} WU chunk(s) failed; old files left in place.",
              file=sys.stderr)
        return 1
    with open(ROOT / "season_base_rates.json", "w") as f:
        json.dump(climo, f, indent=2)
    recs = records()
    with open(ROOT / "records.jsonl", "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    print(f"  wrote {len(recs)} records, {len(climo) - 1} city base rates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
