"""Prospectively accumulate the live HKO reading into an hourly archive.

WHY. Hong Kong intraday bucket conviction is the one open frontier: London's
intraday-ceiling lever (weather_council/intraday_ceiling.py) lifts exact-bucket
hit to ~89% by 15:00 / ~99% by 18:00 because London City Airport EGLC — the
SETTLEMENT instrument — has an hourly METAR archive to learn the remaining-rise
from. Hong Kong settles on the HKO Observatory, which publishes a DAILY maximum
only; proxies that aren't the settlement instrument all fail the gate (ERA5
hourly ~32%, VHHH airport ~38% — both ≈ day-ahead, see reports/_hk_*_premise.py).

The only path is to build the missing record ourselves: log the live HKO 1-min
reading (the actual settlement gauge, via Sources.hko_current) at hourly cadence.
After ~6 weeks this CSV holds enough (date, hour, temp) to learn HK's own
remaining-rise distribution and feed an HK arm of intraday_ceiling — validatable
against the HKO daily settlement exactly as London is today.

This is READ-ONLY w.r.t. forecasting: it writes only to its own data ledger, never
a verdict, vote, weight, or trade. Idempotent: re-running within the same local
hour updates that hour's running max rather than duplicating it.

USAGE (schedule hourly yourself — this tool does NOT install a scheduler):
    PYTHONPATH=. python3 tools/hko_intraday_accumulate.py
    # cron:    0 * * * *  cd <repo> && PYTHONPATH=. python3 tools/hko_intraday_accumulate.py
"""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from weather_council.sources import Sources

LEDGER = Path(__file__).resolve().parent.parent / "reports" / "hko_intraday.csv"
_FIELDS = ("date", "hour", "temp_c", "record_time", "logged_at")


def _load(ledger: Path) -> dict[tuple[str, int], dict]:
    """Existing rows keyed by (local date, local hour)."""
    rows: dict[tuple[str, int], dict] = {}
    if ledger.exists():
        with ledger.open(newline="") as fh:
            for r in csv.DictReader(fh):
                try:
                    rows[(r["date"], int(r["hour"]))] = r
                except (KeyError, ValueError):
                    continue
    return rows


def _write(ledger: Path, rows: dict[tuple[str, int], dict]) -> None:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows.values(), key=lambda r: (r["date"], int(r["hour"])))
    with ledger.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_FIELDS)
        w.writeheader()
        w.writerows(ordered)


def accumulate(sources: Sources | None = None, ledger: Path = LEDGER) -> str | None:
    """Log one live HKO reading into the hourly ledger, keeping the MAX temperature
    seen within each local hour (so the per-hour running max — what the remaining-
    rise model needs — is preserved). Returns a human line, or None when the feed
    gave no usable reading (logged nothing; never raises on a feed gap)."""
    live = (sources or Sources()).hko_current()
    if not live or live.get("temperature_2m") is None:
        return None
    temp = float(live["temperature_2m"])
    rt = str(live.get("record_time") or "")
    # Prefer the reading's own timestamp; fall back to now if the feed omits it.
    if len(rt) >= 13 and rt[10] in "T " and rt[11:13].isdigit():
        date, hour = rt[:10], int(rt[11:13])
    else:
        now = dt.datetime.now()
        date, hour = now.date().isoformat(), now.hour

    rows = _load(ledger)
    key = (date, hour)
    prev = rows.get(key)
    if prev is not None and float(prev["temp_c"]) >= temp:
        return None                       # this hour already has an equal/higher reading
    rows[key] = {
        "date": date, "hour": hour, "temp_c": f"{temp:.1f}",
        "record_time": rt, "logged_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    _write(ledger, rows)
    verb = "updated" if prev is not None else "logged"
    return f"{verb} HKO {date} {hour:02d}:00 -> {temp:.1f}°C ({len(rows)} hours in ledger)"


def main() -> int:
    line = accumulate()
    print(line if line else "no usable HKO reading this run (nothing logged)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
