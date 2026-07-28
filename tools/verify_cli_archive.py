#!/usr/bin/env python3
"""verify_cli_archive.py — S2 rule-2 verification of the IEM parsed-CLI archive.

kalshi_sf_seam.md rule 2: the IEM parsed-CLI archive (sources.nws_cli_daily —
the backtest/probe truth the sf_cli_scale_intraday_pmf.md probe will score
against) must be VERIFIED against first-party CLISFO product text on ≥30 recent
days BEFORE adoption; on failure, fall back to direct capture forward-only.

This tool pairs the two sources day-by-day and renders the verdict:
  * IEM half:    sources.nws_cli_daily (mesonet.agron.iastate.edu — allowlisted).
  * Direct half: api.weather.gov /products/types/CLI (first-party NWS text —
                 allowlisted), filtered to the station's issuing office, the
                 MAXIMUM line + climate-summary date parsed out of the text.

Deliberately split so the logic is testable offline:
  * parse_cli_product(text)  — (climate_date_iso, max_f) from CLISFO text. PURE.
  * compare_cli_series(...)  — the ADOPT/REJECT/INSUFFICIENT verdict. PURE, with
                               a known-answer selftest. The ≥30-day bar is NOT
                               relaxed: if the products window covers fewer days,
                               the verdict is INSUFFICIENT (accrue, rerun) —
                               never fabricate the missing days.
  * fetch halves             — REQUIRES-LIVE, behind SafeHTTPClient.

Stdlib only. Run:      PYTHONPATH=. python3 tools/verify_cli_archive.py [--station KSFO --days 40]
Self-test: PYTHONPATH=. python3 tools/verify_cli_archive.py --selftest
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

# The WFO whose CLI products settle the station's contract (kalshi_sf_seam.md:
# KSFO -> MTR "San Francisco Bay Area", issuedby=SFO) + the header that selects
# THIS station's product out of the office's multi-station CLI stream.
_STATION_OFFICE = {
    "KSFO": ("KMTR", re.compile(r"SAN FRANCISCO AIRPORT CLIMATE SUMMARY")),
}
# Per-run fetch cap for product texts (SafeHTTPClient budget is 64/run; the IEM
# half costs 1) and the accrual ledger that makes ≥30-day coverage reachable
# across runs (the /products window only spans recent days — accrue, never
# refetch, exactly like the repo's other ledgers).
_MAX_PRODUCT_FETCHES = 48
_ACCRUAL_TARGET_DAYS = 35
_DIRECT_LEDGER = {"KSFO": Path(__file__).resolve().parent.parent
                  / "ledger" / "ksfo_cli_direct.jsonl"}

# "...THE SAN FRANCISCO AIRPORT CLIMATE SUMMARY FOR JULY 27 2026..." — the date
# the report is FOR (it issues early the NEXT morning local).
_RE_FOR_DATE = re.compile(r"CLIMATE SUMMARY FOR\s+([A-Z]+)\s+(\d{1,2}),?\s+(\d{4})")
# The MAXIMUM line: " MAXIMUM         69   4:04 PM ..." (value can be "M" = missing).
_RE_MAXIMUM = re.compile(r"^\s*MAXIMUM\s+(\d+|M)\b", re.M)
_MONTHS = {m: i for i, m in enumerate(
    ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST",
     "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"], start=1)}


def parse_cli_product(text: str) -> tuple[str, float | None] | None:
    """(climate_date_iso, max_f) from a first-party CLI product's text, or None
    when unparseable. max_f is None for the "M" (missing) sentinel — surfaced,
    never fabricated (the kalshi_logger "M" bug class)."""
    md = _RE_FOR_DATE.search(text)
    if not md:
        return None
    month = _MONTHS.get(md.group(1))
    if month is None:
        return None
    date_iso = dt.date(int(md.group(3)), month, int(md.group(2))).isoformat()
    mm = _RE_MAXIMUM.search(text)
    if not mm:
        return None
    return date_iso, (None if mm.group(1) == "M" else float(mm.group(1)))


def compare_cli_series(pairs: list[tuple[str, float | None, float | None]],
                       min_days: int = 30, exact_bar: float = 0.90,
                       tol_f: float = 1.0) -> dict:
    """The adoption verdict for the IEM parsed-CLI archive.

    pairs: (date, iem_high_f, direct_high_f) — None on either side = missing.
    Gate (kalshi_sf_seam.md rule 2): >= min_days COMPARABLE days (both sides
    present), exact match on >= exact_bar of them, ALL within tol_f.
    INSUFFICIENT below the bar on comparable days — the honest answer when the
    first-party window is short; never relax the bar to force a verdict."""
    comparable = [(d, a, b) for d, a, b in pairs
                  if a is not None and b is not None]
    n = len(comparable)
    diffs = [abs(a - b) for _, a, b in comparable]
    exact = sum(1 for x in diffs if x == 0)
    out = {"n_pairs": len(pairs), "n_comparable": n,
           "n_exact": exact,
           "exact_rate": round(exact / n, 3) if n else 0.0,
           "max_abs_diff_f": max(diffs) if diffs else None,
           "min_days": min_days, "exact_bar": exact_bar, "tol_f": tol_f}
    if n < min_days:
        out["verdict"] = "INSUFFICIENT"
        out["reason"] = (f"only {n} comparable days (< {min_days}) — accrue and "
                         f"rerun; the bar is not relaxed")
    elif exact / n < exact_bar:
        out["verdict"] = "REJECT"
        out["reason"] = (f"exact-rate {exact / n:.1%} < {exact_bar:.0%} bar — "
                         f"fall back to direct capture forward-only")
    elif max(diffs) > tol_f:
        out["verdict"] = "REJECT"
        out["reason"] = f"max |diff| {max(diffs):.1f}°F exceeds ±{tol_f:.1f}°F"
    else:
        out["verdict"] = "ADOPT"
        out["reason"] = (f"{n} days, {exact / n:.1%} exact, max |diff| "
                         f"{max(diffs):.1f}°F — archive verified for probe use")
    return out


def fetch_iem_half(station: str, start: dt.date, end: dt.date) -> dict:
    from weather_council.sources import Sources
    return Sources().nws_cli_daily(station, start, end)


def _load_direct_ledger(station: str) -> dict[str, float | None]:
    path = _DIRECT_LEDGER.get(station.upper())
    out: dict[str, float | None] = {}
    if path is None or not path.exists():
        return out
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            out[r["date"]] = r.get("max_f")
        except (ValueError, KeyError):
            continue
    return out


def _save_direct_ledger(station: str, series: dict[str, float | None]) -> None:
    path = _DIRECT_LEDGER[station.upper()]
    with open(path, "w") as f:
        for d in sorted(series):
            f.write(json.dumps({"date": d, "max_f": series[d]}) + "\n")


def fetch_direct_half(station: str,
                      max_fetches: int = _MAX_PRODUCT_FETCHES) -> tuple[dict, int]:
    """({climate_date_iso -> max_f}, n_new) from first-party CLI product text,
    accrued into ledger/<station>_cli_direct.jsonl across runs. The /products
    endpoint serves a RECENT nationwide window and the office stream mixes
    stations, so one run covers < 30 days — the accrual ledger is how the bar
    is reached honestly (accrue, never refetch old dates)."""
    from weather_council.security import SafeHTTPClient
    office, header_re = _STATION_OFFICE[station.upper()]
    accr = _load_direct_ledger(station)
    c = SafeHTTPClient()
    idx = c.get_json("https://api.weather.gov/products/types/CLI", {})
    entries = [p for p in idx.get("@graph", []) or []
               if p.get("issuingOffice") == office]
    entries.sort(key=lambda p: p.get("issuanceTime", ""), reverse=True)
    n_new = 0
    for p in entries[:max_fetches]:
        full = c.get_json(f"https://api.weather.gov/products/{p['id']}", {})
        text = full.get("productText", "") or ""
        if not header_re.search(text):
            continue                      # another station in the office's stream
        parsed = parse_cli_product(text)
        if parsed is not None and parsed[0] not in accr:
            accr[parsed[0]] = parsed[1]
            n_new += 1
        if len(accr) >= _ACCRUAL_TARGET_DAYS:
            break
    _save_direct_ledger(station, accr)
    return accr, n_new


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify the IEM parsed-CLI archive "
                                     "against first-party CLISFO text (S2 rule 2).")
    ap.add_argument("--station", default="KSFO")
    ap.add_argument("--days", type=int, default=40)
    ap.add_argument("--min-days", type=int, default=30)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    station = args.station.upper()
    if station not in _STATION_OFFICE:
        print(f"{station}: no pinned issuing office (have: "
              f"{', '.join(_STATION_OFFICE)})")
        return 1
    end = dt.date.today() - dt.timedelta(days=1)   # CLI for today not yet issued
    start = end - dt.timedelta(days=args.days - 1)
    iem = fetch_iem_half(station, start, end)
    direct, n_new = fetch_direct_half(station)
    dates = sorted(set(iem) | set(direct))
    pairs = [(d, (iem.get(d) or {}).get("high_f"), direct.get(d)) for d in dates]
    res = compare_cli_series(pairs, min_days=args.min_days)
    print(f"{station} CLI archive verification (S2 rule 2), {start}..{end}")
    print(f"  direct capture  : {len(direct)} accrued day(s) in "
          f"ledger/{station.lower()}_cli_direct.jsonl (+{n_new} this run) — "
          f"the products window is recent-only; accrue by rerunning daily")
    print(f"  comparable days : {res['n_comparable']} of {res['n_pairs']} paired "
          f"(IEM parsed-CLI vs first-party product text)")
    print(f"  exact matches   : {res['n_exact']} ({res['exact_rate']:.1%}, "
          f"bar {res['exact_bar']:.0%})")
    print(f"  max |diff|      : {res['max_abs_diff_f']}°F (tol ±{res['tol_f']}°F)")
    print(f"  VERDICT: {res['verdict']} — {res['reason']}")
    return 0 if res["verdict"] == "ADOPT" else 2


def _selftest() -> int:
    sample = """CLIMAte REPORT filler
