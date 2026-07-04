"""p2b_1200_logger.py — forward instrument for the P2b pre-registration (12:00-only lock).

Logs, point-in-time each day, BOTH the unconditional and the (state@12 x live-cloud-tercile)
conditional 12:00 bucket pmfs for Singapore, then settles prior rows against the final IEM
bucket. Recommend-only: nothing served changes; this ledger exists so the frozen gate in
ledger/preregistered/p2b_1200_forward.md can rule at n>=60. Tercile thresholds are FROZEN
constants from the D15 warmup block (2016-17); the cloud predictor is the LIVE open-meteo
forecast feed — the deployable version, not ERA5 (D11's reanalysis-optimism lesson).

Run daily:  PYTHONPATH=. python3 tools/p2b_1200_logger.py
Self-test:  PYTHONPATH=. python3 tools/p2b_1200_logger.py --selftest
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "ledger" / "p2b_1200.jsonl"
ARCHIVE = ROOT / "data" / "wsss_hourly_iem.jsonl"
T1, T2 = 84.3, 97.7                 # FROZEN cloud terciles (D15 warmup block, 2016-17)
MIN_CELL = 30
HOUR = 12
HOLD_DELTA = 0.3                    # same day-state rule as the shipped lever


def bucket(c: float) -> int:
    return math.floor(c + 0.5)


def tercile(cloud: float | None) -> int | None:
    if cloud is None:
        return None
    return 0 if cloud < T1 else (1 if cloud < T2 else 2)


def day_state(obs: list, hour: float) -> str | None:
    pri = [(hh, c) for hh, c in obs if hh <= hour]
    if not pri:
        return None
    rm = max(c for _, c in pri)
    return "holding" if pri[-1][1] >= rm - HOLD_DELTA else "declining"


def pmf_from_rises(rises: list, runmax: float) -> dict[int, float]:
    """Exact resample of sorted rises through the round-half-up quantizer."""
    if not rises:
        return {}
    out = {}
    for k in range(bucket(runmax + rises[0]), bucket(runmax + rises[-1]) + 1):
        left = bisect.bisect_left(rises, k - 0.5 - runmax)
        right = bisect.bisect_left(rises, k + 0.5 - runmax)
        if right > left:
            out[k] = round((right - left) / len(rises), 4)
    return out


def modal(pmf: dict[int, float]) -> int | None:
    return max(pmf, key=pmf.get) if pmf else None


def build_history(archive_rows: list, before: str):
    """(sorted uncond rises@12, {(state,terc): sorted rises}) from strictly-earlier days.
    Cloud for historical cells comes from the training table (ERA5) — the CELL POPULATIONS
    are historical climatology; only TODAY'S predictor must be the live feed."""
    tr = {}
    tpath = ROOT / "data" / "wsss_training.jsonl"
    if tpath.exists():
        for line in tpath.read_text().splitlines():
            r = json.loads(line)
            tr[r["date"]] = r.get("cloud_8_13")
    unc, cells = [], {}
    for r in archive_rows:
        if r["date"] >= before:
            continue
        obs = r["obs"]
        pri = [c for hh, c in obs if hh <= HOUR]
        if not pri:
            continue
        rm = max(pri)
        rise = max(c for _, c in obs) - rm
        bisect.insort(unc, rise)
        st = day_state(obs, HOUR)
        tc = tercile(tr.get(r["date"]))
        if st is not None and tc is not None:
            cells.setdefault((st, tc), [])
            bisect.insort(cells[(st, tc)], rise)
    return unc, cells


def settle_rows(rows: list, archive: dict) -> int:
    """Stamp settled_bucket on unsettled rows whose day is complete in the archive."""
    n = 0
    for r in rows:
        if r.get("settled_bucket") is None and r["date"] in archive:
            obs = archive[r["date"]]
            if obs and max(hh for hh, _ in obs) >= 18:          # day complete
                r["settled_bucket"] = bucket(max(c for _, c in obs))
                n += 1
    return n


