"""True-settlement audit: score served verdict buckets against the contract's
OWN resolved outcome, and alarm when our proxy truth diverges from it.

WHY THIS EXISTS
---------------
Every internal metric (MAE, CRPS, coverage, the bucket hit in Validation) is
computed against the council's ANCHOR-STATION truth — a PROXY for what the market
actually paid out. For the two basket cities the proxy is meant to be the
settlement source itself (HK == HKO Observatory abs-daily-max; London ==
Wunderground EGLC), but "matched yesterday" is not "matches today". The only
honest answer to "did the verdict match the market" is the contract's resolved
bucket, read straight from Gamma (`market.MarketData.fetch_resolution`, persisted
by `storage.backfill_pm_resolutions` into `pm_resolved_label`).

This tool reports two things the internal scores cannot:
  * SERVED vs TRUE — the real objective ("match the highest-temperature bucket"):
    did the bucket our verdict's high snaps to equal the bucket the market paid?
  * PROXY vs TRUE — the alignment-gap alarm: did our anchor-station realized bucket
    (`realized_label`) disagree with the contract (`pm_resolved_label`)? Any such
    row means the truth we score ourselves on is NOT what settled, and any edge
    claim built on it is suspect for that day.

It is READ-ONLY w.r.t. forecasting: it never changes a verdict, a vote, a weight,
or the served distribution. With --backfill it first fetches any missing
authoritative resolutions (the only writes are the additive pm_resolved_* columns).

USAGE
-----
  PYTHONPATH=. python3 tools/settlement_audit.py --backfill
  PYTHONPATH=. python3 tools/settlement_audit.py --since 2026-06-05
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from weather_council.market import _native_reading_int
from weather_council.storage import (DB_PATH, _bucket_for_reading,
                                      backfill_pm_resolutions)


def _latest_verdict_high(conn: sqlite3.Connection, place: str, target: str):
    row = conn.execute(
        "SELECT high FROM verdicts WHERE place=? AND target_date=? "
        "ORDER BY issued_at DESC LIMIT 1", (place, target)).fetchone()
    return row[0] if row else None


def _rows(conn: sqlite3.Connection, since: str | None):
    """One representative (latest-issued) settled snapshot per (place, target)
    that carries an authoritative resolution."""
    q = ("SELECT place, target_date, grain, sub_degree, buckets_json, "
         "       realized_label, pm_resolved_label, MAX(issued_at) "
         "FROM market_snapshots WHERE pm_resolved_label IS NOT NULL ")
    args: list = []
    if since:
        q += "AND target_date >= ? "
        args.append(since)
    q += "GROUP BY place, target_date ORDER BY place, target_date"
    return conn.execute(q, args).fetchall()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backfill", action="store_true",
                    help="first fetch any missing authoritative resolutions")
    ap.add_argument("--since", default=None, help="only audit target dates >= YYYY-MM-DD")
    args = ap.parse_args()

    if args.backfill:
        for line in backfill_pm_resolutions():
            print("  backfilled:", line, file=sys.stderr)

    conn = sqlite3.connect(DB_PATH)
    rows = _rows(conn, args.since)
    if not rows:
        print("no settled snapshots with an authoritative resolution yet "
              "(run with --backfill once a market has resolved).")
        return 0

    print(f"{'place':22} {'date':11} {'served':>7} {'srv bkt':>9} "
          f"{'TRUE':>9} {'hit':>4} {'proxy':>9} {'gap':>4}")
    print("-" * 86)
    agg: dict[str, list[int]] = {}          # city -> [hits, n]
    gaps = 0
    n_proxy = 0                             # rows that HAD a proxy label to compare
    for (place, target, grain, sub_degree, buckets_json,
         proxy, pm, _issued) in rows:
        high = _latest_verdict_high(conn, place, target)
        served_bkt = "-"
        hit = ""
        if high is not None:
            reading = _native_reading_int(high, grain or "C", bool(sub_degree))
            served_bkt = _bucket_for_reading(json.loads(buckets_json), reading) or "?"
            hit = "Y" if served_bkt == pm else "."
            city = place.split(",", 1)[0]
            tally = agg.setdefault(city, [0, 0])
            tally[0] += 1 if hit == "Y" else 0
            tally[1] += 1
        gap = ""
        if proxy is not None and pm is not None:
            n_proxy += 1
            if proxy != pm:
                gap = "!!"
                gaps += 1
        print(f"{place[:22]:22} {target:11} "
              f"{('%.1f' % high if high is not None else '-'):>7} "
              f"{str(served_bkt):>9} {str(pm):>9} {hit:>4} "
              f"{str(proxy) if proxy is not None else '-':>9} {gap:>4}")
    print("-" * 86)
    for city, (h, n) in sorted(agg.items()):
        print(f"  {city}: served bucket matched TRUE settlement {h}/{n}")
    if n_proxy == 0:
        print("  (no proxy-settled days to cross-check yet — run settle_market_snapshots "
              "to populate realized_label, then the alignment-gap alarm has teeth.)")
    elif gaps:
        print(f"  ⚠ ALIGNMENT GAP: {gaps}/{n_proxy} cross-checked day(s) where our proxy "
              f"truth disagreed with the contract — internal scores for those days are "
              f"NOT settlement.")
    else:
        print(f"  proxy truth agreed with the contract on all {n_proxy} cross-checked day(s).")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
