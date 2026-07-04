"""twc_forecast_logger.py — forward-log The Weather Company's OWN daily-high forecast.

The market settles on the Wunderground / Weather Company (TWC) airport record, and TWC
publishes its OWN forecast for that same station (api.weather.com v3 — the same allowlisted
host + key as the observation feed). That forecast is a candidate 9TH COUNCIL MEMBER, distinct
from the 8 Open-Meteo NWP grid members: it is the settlement ORACLE forecasting its own station,
natively — so it may capture the station/microclimate/measurement-convention effects the council's
bias correction only approximates.

But it has NO historical-forecast archive (the endpoint serves only the current forecast), so it
is NOT backtestable today — exactly the [D09] situation. An un-backtestable member cannot clear the
gate, so it is NOT asserted into the blend. Instead this logs TWC's day-ahead (lead-1) forecast each
day via `storage.log_tracked_forecast(source="twc", ...)` — the ledger built precisely to "let a
forecaster with no backtestable archive earn a measured record before a human ever considers
promoting it." Recommend-only: it never feeds a verdict, a band, or a trade. After ~40+ settled
pairs it can be run through the same disjoint-fold gate as everything else; ship as a member only
if TWC-added-to-the-council beats the council alone out-of-sample. Prior is unfavorable (forecast
members are 0/6: AIFS/UKMO-2km/ICON-D2/ICON-EU/AROME), but TWC's settlement-alignment is the one
mechanism none of those had.

Run daily:  PYTHONPATH=. python3 tools/twc_forecast_logger.py
Self-test:  PYTHONPATH=. python3 tools/twc_forecast_logger.py --selftest
"""
from __future__ import annotations

import argparse
import datetime as dt
from zoneinfo import ZoneInfo

from weather_council.market import _native_reading_int
from weather_council.sources import Place, Sources, WU_API_KEY
from weather_council import storage

TWC_URL = "https://api.weather.com/v3/wx/forecast/daily/5day"
GATE_MIN_PAIRS = 40                      # minimum settled pairs before the disjoint-fold gate can rule

# The current WU-anchored basket (name, country -> settlement station geocode + tz). Names/countries
# are chosen so Place.label() matches the council's own label, so the eventual TWC-vs-council join is exact.
_STATIONS = {
    ("Singapore", "Singapore"): ("WSSS", "Changi", 1.3502, 103.994, "Asia/Singapore"),
    ("Manila", "Philippines"): ("RPLL", "Ninoy Aquino Intl", 14.5086, 121.0198, "Asia/Manila"),
    ("London", "United Kingdom"): ("EGLC", "London City", 51.5053, 0.0553, "Europe/London"),
}


def _pick(valid, highs, lows, target_iso):
    """PURE: (high_F, low_F) for `target_iso` from the parallel TWC arrays, or None. KAT'd."""
    for v, h, l in zip(valid or [], highs or [], lows or []):
        if isinstance(v, str) and v[:10] == target_iso and isinstance(h, (int, float)):
            return float(h), (float(l) if isinstance(l, (int, float)) else None)
    return None


def _f_to_c(f):
    return (f - 32.0) * 5.0 / 9.0


def fetch_twc(src: Sources, lat: float, lon: float):
    """TWC daily forecast at a station geocode (whole-°F, matching the settlement grain)."""
    d = src.http.get_json(TWC_URL, {"geocode": f"{lat},{lon}", "format": "json",
                                    "units": "e", "language": "en-US", "apiKey": WU_API_KEY})
    return (d.get("validTimeLocal"), d.get("calendarDayTemperatureMax"),
            d.get("calendarDayTemperatureMin"))


