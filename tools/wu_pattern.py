"""Live + historical WU pattern recognition for a settlement city.

Reports, from the city's Wunderground airport record: today's running max so far
(live feed), the leak-free conditional ANALOG distribution (given today's
running-max-by-noon bucket, what strictly-earlier complete days with the same
morning settled at), the noon->peak rise, the recent settled record, and the
season base rate. Read-only; conditioning uses only earlier complete days.

Usage:  PYTHONPATH=. python3 tools/wu_pattern.py [--city singapore] [--days 150]
"""
from __future__ import annotations
import argparse
import datetime as dt
import statistics
from collections import Counter, defaultdict
from zoneinfo import ZoneInfo

from weather_council.sources import Sources, WU_LOCATION, WU_HISTORY_URL, WU_API_KEY
from weather_council.intraday_ceiling import _HOURLY_STATION
from weather_council.market import _native_reading_int


def main() -> int:
    ap = argparse.ArgumentParser(description="Live+historical WU pattern.")
    ap.add_argument("--city", default="singapore")
    ap.add_argument("--days", type=int, default=150)
    ap.add_argument("--noon", type=int, default=12)
    args = ap.parse_args()
    key = args.city.strip().lower()
    if key not in _HOURLY_STATION:
        print(f"city '{key}' not configured (have: {', '.join(_HOURLY_STATION)})")
        return 1
    icao, tz, sub, name = _HOURLY_STATION[key]
    loc = WU_LOCATION.get(icao.upper())
    if loc is None:
        print(f"{icao} not a WU station")
        return 1
    B = lambda f: _native_reading_int((f - 32.0) * 5.0 / 9.0, "C", sub)
    fc = lambda f: (f - 32.0) * 5.0 / 9.0
    zone = ZoneInfo(tz)
    src = Sources()
    now = dt.datetime.now(zone)
    end = now.date()
    start = end - dt.timedelta(days=args.days)

    by: dict[str, list[tuple[float, float]]] = defaultdict(list)
    cur = start
    while cur <= end:
        ce = min(cur + dt.timedelta(days=30), end)
        try:
            d = src.http.get_json(WU_HISTORY_URL.format(loc=loc),
                {"apiKey": WU_API_KEY, "units": "e",
                 "startDate": cur.strftime("%Y%m%d"), "endDate": ce.strftime("%Y%m%d")})
        except Exception:
            d = {}
        for o in (d.get("observations") or []):
            t, vt = o.get("temp"), o.get("valid_time_gmt")
            if not isinstance(t, (int, float)) or not isinstance(vt, (int, float)):
                continue
            lo = dt.datetime.fromtimestamp(vt, tz=dt.timezone.utc).astimezone(zone)
            by[lo.date().isoformat()].append((lo.hour + lo.minute / 60.0, float(t)))
        cur = ce + dt.timedelta(days=1)

    today = end.isoformat()
    tt = by.get(today, [])
    print(f"WU PATTERN — {name} {icao}, as of {now:%Y-%m-%d %H:%M} {tz}")
    target = None
    if tt:
        rmf = max(f for _, f in tt)
        hh = max(h for h, f in tt if f == rmf)
        noon = max((f for h, f in tt if h <= args.noon), default=rmf)
        target = B(noon)
        print(f"  LIVE today: running max {fc(rmf):.1f}C ({rmf:.0f}F) ~{hh:04.1f}h "
              f"-> bucket {B(rmf)} | running-max-by-noon bucket {target}")
    else:
        print("  LIVE today: no observations yet")

    rows = []
    for dd in sorted(by):
        hrs = by[dd]
        if dd == today or max(h for h, _ in hrs) < 18:    # skip today + incomplete days
            continue
        noonv = [f for h, f in hrs if h <= args.noon]
        if not noonv:
            continue
        rows.append((B(max(noonv)), B(max(f for _, f in hrs)),
                     fc(max(f for _, f in hrs)) - fc(max(noonv))))
    if not rows:
        print("  (insufficient history)")
        return 0
    if target is None:
        target = Counter(r[0] for r in rows).most_common(1)[0][0]

    ana = [r for r in rows if r[0] == target]
    fin = Counter(r[1] for r in ana)
    tot = sum(fin.values()) or 1
    print(f"  ANALOGS — days whose running-max-by-{args.noon}:00 settled {target}C "
          f"(n={len(ana)} of {len(rows)} complete days), where they FINISHED:")
    for b in sorted(fin):
        print(f"    {b}C: {fin[b] / tot * 100:4.0f}%  {'#' * round(fin[b] / tot * 24)}")
    if ana:
        rises = sorted(r[2] for r in ana)
        print(f"    noon->peak rise: median {statistics.median(rises):+.1f}C  "
              f"P90 {rises[min(int(len(rises) * 0.9), len(rises) - 1)]:+.1f}C")

    allb = [r[1] for r in rows]
    n = len(allb)
    h = Counter(allb)
    print("  recent settled (last 10): " + " ".join(str(r[1]) for r in rows[-10:]))
    print(f"  season base rate (n={n}): " +
          " ".join(f"{b}C:{h[b]/n*100:.0f}%" for b in sorted(h)) +
          f"  | mode {statistics.mode(allb)} median {int(statistics.median(allb))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
