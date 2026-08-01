"""ObsLog — the guard's Phase 1 persistence: every v3 read-sequence, from go-live.

The 2026-07-31 KSFO incident (cur_f 74°F on a frozen valid_local over a 72°F record)
was reconstructable only because a human captured the reads in-session; the machine
kept nothing. The corroboration guard (ledger/preregistered/cur_f_corroboration_guard_v2.md)
judges a cur_f lead on the SEQUENCE of reads — freshness of the obs stamp, liveness of
the secondary fields — so that sequence must be persisted per read, per city, from
go-live. This is that ledger. It is what makes the next incident replayable instead of
reconstructed (the prereg's MATERIAL DISCLOSURE applies to everything before go-live).

Append-only JSONL at `ledger/cur_f_obslog.jsonl`, keyed (city, date) — same convention
as ledger/intraday_tape.jsonl. One row per v3 current-conditions read:

    {v, city, date, ts_utc, cur_f, max24_f, valid_local,
     secondaries: {temperatureDewPoint, windSpeed, pressureMeanSeaLevel,
                   relativeHumidity, windDirection}}

A second ledger, `ledger/cur_f_guard_shadow.jsonl`, carries one row per SERVED
decision (shadow mode, prereg Phase 5): the guard's provenance verdict and every
input that produced it, so a served number is auditable against the frozen design
after the fact. Read-only w.r.t. forecasting: nothing here moves a number by itself;
the guard reads these rows to decide. KAT: tests/test_cur_f_guard.py.
"""
from __future__ import annotations

__all__ = ["OBSLOG_PATH", "SHADOW_PATH", "SECONDARY_FIELDS", "append_read",
           "load_reads", "append_decision"]

import json
import os

OBSLOG_PATH = "ledger/cur_f_obslog.jsonl"
SHADOW_PATH = "ledger/cur_f_guard_shadow.jsonl"
# The 5 usable v3 secondaries, frozen per config/guard_cities.json (Phase 0 Q1: all 6
# active cities expose all 5; the liveness contingency is NOT triggered anywhere).
SECONDARY_FIELDS = ("temperatureDewPoint", "windSpeed", "pressureMeanSeaLevel",
                    "relativeHumidity", "windDirection")


def _append(path: str, row: dict) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def append_read(city: str, date_iso: str, ts_utc: str, *,
                cur_f: float | None, max24_f: float | None,
                valid_local: str | None, secondaries: dict | None,
                path: str = OBSLOG_PATH) -> None:
    """Persist one v3 current-conditions read. `ts_utc` is the run's own UTC receipt
    stamp (caller-supplied; keeps the module deterministic under test). `secondaries`
    is filtered to the 5 frozen fields. No-op when there is no current reading."""
    if cur_f is None:
        return
    sec = {k: (secondaries or {}).get(k) for k in SECONDARY_FIELDS}
    _append(path, {"v": 1, "city": (city or "").strip().lower(), "date": date_iso,
                   "ts_utc": ts_utc, "cur_f": cur_f, "max24_f": max24_f,
                   "valid_local": valid_local, "secondaries": sec})


def load_reads(city: str, date_iso: str, path: str = OBSLOG_PATH) -> list[dict]:
    """This city/day's reads, oldest-first (append order). Missing file / bad lines /
    non-dict rows are skipped — a corrupt ledger yields FEWER corroborating reads,
    never fabricated ones (fail-closed by construction)."""
    key = (city or "").strip().lower()
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict):
                    continue
                if r.get("city") == key and r.get("date") == date_iso:
                    out.append(r)
    except OSError:
        pass
    return out


def append_decision(city: str, date_iso: str, ts_utc: str, decision: dict,
                    path: str = SHADOW_PATH) -> None:
    """Shadow-mode row (prereg Phase 5): the guard's provenance decision plus the
    inputs that produced it, on every serve. `decision` is the GuardResult as a dict
    (provenance, corroborated/fresh/sustained/converging, freshness window + basis,
    cur_f, recorded/served/banked floors). Emitted whether or not the lead banked —
    the UNCORROBORATED rows are the incident specimens."""
    _append(path, {"v": 1, "city": (city or "").strip().lower(), "date": date_iso,
                   "ts_utc": ts_utc, **decision})
