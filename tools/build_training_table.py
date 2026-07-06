"""build_training_table.py — P1 deliverable: the frozen per-day training table for WSSS.

Joins the 10-year IEM obs archive (data/wsss_hourly_iem.jsonl, training grain) with ERA5
hourly predictors (archive-api.open-meteo.com, allowlisted; cloud, shortwave, precip, wind)
into ONE flat file: data/wsss_training.jsonl — one line per day, outcomes + predictors, for
the P2 peak-conditioning probe and any later gated model. A FILE, not a model: nothing served
changes. Predictors are restricted to what is knowable by the hour they claim (morning blocks
end 13:00 local) so downstream probes stay leak-free by construction.

Per-day fields:
  outcomes:   tmax_c, bucket (round-half-up whole °C — TRAINING grain, not WU settlement),
              peak_hh (first hour of the max), rise12/13/14/15 (final − runmax@H),
              late14/late15 (settled bucket still rises after H), state14/state15
              (NOTE: state cols in the existing frozen artifact were built with the pre-
              2026-07-06 SINGLE-READ day-state; the live lever now uses the certified
              2-consecutive rule — regenerate before conditioning any new probe on them)
  obs preds:  runmax12, cur12, prev_tmax, prev_peak_hh, doy_sin, doy_cos
  era5 preds: cloud_8_13 (mean %), sw_8_13 (sum W/m2), precip_0_13 (sum mm), wind_11_14 (mean)

Run:  PYTHONPATH=. python3 tools/build_training_table.py
"""
from __future__ import annotations

import datetime as _dt
import json
import math
from pathlib import Path

from weather_council.sources import ARCHIVE_URL, Sources
from weather_council.intraday_ceiling import _running_max, _final_max, _day_state

ROOT = Path(__file__).resolve().parent.parent
OBS = ROOT / "data" / "wsss_hourly_iem.jsonl"
OUT = ROOT / "data" / "wsss_training.jsonl"
LAT, LON, TZ = 1.3502, 103.994, "Asia/Singapore"
ERA5_VARS = "cloudcover,shortwave_radiation,precipitation,windspeed_10m"


def _b(c):
    return math.floor(c + 0.5)


def fetch_era5(src: Sources, start: _dt.date, end: _dt.date) -> dict[str, dict[str, list]]:
    """date -> {var: [24 hourly values]} over [start, end], yearly chunks."""
    out: dict[str, dict[str, list]] = {}
    cur = start
    while cur <= end:
        ce = min(cur + _dt.timedelta(days=364), end)
        d = src.http.get_json(ARCHIVE_URL, {
            "latitude": LAT, "longitude": LON, "timezone": TZ,
            "hourly": ERA5_VARS,
            "start_date": cur.isoformat(), "end_date": ce.isoformat()})
        h = d.get("hourly", {})
        times = h.get("time", [])
        cols = {v: h.get(v, []) for v in ERA5_VARS.split(",")}
        for i, t in enumerate(times):
            day, hh = t[:10], int(t[11:13])
            slot = out.setdefault(day, {v: [None] * 24 for v in cols})
            for v in cols:
                if i < len(cols[v]):
                    slot[v][hh] = cols[v][i]
        print(f"  era5 {cur} -> {ce}: {len(times)} hours")
        cur = ce + _dt.timedelta(days=1)
    return out


def _agg(vals, lo, hi, how):
    xs = [x for x in (vals or [])[lo:hi + 1] if isinstance(x, (int, float))]
    if not xs:
        return None
    return round(sum(xs) / len(xs), 3) if how == "mean" else round(sum(xs), 3)


def build_rows(obs_by_day: dict, era5: dict) -> list[dict]:
    days = sorted(obs_by_day)
    rows = []
    prev = None
    for d in days:
        o = [(hh, c) for hh, c in obs_by_day[d]]
        fm = _final_max(o)
        if fm is None:
            prev = None
            continue
        peak_hh = min(hh for hh, c in o if c == fm)
        e = era5.get(d, {})
        doy = _dt.date.fromisoformat(d).timetuple().tm_yday
        row = {"date": d, "tmax_c": round(fm, 2), "bucket": _b(fm),
               "peak_hh": round(peak_hh, 1)}
        for H in (12, 13, 14, 15):
            rm = _running_max(o, H)
            row[f"rise{H}"] = round(fm - rm, 2) if rm is not None else None
            if H >= 14:
                row[f"late{H}"] = (_b(fm) > _b(rm)) if rm is not None else None
                row[f"state{H}"] = _day_state(o, H)
        rm12 = _running_max(o, 12)
        cur12 = next((c for hh, c in sorted(o, reverse=True) if hh <= 12), None)
        row.update({
            "runmax12": round(rm12, 2) if rm12 is not None else None,
            "cur12": round(cur12, 2) if cur12 is not None else None,
            "prev_tmax": prev[0] if prev else None,
            "prev_peak_hh": prev[1] if prev else None,
            "doy_sin": round(math.sin(2 * math.pi * doy / 365.25), 4),
            "doy_cos": round(math.cos(2 * math.pi * doy / 365.25), 4),
            "cloud_8_13": _agg(e.get("cloudcover"), 8, 13, "mean"),
            "sw_8_13": _agg(e.get("shortwave_radiation"), 8, 13, "sum"),
            "precip_0_13": _agg(e.get("precipitation"), 0, 13, "sum"),
            "wind_11_14": _agg(e.get("windspeed_10m"), 11, 14, "mean"),
        })
        rows.append(row)
        prev = (round(fm, 2), round(peak_hh, 1))
    return rows


def main() -> int:
    obs_by_day = {}
    for line in OBS.read_text().splitlines():
        r = json.loads(line)
        obs_by_day[r["date"]] = r["obs"]
    days = sorted(obs_by_day)
    print(f"obs days: {len(days)}  {days[0]} -> {days[-1]}")
    src = Sources()
    era5 = fetch_era5(src, _dt.date.fromisoformat(days[0]), _dt.date.fromisoformat(days[-1]))
    rows = build_rows(obs_by_day, era5)
    with open(OUT, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    n_era = sum(1 for r in rows if r["cloud_8_13"] is not None)
    n_late = sum(1 for r in rows if r.get("late15"))
    print(f"TRAINING TABLE: {len(rows)} days -> {OUT.relative_to(ROOT)}")
    print(f"  era5 coverage {n_era}/{len(rows)} | late-after-15 days {n_late} "
          f"({n_late/len(rows):.1%}) | holding@15 "
          f"{sum(1 for r in rows if r.get('state15')=='holding')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