def _selftest() -> int:
    assert bucket(28.9) == 29 and bucket(28.4) == 28
    assert tercile(50) == 0 and tercile(90) == 1 and tercile(99) == 2 and tercile(None) is None
    assert day_state([(10, 30.0), (12, 32.0)], 12) == "holding"
    assert day_state([(10, 32.0), (12, 31.0)], 12) == "declining"
    pmf = pmf_from_rises([0.0, 0.1, 0.2, 1.0], 31.1)            # 31.1/31.2/31.3 -> 31; 32.1 -> 32
    assert modal(pmf) == 31 and abs(sum(pmf.values()) - 1.0) < 1e-6 and pmf == {31: 0.75, 32: 0.25}
    rows = [{"date": "2026-07-01", "settled_bucket": None}]
    arch = {"2026-07-01": [(9, 30.0), (14, 32.6), (19, 30.0)]}
    assert settle_rows(rows, arch) == 1 and rows[0]["settled_bucket"] == 33
    # paired-gate arithmetic sanity: McNemar counts from rows
    hits = [(1, 0), (1, 0), (0, 1), (1, 1)]                     # cond-hit, unc-hit pairs
    b = sum(1 for c, u in hits if c and not u)
    c_ = sum(1 for c, u in hits if u and not c)
    assert (b, c_) == (2, 1)
    print("p2b_1200_logger selftest PASS (bucket, frozen terciles, state, pmf resample, "
          "settle stamp, discordant-pair counts)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()

    from weather_council.sources import LIVE_URL, Sources
    src = Sources()
    tz = ZoneInfo("Asia/Singapore")
    today = dt.datetime.now(tz).date().isoformat()

    rows = []
    if LEDGER.exists():
        rows = [json.loads(l) for l in LEDGER.read_text().splitlines() if l.strip()]

    archive_rows = [json.loads(l) for l in ARCHIVE.read_text().splitlines()]
    archive = {r["date"]: r["obs"] for r in archive_rows}
    # keep the archive current: pull the last few days from IEM and persist complete
    # days, so yesterday's row can settle without waiting for a manual backfill
    t0 = dt.date.fromisoformat(today)
    try:
        recent = src.fetch_metar_observations("WSSS", t0 - dt.timedelta(days=4),
                                              t0 + dt.timedelta(days=1), "Asia/Singapore")
        by: dict[str, list] = {}
        for ts, c in recent:
            by.setdefault(ts[:10], []).append([float(ts[11:13]) + float(ts[14:16]) / 60.0, c])
        added = [d for d, o in by.items() if d not in archive and d < today and len(o) >= 20]
        if added:
            for d in added:
                archive[d] = by[d]
                archive_rows.append({"date": d, "obs": by[d]})
            archive_rows.sort(key=lambda r: r["date"])
            with open(ARCHIVE, "w") as f:
                for r in archive_rows:
                    f.write(json.dumps(r) + "\n")
            print(f"P2B: archive extended +{len(added)} day(s): {', '.join(sorted(added))}")
    except Exception as e:
        print(f"P2B: incremental archive update failed ({type(e).__name__}) — settling on what exists")
    settled = settle_rows(rows, archive)

    if any(r["date"] == today for r in rows):
        print(f"P2B: {today} already logged (idempotent); settled {settled} prior row(s)")
    else:
        # today's obs through 12:00 SGT — live IEM fetch for just today
        t = dt.date.fromisoformat(today)
        obs = [(float(ts[11:13]) + float(ts[14:16]) / 60.0, c)
               for ts, c in src.fetch_metar_observations(
                   "WSSS", t, t + dt.timedelta(days=1), "Asia/Singapore")
               if ts[:10] == today]
        pri = [(hh, c) for hh, c in obs if hh <= HOUR]
        if not pri:
            print(f"P2B: no WSSS obs by 12:00 SGT yet for {today} — row skipped this run")
        else:
            runmax = max(c for _, c in pri)
            st = day_state(obs, HOUR)
            cloud = None
            try:
                d = src.http.get_json(LIVE_URL, {
                    "latitude": 1.3502, "longitude": 103.994, "timezone": "Asia/Singapore",
                    "hourly": "cloudcover", "start_date": today, "end_date": today})
                vals = [v for tt, v in zip(d["hourly"]["time"], d["hourly"]["cloudcover"])
                        if 8 <= int(tt[11:13]) <= 12 and isinstance(v, (int, float))]
                cloud = round(sum(vals) / len(vals), 1) if vals else None
            except Exception:
                pass                                           # predictor missing -> fallback row
            unc, cells = build_history(archive_rows, before=today)
            tc = tercile(cloud)
            cell = cells.get((st, tc), []) if (st and tc is not None) else []
            fallback = len(cell) < MIN_CELL
            u_pmf = pmf_from_rises(unc, runmax)
            c_pmf = u_pmf if fallback else pmf_from_rises(cell, runmax)
            row = {"date": today, "issued_ts": dt.datetime.now(tz).isoformat(timespec="minutes"),
                   "runmax12": round(runmax, 2), "state12": st, "cloud_live": cloud,
                   "tercile": tc, "cell_n": len(cell), "fallback": fallback,
                   "unc_modal": modal(u_pmf), "cond_modal": modal(c_pmf),
                   "unc_pmf": u_pmf, "cond_pmf": c_pmf, "settled_bucket": None}
            rows.append(row)
            print(f"P2B: logged {today} runmax12={runmax:.1f} state={st} cloud={cloud} "
                  f"terc={tc} cell_n={len(cell)}{' FALLBACK' if fallback else ''} "
                  f"unc->{row['unc_modal']} cond->{row['cond_modal']}; settled {settled} prior")

    LEDGER.parent.mkdir(exist_ok=True)
    with open(LEDGER, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    done = [r for r in rows if r.get("settled_bucket") is not None and not r.get("fallback")]
    print(f"P2B ledger: {len(rows)} rows, {len(done)} settled non-fallback / 60 needed "
          f"(gate: ledger/preregistered/p2b_1200_forward.md)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
