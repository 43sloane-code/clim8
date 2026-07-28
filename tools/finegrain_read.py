"""finegrain_read.py — the 00Z / T-group fine-grain settlement read (2026-07-16 specimen).

THE INSTRUMENT that resolved Kalshi KXHIGHTSFO-26JUL16 five hours before settlement:
the NWS CLI settles on the sensor's CONTINUOUS maximum, which lives in two METAR
fields the whole-°F obs record hides —
  * the hourly T-group  (`T02060183` → 20.6°C / dew 18.3°C, tenths precision), and
  * the 6-hourly max group (`1 0206` at 00/06/12/18Z → the max over the WHOLE prior
    6 h window, INCLUDING the minutes between obs — where 07-15's CLI 74-vs-obs-73
    catch lived, and where 07-16's absence of a catch (6h-max == T-group == 69.1°F)
    killed the 70-71 bucket).
Read-only, allowlisted host only (IEM asos.py raw METAR), no served number touched.

USE AT EVERY °F BOUNDARY DAY: obs peak N, bucket edge at N+0.5 — this read answers
"did the invisible minutes touch N+0.5" hours before the CLI publishes. The 00Z ob
(~16:56 PDT for KSFO) carries the 18-00Z group covering the whole afternoon peak.

PATTERN RECOGNITION (--pattern): the operator directive of 2026-07-16 — every full
stack verdict / intraday validation also quotes the HISTORICAL pattern: over the
10y archive, of days whose running max at hour H matched today's, what fraction
ended >= the next whole-°F threshold? (The n=182 43% query that outranked the n=4
offset series and flipped the 07-16 favorite correctly.) Leak-free by construction
(archive days all strictly earlier). SCALE HONESTY (2026-07-27 audit): the archive
is hourly-obs rows only, so the pattern rate is the OBS-CLIMB rate — a FLOOR for
the CLI-catch rate the market pays on, never the thing itself (07-27: obs-climb
0% from 17:00, CLI paid +1 bucket via `10211`). Read the caveat line it prints.

Run:      PYTHONPATH=. python3 tools/finegrain_read.py --station KSFO \
              --date 2026-07-16 --tz America/Los_Angeles [--pattern-hour 14]
Self-test: PYTHONPATH=. python3 tools/finegrain_read.py --selftest
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IEM_ASOS = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
ARCHIVES = {"KSFO": ROOT / "data" / "ksfo_hourly_iem.jsonl"}

_T_GROUP = re.compile(r"\bT([01])(\d{3})[01]\d{3}")
# 6-hourly max group: RMK-section " 1sTTT" (s=0 pos / 1 neg, TTT tenths °C).
# Anchored on a leading space + word boundary so wind ("31015KT"), vis, and SLP
# fields cannot false-match.
_SIX_MAX = re.compile(r"\s1([01])(\d{3})\b")


def parse_t_group(metar: str) -> float | None:
    """Hourly temperature in tenths °C from the T-group, or None."""
    m = _T_GROUP.search(metar)
    if not m:
        return None
    return int(m.group(2)) / 10.0 * (-1 if m.group(1) == "1" else 1)


def parse_six_hour_max(metar: str) -> float | None:
    """6-hourly maximum temperature (tenths °C) from the 1-group, or None."""
    m = _SIX_MAX.search(metar)
    if not m:
        return None
    return int(m.group(2)) / 10.0 * (-1 if m.group(1) == "1" else 1)


def finegrain_day_max(raw_rows: list[tuple[str, str]], date_iso: str):
    """(max_f, max_c, source, ts, n_metars) over one local day's raw METARs.
    raw_rows: (local_ts, metar) pairs. Returns None when no parseable field."""
    best = None
    n = 0
    for ts, metar in raw_rows:
        if not ts.startswith(date_iso):
            continue
        n += 1
        for v, src in ((parse_t_group(metar), "T-grp"),
                       (parse_six_hour_max(metar), "6h-max")):
            if v is None:
                continue
            f = v * 9 / 5 + 32
            if best is None or f > best[0]:
                best = (f, v, src, ts)
    if best is None:
        return None
    return {"max_f": round(best[0], 1), "max_c": best[1], "source": best[2],
            "ts": best[3], "n_metars": n, "cli_whole_f": round(best[0])}


def pattern_rate(station: str, hour: float, runmax_f: float,
                 band_f: float = 0.4, threshold_extra_f: float = 0.4):
    """Historical pattern: of archive days whose running max by `hour` sat within
    ±band_f of `runmax_f`, what fraction climbed >= runmax_f + threshold_extra_f
    ON THE HOURLY-OBS RECORD? Returns (n_match, n_catch) — leak-free (every
    archive day predates today by construction).

    OBS-SCALE ONLY (2026-07-27 audit): the archive is hourly (hour, °C) rows —
    no raw METARs, no 6-hourly groups — so this measures the OBS-CLIMB rate,
    NOT the CLI-catch rate a Kalshi/CLI-settled market pays on. The CLI prints
    at/above the hourly-obs max via the 6-hourly groups, so the paying catch
    rate is >= this number (07-27: obs-climb 0% from 17:00, yet the CLI paid
    +1 bucket via `10211`=21.1°C=69.98°F→70 — the day this mislabel served
    "0%" against the bucket that won). The CLI-scale replacement series is
    registered under ledger/preregistered/sf_cli_scale_intraday_pmf.md."""
    path = ARCHIVES.get(station.upper())
    if path is None or not path.exists():
        return None
    n_match = n_catch = 0
    with open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            obs = _screen_obs(r.get("obs") or [])
            if len(obs) < 20:
                continue
            by_h = [c for h, c in obs if h <= hour]
            if not by_h:
                continue
            rm_f = max(by_h) * 9 / 5 + 32
            if abs(rm_f - runmax_f) <= band_f:
                n_match += 1
                if max(c for _, c in obs) * 9 / 5 + 32 >= runmax_f + threshold_extra_f:
                    n_catch += 1
    return n_match, n_catch


def _cli_seam_note(station: str) -> str:
    """The measured CLI-vs-obs/WU seam for the station's ledger series, or an
    honest 'not yet logged' (same ledger run._load_cli_seam reads — one source
    of truth for the number, so the tool and the verdict cannot drift). Rows are
    screened with the same ±8°F out-of-band rule as run._clean_divergences."""
    path = ROOT / "ledger" / f"{station.lower()}_cli_wu.jsonl"
    try:
        divs = [json.loads(l)["divergence"] for l in
                path.read_text().splitlines() if l.strip()]
        divs = [x for x in divs if isinstance(x, (int, float))
                and not isinstance(x, bool) and abs(x) <= 8.0][-30:]
        if divs:
            import statistics
            return (f"seam CLI − obs/WU {statistics.mean(divs):+.2f}F mean "
                    f"(n={len(divs)})")
    except Exception:
        pass
    return "seam not yet logged"


def _screen_obs(obs: list, lo_c: float = -30.0, hi_c: float = 55.0) -> list:
    """Drop malformed / out-of-band (hour, °C) archive rows before any statistic
    touches them (same hygiene class as the verdict's QC screen: KSFO cannot
    physically approach either bound; a row past it is a parse artifact)."""
    return [(h, c) for h, c in obs
            if isinstance(c, (int, float)) and not isinstance(c, bool)
            and lo_c <= c <= hi_c]


def _pattern_lines(station: str, hour: float, runmax_f: float,
                   pr: tuple[int, int]) -> list[str]:
    """The pattern read-out, honestly labeled (2026-07-27 audit): the archive is
    hourly-obs only, so the rate is the OBS-CLIMB rate — a floor for the paying
    CLI-catch rate, never the thing itself. LABELING ONLY: the numbers
    (n_match, n_catch) are unchanged; the words now say what they measure."""
    n, k = pr
    return [
        f"pattern: {n} archive days had running max ~{runmax_f:.0f}F "
        f"by {hour:.0f}:00; {k} ({k / max(1, n) * 100:.0f}%) climbed "
        f">= {runmax_f + 0.4:.1f}F ON THE HOURLY-OBS RECORD (obs-climb rate — "
        f"NOT the CLI settle)",
        f"pattern-scale caveat: the CLI prints at/above the obs max via the "
        f"6-hourly groups ({_cli_seam_note(station)}), so the PAYING CLI-catch "
        f"rate is >= this — 07-27: obs-climb 0% from 17:00, CLI paid +1 bucket "
        f"via 10211=21.1C=69.98F->70. CLI-scale series pending "
        f"sf_cli_scale_intraday_pmf.md.",
    ]


def fetch_day_metars(station: str, date_iso: str, tz: str) -> list[tuple[str, str]]:
    from weather_council.security import SafeHTTPClient
    d = dt.date.fromisoformat(date_iso)
    c = SafeHTTPClient()
    txt = c.get_text(IEM_ASOS, {
        "station": station, "data": "metar",
        "year1": d.year, "month1": d.month, "day1": d.day,
        "year2": d.year, "month2": d.month, "day2": d.day + 1 if d.day < 28 else d.day,
        "tz": tz, "format": "onlycomma",
        "missing": "empty", "trace": "empty", "report_type": [3, 4]})
    out = []
    for line in txt.splitlines()[1:]:
        p = line.split(",")
        if len(p) >= 3:
            out.append((p[1].strip(), ",".join(p[2:])))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--station", default="KSFO")
    ap.add_argument("--date", default=None, help="local date (default: station today)")
    ap.add_argument("--tz", default="America/Los_Angeles")
    ap.add_argument("--pattern-hour", type=float, default=None,
                    help="also quote the historical catch-rate conditioned at this hour")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()

    from zoneinfo import ZoneInfo
    date_iso = args.date or dt.datetime.now(ZoneInfo(args.tz)).date().isoformat()
    rows = fetch_day_metars(args.station, date_iso, args.tz)
    res = finegrain_day_max(rows, date_iso)
    if res is None:
        print(f"{args.station} {date_iso}: no parseable T-group / 6h-max yet")
        return 2
    print(f"{args.station} {date_iso}: fine-grain max {res['max_c']:.1f}C = "
          f"{res['max_f']:.1f}F ({res['source']} at {res['ts']}, {res['n_metars']} METARs) "
          f"-> CLI whole-F: {res['cli_whole_f']}")
    if args.pattern_hour is not None:
        pr = pattern_rate(args.station, args.pattern_hour, res["max_f"])
        if pr:
            for line in _pattern_lines(args.station, args.pattern_hour,
                                       res["max_f"], pr):
                print(line)
    return 0


def _selftest() -> int:
    # T-group parse: sign, tenths, no false hit without the group
    assert parse_t_group("KSFO 162056Z ... RMK AO2 SLP121 T02060183") == 20.6
    assert parse_t_group("RMK T10061011") == -0.6              # negative temps
    assert parse_t_group("KSFO 162056Z 31015KT 10SM FEW008") is None
    # 6h-max group: RMK " 1sTTT"; wind/vis/SLP must NOT false-match
    assert parse_six_hour_max("RMK AO2 SLP121 10206 20111") == 20.6
    assert parse_six_hour_max("RMK AO2 11017 21006") == -1.7    # negative max
    assert parse_six_hour_max("31015KT 10SM SLP121 T02060183") is None
    # day max: 6h-max beats a lower T-group; ties keep first (T-grp)
    rows = [("2026-07-16 12:56", "RMK T02060183"),
            ("2026-07-16 16:56", "RMK 10206 T01890178"),
            ("2026-07-17 00:56", "RMK T09990999")]              # next day ignored
    r = finegrain_day_max(rows, "2026-07-16")
    assert r["max_c"] == 20.6 and r["cli_whole_f"] == 69 and r["n_metars"] == 2, r
    # 69.1F rounds to CLI 69; 69.6F would round to 70 (the frozen boundary rule)
    assert round(20.6 * 9 / 5 + 32) == 69 and round(20.9 * 9 / 5 + 32) == 70
    print("finegrain_read selftest PASS (T-group signs, 6h-max anchoring, day-max, "
          "CLI rounding boundary)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