...THE SAN FRANCISCO AIRPORT CLIMATE SUMMARY FOR JULY 26 2026...
 MAXIMUM         69   4:04 PM  94    1963  73     -4       69
 MINIMUM         59  11:59 PM"""
    assert parse_cli_product(sample) == ("2026-07-26", 69.0)
    assert parse_cli_product(sample.replace("69   4:04", "M    4:04")) == \
        ("2026-07-26", None)                       # "M" sentinel -> None
    assert parse_cli_product("no header here") is None
    # Known-answer verdicts: ADOPT / REJECT (exact-rate) / REJECT (tol) / INSUFFICIENT
    good = [(f"2026-07-{d:02d}", 70.0, 70.0) for d in range(1, 31)]
    assert compare_cli_series(good)["verdict"] == "ADOPT"
    off = good[:29] + [("2026-07-30", 70.0, 74.0)]
    r = compare_cli_series(off)
    assert r["verdict"] == "REJECT" and "±" in r["reason"]       # 4.0 > tol
    sloppy = [(d, a, b if i % 2 else b + 1.0)
              for i, (d, a, b) in enumerate(good)]
    assert compare_cli_series(sloppy)["verdict"] == "REJECT"     # exact-rate < 90%
    short = good[:12]
    assert compare_cli_series(short)["verdict"] == "INSUFFICIENT"
    missing = [(d, None, b) for d, _, b in good]                  # one side missing
    assert compare_cli_series(missing)["n_comparable"] == 0
    print("verify_cli_archive selftest PASS (parse, M-sentinel, 4 verdict paths, "
          "missing-side handling)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
