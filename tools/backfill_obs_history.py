"""backfill_obs_history.py — accumulate the multi-year, settlement-grain WSSS dataset.

The entire live system trains/validates on ~220 days of WU hourly history — one season
slice. Any own-model work the deep-research plan proposes (peak-timing, state-conditional
rises, seasonal remaining-rise depth) needs YEARS of the SETTLEMENT-GRAIN record (whole-°F
WU, the exact feed the market pays on). This pulls N years of WSSS sub-hourly obs through
the existing chunked/cached fetcher and persists them as an explicit dataset artifact —
`data/wsss_hourly.jsonl` (one line per day: {date, obs: [[hh, temp_c], ...]}) — because the
runtime obs cache is keyed per-(start,end) window and TTL'd, so it is NOT a durable dataset.

Idempotent + additive: existing days are kept, only missing days are fetched (in yearly
slices to stay inside API windows). Dataset-only: changes NOTHING served — DEFAULT_BACK_DAYS
and every lever parameter stay untouched (widening a training window is a GATED change).

Run:  PYTHONPATH=. python3 tools/backfill_obs_history.py [--years 3] [--icao WSSS]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
from pathlib import Path

from weather_council.sources import Sources

ROOT = Path(__file__).resolve().parent.parent
_TZ = {"WSSS": "Asia/Singapore", "RPLL": "Asia/Manila", "EGLC": "Europe/London"}


def load_dataset(path: Path) -> dict:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["date"]] = r["obs"]
    return out


def save_dataset(path: Path, data: dict) -> None:
    path.parent.mkdir(exist_ok=True)
    with open(path, "w") as f:
        for d in sorted(data):
            f.write(json.dumps({"date": d, "obs": data[d]}) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--icao", default="WSSS")
    ap.add_argument("--source", choices=("wu", "iem"), default="wu",
                    help="wu = settlement grain (whole-degF); iem = training grain (degC METAR, "
                         "global archive, decade depth)")
    args = ap.parse_args()
    icao = args.icao.upper()
    tz = _TZ[icao]
    suffix = "_hourly.jsonl" if args.source == "wu" else "_hourly_iem.jsonl"
    path = ROOT / "data" / f"{icao.lower()}{suffix}"
    data = load_dataset(path)
    src = Sources()
    end = _dt.date.today()
    start = end - _dt.timedelta(days=365 * args.years)
    print(f"BACKFILL {icao}: target {start} -> {end} ({365*args.years}d); "
          f"dataset already holds {len(data)} days")

    # yearly slices, oldest first; skip slices already fully present
    cur = start
    fetched = 0
    while cur < end:
        ce = min(cur + _dt.timedelta(days=365), end)
        span = [(cur + _dt.timedelta(days=k)).isoformat() for k in range((ce - cur).days)]
        missing = [d for d in span if d not in data]
        if missing:
            try:
                if args.source == "wu":
                    obs = src.wunderground_hourly_observations(icao, cur, ce, tz)
                else:
                    obs = src.fetch_metar_observations(icao, cur, ce, tz)
            except Exception as e:
                print(f"  slice {cur}->{ce}: fetch failed ({type(e).__name__}) — kept going")
                cur = ce
                continue
            by: dict[str, list] = {}
            for ts, c in obs:
                hh = int(ts[11:13]) + int(ts[14:16]) / 60.0
                by.setdefault(ts[:10], []).append([hh, c])
            added = 0
            for d, rows in by.items():
                if d not in data and len(rows) >= 20:      # complete days only
                    data[d] = rows
                    added += 1
            fetched += added
            print(f"  slice {cur} -> {ce}: +{added} days (asked {len(missing)} missing)")
        cur = ce
    save_dataset(path, data)
    if data:
        days = sorted(data)
        grain = "settlement-grain (WU whole-degF)" if args.source == "wu" else "training-grain (IEM degC)"
        print(f"DATASET: {len(days)} {grain} days  {days[0]} -> {days[-1]}  "
              f"(+{fetched} new)  -> {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
