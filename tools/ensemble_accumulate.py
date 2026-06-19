#!/usr/bin/env python3
"""Accumulate the live ECMWF EPS ensemble per-member daily-max for the basket
cities, so the flow-dependent-spread (ensemble) lever can be GATED prospectively.

Why this exists
---------------
The ONLY mechanism that can beat the σ≈bucket day-ahead conviction ceiling is a
*flow-dependent* spread: on calm/predictable days the predictive distribution is
genuinely narrower, so bucket conviction legitimately exceeds the constant-σ
average — without needing better point skill. Every day-ahead lever tested on the
existing free feeds (6 statistical correctors, regime-gating, AIFS, four
high-resolution NWP models) failed the CRPS+bucket disjoint-fold gate because each
only chases a LOWER CONSTANT σ, which is information-capped.

The council's own *backtestable* proxy for flow-dependence — the 8 members'
per-day dispersion — is FLAT/weak and window-unstable (see spread_skill_eval on
live London/Manila: labels flip FLAT↔"tracks" between windows), so the structural
multi-model spread cannot carry the lever. A true initial-condition-perturbed
ECMWF EPS ensemble is *engineered* so its spread tracks error (Leutbecher & Palmer
2008); it MIGHT succeed where the structural council spread fails. But Open-Meteo's
free tier stores no historical ENSEMBLE members (only the control / mean — see
Sources.fetch_ensemble_history_means), so the EPS spread cannot be backtested.

The honest path is therefore prospective accumulation: capture the live per-member
bucket pmf daily, join it to the realized settlement once that lands, and GATE
after ~40 days with the SAME spread_skill_eval + bucket-hit-by-spread-tier
discipline as every other lever. The prior is unfavorable (the cheap proxy is
weak), so this is a genuine test, not a foregone win.

RECOMMEND-ONLY. Captures data; never moves a served verdict. No served
distribution changes until the accumulated record clears the gate.

Stdlib + project only. Reaches Open-Meteo through the already-allowlisted
ensemble-api via Sources.fetch_ensemble_members (no new host, no sign-off).

Usage:
  python3 tools/ensemble_accumulate.py            # capture today's pmf (both cities, lead 0 & 1)
  python3 tools/ensemble_accumulate.py --validate  # spread-skill status over the accrued record
  python3 tools/ensemble_accumulate.py --self-test # deterministic oracle
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import statistics
import sys
from pathlib import Path

# Basket cities (must match the verdict basket). Each settles round-half-up at
# whole °C on its airport (London → EGLC, Manila → RPLL).
CITIES = ("London", "Manila")
# ECMWF EPS — the operational IC-perturbed ensemble whose spread is engineered to
# be flow-dependent. 50 perturbed members + control on Open-Meteo.
MODEL = "ecmwf_ifs025"
# Below this many members the day's spread is not trustworthy — skip the capture.
MIN_MEMBERS = 20
# Gate cannot even be considered below this many settled, joined days (mirrors the
# spread_skill / calibration sample floors — never judge spread on a handful).
MIN_GATE_DAYS = 40

DATA_CSV = Path(__file__).resolve().parent.parent / "data" / "ensemble_accumulate.csv"

FIELDS = (
    "capture_date",   # city-local date the forecast was pulled (idempotency key)
    "target_date",    # day being forecast
    "city",
    "lead",           # target − capture, in days
    "model",
    "n_members",
    "mean_high",      # ensemble mean daily-max (°C)
    "sd_high",        # ensemble member SD — the flow-dependent spread signal
    "min_high",
    "max_high",
    "modal_bucket",   # round-half-up settlement bucket with the most members
    "modal_prob",     # member fraction in the modal bucket
    "pmf_json",       # {bucket:int -> prob:float} over settlement buckets
    "members_json",   # raw member highs (so any correction can be re-derived later)
)


def _round_half_up(x: float) -> int:
    """Whole-°C settlement rounding for London/Manila (mirrors
    weather_council.sources._round_half_up; inlined to keep the tool importable
    without side effects). round-half-up == floor(x + 0.5) for the positive
    summer maxima these markets settle on."""
    return math.floor(x + 0.5)


def _summarize(highs: list[float]) -> dict | None:
    """Member highs -> spread + settlement-bucket pmf summary, or None if thin."""
    vals = [float(h) for h in highs if h is not None]
    if len(vals) < MIN_MEMBERS:
        return None
    n = len(vals)
    buckets: dict[int, int] = {}
    for v in vals:
        b = _round_half_up(v)
        buckets[b] = buckets.get(b, 0) + 1
    pmf = {b: round(c / n, 4) for b, c in sorted(buckets.items())}
    modal_b = max(buckets.items(), key=lambda kv: kv[1])
    return {
        "n_members": n,
        "mean_high": round(statistics.mean(vals), 3),
        "sd_high": round(statistics.pstdev(vals), 4),
        "min_high": round(min(vals), 2),
        "max_high": round(max(vals), 2),
        "modal_bucket": modal_b[0],
        "modal_prob": round(modal_b[1] / n, 4),
        "pmf_json": json.dumps(pmf),
        "members_json": json.dumps([round(v, 2) for v in vals]),
    }


def _load(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        return []
    with csv_path.open(newline="") as f:
        return list(csv.DictReader(f))


def _write(csv_path: Path, rows: list[dict]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def capture(sources, csv_path: Path = DATA_CSV, leads=(0, 1)) -> list[dict]:
    """Pull today's live EPS pmf for both cities at the given leads and append
    idempotently (one row per capture_date × city × target × lead — re-running
    the same day refreshes rather than duplicates)."""
    from weather_council.sources import place_today

    rows = _load(csv_path)
    index = {(r["capture_date"], r["city"], r["target_date"], r["lead"]): i
             for i, r in enumerate(rows)}
    new = 0
    for city in CITIES:
        place = sources.geocode(city)
        today = place_today(place)
        for lead in leads:
            target = today + dt.timedelta(days=lead)
            highs, _lows = sources.fetch_ensemble_members(MODEL, place, target)
            summ = _summarize(highs)
            if summ is None:
                print(f"  skip {city} lead {lead}: only "
                      f"{len([h for h in highs if h is not None])} members",
                      file=sys.stderr)
                continue
            row = {
                "capture_date": today.isoformat(),
                "target_date": target.isoformat(),
                "city": city,
                "lead": str(lead),
                "model": MODEL,
                **summ,
            }
            key = (row["capture_date"], row["city"], row["target_date"], row["lead"])
            if key in index:
                rows[index[key]] = row
            else:
                rows.append(row)
                index[key] = len(rows) - 1
                new += 1
            print(f"  {city} lead {lead} target {target}: "
                  f"modal {summ['modal_bucket']}°C @ {summ['modal_prob']*100:.0f}% "
                  f"SD={summ['sd_high']:.2f} (n={summ['n_members']})")
    _write(csv_path, rows)
    print(f"captured: {new} new row(s), {len(rows)} total -> {csv_path.name}",
          file=sys.stderr)
    return rows


def validate(sources, csv_path: Path = DATA_CSV) -> dict:
    """Once targets have settled, join captured spread to realized error and run
    the spread–skill gate. Truth here is the ERA5-archive daily max at the city
    point — a PROXY pending the precise Wunderground-settlement join; it is enough
    to measure whether EPS spread tracks error (the lever's premise), but the
    bucket-hit economics must later use the true settlement value. Returns a
    status dict; prints a human summary."""
    from weather_council.sources import place_today
    from weather_council.spread_skill import spread_skill_eval

    rows = _load(csv_path)
    if not rows:
        print("no captures yet — run without --validate first (daily).")
        return {"status": "empty", "n": 0}

    # Group settled targets and fetch proxy truth per city in one archive call.
    by_city: dict[str, list[dict]] = {}
    for r in rows:
        by_city.setdefault(r["city"], []).append(r)

    pairs_by_city: dict[str, list[tuple[float, float]]] = {}
    for city, crows in by_city.items():
        place = sources.geocode(city)
        today = place_today(place)
        settled = [r for r in crows
                   if dt.date.fromisoformat(r["target_date"]) < today]
        if not settled:
            pairs_by_city[city] = []
            continue
        tmin = min(dt.date.fromisoformat(r["target_date"]) for r in settled)
        tmax = max(dt.date.fromisoformat(r["target_date"]) for r in settled)
        truth = sources.fetch_history_series(MODEL, place, tmin, tmax)  # proxy obs
        pairs = []
        for r in settled:
            obs = truth.get(r["target_date"])
            if obs is None:
                continue
            obs_high = obs[0] if isinstance(obs, (tuple, list)) else obs
            signed = float(r["mean_high"]) - float(obs_high)
            pairs.append((signed, float(r["sd_high"])))
        pairs_by_city[city] = pairs

    out: dict = {"status": "ok", "cities": {}}
    for city, pairs in pairs_by_city.items():
        ss = spread_skill_eval(pairs) if len(pairs) >= MIN_GATE_DAYS else None
        out["cities"][city] = {
            "captured": len(by_city.get(city, [])),
            "settled_pairs": len(pairs),
            "spread_skill": (ss.label if ss else None),
            "reliable": (ss.reliable if ss else None),
        }
        status = (f"spread–skill: {ss.label} (reliable={ss.reliable})" if ss
                  else f"need ≥{MIN_GATE_DAYS} settled days to gate "
                       f"(have {len(pairs)})")
        print(f"  {city}: {len(pairs)} settled pair(s) — {status}")
    return out


def _self_test() -> None:
    """Deterministic oracle: a fake Sources with known members; the summary,
    pmf, idempotency, and validate-status must read back exactly."""
    import tempfile

    class _FakePlace:
        latitude, longitude, timezone = 51.5, 0.0, "UTC"

    class _FakeSources:
        """Returns a fixed 10→ replicated member set so the bucket math is known."""
        def geocode(self, city):
            return _FakePlace()

        def fetch_ensemble_members(self, model, place, target):
            # 25 members centred on 22.0 with a tight spread; round-half-up:
            # values in [21.6,22.4] -> bucket 22 ; a couple at 22.6 -> 23.
            base = [22.0] * 20 + [21.6, 21.7, 22.4, 22.6, 22.6]
            return base, [15.0] * 25

        def fetch_history_series(self, model, place, start, end):
            return {}

    import weather_council.sources as src
    real_place_today = src.place_today
    src.place_today = lambda place: dt.date(2026, 6, 19)
    try:
        with tempfile.TemporaryDirectory() as d:
            csv_path = Path(d) / "ens.csv"
            rows = capture(_FakeSources(), csv_path, leads=(0, 1))
            # 2 cities × 2 leads = 4 rows, all new.
            assert len(rows) == 4, rows
            # Read back the PERSISTED form (CSV = all strings, what downstream
            # validate consumes), not the mixed-type in-memory return.
            persisted = _load(csv_path)
            r0 = next(r for r in persisted
                      if r["city"] == "London" and r["lead"] == "0")
            assert int(r0["modal_bucket"]) == 22, r0        # 23 of 25 round to 22
            assert abs(float(r0["modal_prob"]) - 23 / 25) < 1e-9, r0
            pmf = json.loads(r0["pmf_json"])
            assert pmf == {"22": round(23 / 25, 4), "23": round(2 / 25, 4)}, pmf
            assert json.loads(r0["members_json"])[0] == 22.0
            # Idempotency: a second capture same day must NOT duplicate.
            rows2 = capture(_FakeSources(), csv_path, leads=(0, 1))
            assert len(rows2) == 4, f"re-capture duplicated: {len(rows2)}"
            # Validate with no settled targets (all are >= 2026-06-19) -> 0 pairs.
            st = validate(_FakeSources(), csv_path)
            assert st["status"] == "ok", st
            for c in CITIES:
                assert st["cities"][c]["settled_pairs"] == 0, st
    finally:
        src.place_today = real_place_today
    print("ensemble_accumulate self-test PASSED "
          "(summary+pmf exact; idempotent re-capture; validate status honest)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--validate", action="store_true",
                    help="report spread–skill status over the accrued record")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        _self_test()
        return 0

    from weather_council.sources import Sources
    sources = Sources()
    if args.validate:
        validate(sources)
    else:
        capture(sources)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
