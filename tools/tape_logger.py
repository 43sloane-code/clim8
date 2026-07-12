#!/usr/bin/env python3
"""tape_logger.py — light intraday tape reader for the WU-register basket cities.

WHY THIS EXISTS (2026-07-12 coverage audit): the intraday tape/grade engine
(weather_council/intraday_tape.py + intraday_grade.py) is only as good as its READ
SEQUENCE, and the daily automation feeds it lopsidedly — the four verdict jobs
(tools/daily_verdict.py) are SINGAPORE-ONLY, and accumulate.py runs the other cities at
--lead 1 (day-ahead: no ceiling, no tape row). Host-local hours 02:00–11:15 are all
PRE-PEAK in London, so London — a served basket city — would accrue zero endpoint-motion
evidence and its mechanical lock could never fire before post-sunset.

This tool is the gap-filler: for each WU-register basket city it computes ONLY the
intraday ceiling and its grade (via run._grade_for, which appends the tape row), then
prints the honest grade lines. No council, no market, no served numbers, no report files
— a fraction of a full verdict run's network budget. Idempotence is not required: more
same-day rows = more endpoint-motion evidence (the tape derivations read the sequence).

Manila is DELIBERATELY excluded: RPLL is not in intraday_ceiling._LIVE_REGISTER (no v3
register consult), so its ceiling carries no endpoint/cur_f and a tape row would be an
empty no-op. Adding Manila to the register consult would change its served running max —
Manila improvement is OUT OF SCOPE by user directive (2026-07-04). Read-only otherwise.

Run:        PYTHONPATH=. python3 tools/tape_logger.py
Self-test:  PYTHONPATH=. python3 tools/tape_logger.py --selftest
Scheduled:  tools/com.weatherverdict.tape.plist (15:30 + 21:45 host/London-local —
            London's peak window and post-sunset settle-grade read; the same firings
            give Singapore harmless extra evening/pre-dawn rows).
"""
from __future__ import annotations

import argparse
import sys

from weather_council.sources import Place, Sources, place_today
from weather_council.intraday_ceiling import (_HOURLY_STATION, _LIVE_REGISTER,
                                              intraday_ceiling)
from weather_council.intraday_grade import grade_lines

# Settlement-anchor coordinates (same convention as twc_forecast_logger._STATIONS:
# the STATION's lat/lon, not the city centroid — _grade_for's sunset gate reads these).
CITIES = [
    Place(name="Singapore", country="Singapore",
          latitude=1.3502, longitude=103.994, timezone="Asia/Singapore"),
    Place(name="London", country="United Kingdom",
          latitude=51.5053, longitude=0.0553, timezone="Europe/London"),
]


def log_once(sources: Sources | None = None) -> int:
    """One tape read per configured city. Returns the number of rows graded (a city
    with no sharpened ceiling logs nothing and says so — never a fabricated row)."""
    from run import _grade_for          # deferred: run.py is import-safe (KAT'd) but heavy
    src = sources if sources is not None else Sources()
    graded = 0
    for place in CITIES:
        target = place_today(place)
        try:
            ceiling = intraday_ceiling(place, target, sources=src)
            grade = _grade_for(place, target, ceiling)
        except Exception as exc:                     # one city failing must not starve the next
            print(f"{place.name} {target}: errored — no tape row ({exc})")
            continue
        if grade is None:
            why = getattr(ceiling, "note", None) or getattr(ceiling, "kind", "?")
            print(f"{place.name} {target}: no sharpened ceiling ({why}) — no grade")
            continue
        graded += 1
        print(f"{place.name} {target}:")
        for line in grade_lines(grade, source=getattr(ceiling, "source", None)):
            print(line)
    return graded


def _self_test() -> None:
    # Every configured city must be tape-capable: an hourly archive for the ceiling AND
    # the v3 register consult (otherwise rows are empty no-ops and the job is theatre).
    for place in CITIES:
        key = place.name.strip().lower()
        assert key in _HOURLY_STATION, f"{key}: no hourly station — cannot build a ceiling"
        assert key in _LIVE_REGISTER, f"{key}: no live-register consult — tape rows would be empty"
    # Manila stays excluded until its register consult is in scope (user directive).
    assert all(p.name.lower() != "manila" for p in CITIES)
    # _grade_for degrades to None on a non-sharpened ceiling without touching the network
    # or the tape — the "no fabricated row" contract this tool relies on.
    import datetime as dt
    from run import _grade_for
    from weather_council.intraday_ceiling import IntradayCeiling
    dummy = IntradayCeiling(kind="unavailable", city="London", target="2026-07-12",
                            sub_degree=False, note="test")
    assert _grade_for(CITIES[1], dt.date(2026, 7, 12), dummy) is None
    assert _grade_for(CITIES[1], dt.date(2026, 7, 12), None) is None
    print("tape_logger self-test PASSED — both cities hourly+register capable; Manila "
          "excluded by directive; non-sharpened ceilings produce no row and no grade.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        _self_test()
        return 0
    log_once()
    # Lock-certification rows at the SAME firings (2026-07-12, london_lock_instrumentation
    # §1 wiring): this job's 15:30 London-local run is the ONLY scheduled runner inside
    # London's certification hours (13–18 local) — daily_verdict fires at 02:15–11:15 and
    # accumulate at 12:00/20:00 London. lock_logger logs every configured city and is
    # idempotent per (city, date, hour), so the extra Singapore row is harmless redundancy.
    try:
        from tools import lock_logger
        lock_logger.main()
    except Exception as exc:
        print(f"lock_logger step failed (non-fatal): {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
