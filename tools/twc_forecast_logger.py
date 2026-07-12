"""twc_forecast_logger.py — forward-log The Weather Company's OWN daily-high forecast.

The market settles on the Wunderground airport OBSERVATION record — the station's own
METAR/ASOS readings, which TWC redistributes; TWC does NOT produce the observations, and
"the settlement oracle forecasting its own station" is the WRONG framing (operator-corrected
2026-07-12). TWC also publishes a forecast for that station (api.weather.com v3 — same
allowlisted host + key). That forecast is a candidate 9TH COUNCIL MEMBER, distinct from the
8 Open-Meteo NWP grid members, with one honest, modest driver: it is plausibly
verified/calibrated against the SAME redistributed record the market settles on (same
station, whole-°F convention, aggregation quirks), so its errors are measured in the
settlement's own metric — which the council's bias correction only approximates. Driver
clauses, kill conditions, and the frozen gate: ledger/preregistered/twc_member_gate.md.

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
from weather_council.sources import Place, Sources
from weather_council import storage

GATE_MIN_PAIRS = 40                      # minimum settled pairs before the disjoint-fold gate can rule

# The current WU-anchored basket (name, country -> settlement station geocode + tz). Names/countries
# are chosen so Place.label() matches the council's own label, so the eventual TWC-vs-council join is exact.
_STATIONS = {
    ("Singapore", "Singapore"): ("WSSS", "Changi", 1.3502, 103.994, "Asia/Singapore"),
    ("Manila", "Philippines"): ("RPLL", "Ninoy Aquino Intl", 14.5086, 121.0198, "Asia/Manila"),
    ("London", "United Kingdom"): ("EGLC", "London City", 51.5053, 0.0553, "Europe/London"),
}


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
    import datetime as _dt
    # The fetch/day-mapping/conversion contract now lives in Sources.twc_forecast_daily (KAT
    # tests/test_sources_twc.py). Here we verify the logger's OWN glue: the date-aligned pick +
    # °C-bucket handoff through the shared method, and the Place.label() join key.
    class _FakeHTTP:
        payload = {"validTimeLocal": ["2026-07-01T07:00:00+0800", "2026-07-02T07:00:00+0800"],
                   "calendarDayTemperatureMax": [90, 86], "calendarDayTemperatureMin": [78, 79]}
        def get_json(self, url, params): return self.payload
    src = Sources(); src.http = _FakeHTTP()
    pick = src.twc_forecast_daily(1.35, 103.99, _dt.date(2026, 7, 2), "Asia/Singapore", "C")
    assert abs(pick["fc_high"] - 30.0) < 1e-9 and _native_reading_int(pick["fc_high"], "C", False) == 30
    pick0 = src.twc_forecast_daily(1.35, 103.99, _dt.date(2026, 7, 1), "Asia/Singapore", "C")
    assert _native_reading_int(pick0["fc_high"], "C", False) == 32       # 90°F=32.2°C -> bucket 32
    assert src.twc_forecast_daily(1.35, 103.99, _dt.date(2026, 7, 9), "Asia/Singapore", "C") is None
    # Place.label() must match the council's join key
    assert Place("Singapore", "Singapore", 1.35, 103.99, "Asia/Singapore").label() == "Singapore, Singapore"
    print("twc_forecast_logger selftest PASS (shared twc_forecast_daily handoff, °C bucket, label join-key)")
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
        target_date = dt.datetime.now(ZoneInfo(tz)).date() + dt.timedelta(days=args.lead)
        target = target_date.isoformat()
        # Fetch through the shared, soft-failure-isolated cross-reference surface (Plan 4 Phase 1):
        # units='e' at the settlement anchor, °C at the edge, day matched on validTimeLocal[:10].
        pick = src.twc_forecast_daily(lat, lon, target_date, tz, "C")
        if not pick or pick.get("fc_high") is None or pick.get("fc_low") is None:
            # tracked_forecasts requires BOTH fc_high and fc_low (NOT NULL). Skip EXPLICITLY when
            # either is missing rather than letting INSERT OR IGNORE drop the row silently — a
            # silent drop would be an invisible accrual gap. Never guesses the missing value.
            print(f"  {name}: no complete TWC forecast for {target} yet")
            continue
        hC, lC = pick["fc_high"], pick["fc_low"]
        place = Place(name=name, country=country, latitude=lat, longitude=lon, timezone=tz)
        ts = {"kind": "station", "station": {"icao": icao, "name": sname, "id": None}}
        # Pair the row with the council's own forecast for the same target AT CAPTURE TIME
        # (latest verdicts row), so the eventual 40-pair head-to-head is point-in-time
        # matched instead of joined after the fact. Best-effort: None if no verdict yet.
        c_high = c_low = None
        try:
            import contextlib
            with contextlib.closing(storage._connect()) as conn:   # close even if execute raises
                row = conn.execute(
                    "SELECT high, low FROM verdicts WHERE place=? AND target_date=? "
                    "ORDER BY issued_at DESC LIMIT 1", (place.label(), target)).fetchone()
            if row:
                c_high, c_low = row
        except Exception:
            pass
        storage.log_tracked_forecast("twc", place, target, hC, lC, c_high, c_low, ts)
        print(f"  {name}: logged TWC lead-{args.lead} {target}  high {hC:.1f}°C "
              f"(bucket {_native_reading_int(hC, 'C', False)})")
    settled = storage.settle_tracked_forecasts(src)
    print(f"  settled {len(settled)} matured entr{'y' if len(settled) == 1 else 'ies'} against the WU record")
    _print_record()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