def _print_record():
    conn = storage._connect()
    try:
        rows = conn.execute(
            "SELECT place, target_date, fc_high, actual_high FROM tracked_forecasts "
            "WHERE source='twc' ORDER BY target_date").fetchall()
    finally:
        conn.close()
    settled = [r for r in rows if r[3] is not None]
    print(f"  RECORD: {len(rows)} TWC forecasts logged, {len(settled)} settled against WU")
    hits = 0
    for p, td, fh, ah in settled[-8:]:
        fb, ab = _native_reading_int(fh, "C", False), _native_reading_int(ah, "C", False)
        hit = fb == ab
        hits += hit
        print(f"    {p[:22]:22} {td}: TWC {fb}°C  vs settled {ab}°C  {'HIT' if hit else 'miss'}")
    if len(settled) >= GATE_MIN_PAIRS:
        print(f"  -> {len(settled)} settled pairs: ENOUGH to run the disjoint-fold gate "
              f"(TWC-added-to-council vs council alone). Gate it before promoting.")
    else:
        print(f"  -> {GATE_MIN_PAIRS - len(settled)} more settled pairs needed before the gate can "
              f"rule (recommend-only until then; never asserted into the blend).")


def _selftest() -> int:
    valid = ["2026-07-01T07:00:00+0800", "2026-07-02T07:00:00+0800"]
    highs, lows = [90, 86], [78, 79]
    assert _pick(valid, highs, lows, "2026-07-02") == (86.0, 79.0)        # date-aligned pick
    assert _pick(valid, highs, lows, "2026-07-01") == (90.0, 78.0)
    assert _pick(valid, highs, lows, "2026-07-09") is None               # missing date -> None
    assert _pick(valid, [None, 86], lows, "2026-07-01") is None          # null high -> None
    assert abs(_f_to_c(86) - 30.0) < 1e-9 and _native_reading_int(_f_to_c(86), "C", False) == 30
    assert _native_reading_int(_f_to_c(90), "C", False) == 32            # 90F=32.2C -> bucket 32
    # Place.label() must match the council's join key
    assert Place("Singapore", "Singapore", 1.35, 103.99, "Asia/Singapore").label() == "Singapore, Singapore"
    print("twc_forecast_logger selftest PASS (date-aligned pick, null guards, °F→°C bucket, label join-key)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lead", type=int, default=1, help="days ahead to log (1 = tomorrow, the day-ahead member comparison)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()

    src = Sources()
    print("TWC FORECAST FORWARD-LOG  (source='twc' → tracked_forecasts; recommend-only, earning a record)")
    for (name, country), (icao, sname, lat, lon, tz) in _STATIONS.items():
        target = (dt.datetime.now(ZoneInfo(tz)).date() + dt.timedelta(days=args.lead)).isoformat()
        try:
            pick = _pick(*fetch_twc(src, lat, lon), target)
        except Exception as e:
            print(f"  {name}: TWC fetch failed ({type(e).__name__}: {str(e)[:60]})")
            continue
        if not pick:
            print(f"  {name}: no TWC forecast for {target} yet")
            continue
        hF, lF = pick
        hC, lC = _f_to_c(hF), (_f_to_c(lF) if lF is not None else None)
        place = Place(name=name, country=country, latitude=lat, longitude=lon, timezone=tz)
        ts = {"kind": "station", "station": {"icao": icao, "name": sname, "id": None}}
        # Pair the row with the council's own forecast for the same target AT CAPTURE TIME
        # (latest verdicts row), so the eventual 40-pair head-to-head is point-in-time
        # matched instead of joined after the fact. Best-effort: None if no verdict yet.
        c_high = c_low = None
        try:
            conn = storage._connect()
            row = conn.execute(
                "SELECT high, low FROM verdicts WHERE place=? AND target_date=? "
                "ORDER BY issued_at DESC LIMIT 1", (place.label(), target)).fetchone()
            conn.close()
            if row:
                c_high, c_low = row
        except Exception:
            pass
        storage.log_tracked_forecast("twc", place, target, hC, lC, c_high, c_low, ts)
        print(f"  {name}: logged TWC lead-{args.lead} {target}  high {hF:.0f}°F={hC:.1f}°C "
              f"(bucket {_native_reading_int(hC, 'C', False)})")
    settled = storage.settle_tracked_forecasts(src)
    print(f"  settled {len(settled)} matured entr{'y' if len(settled) == 1 else 'ies'} against the WU record")
    _print_record()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
