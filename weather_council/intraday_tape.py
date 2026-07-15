"""Intraday TAPE — the machine's memory of the settlement surface across a day's live runs.

Every failure in ISSUES_2026-07-12_INTRADAY_ACCURACY.md shares one mechanical root: each
live run was MEMORYLESS. "The endpoint is still rising" (H3), "the cur_f is sustained, not a
one-off" (rule G4), and "how often does a lead actually bank?" (F2) are all statements about
a SEQUENCE of reads — and the machine kept only the latest one, leaving the sequencing to a
human who mis-sequenced it (Karachi: endpoint 90°F(n=27) → 91°F(n=34) across two runs, called
"locked" in between).

This module is that sequence. Each live run appends ONE row per city/day — the endpoint
(`wunderground_daily_max`: max_f + its obs-count n) and the v3 nowcast (cur_f + its own obs
timestamp) — and pure functions over the rows answer the three questions mechanically:

  * endpoint_motion(rows)   — (rising, stable): is the settlement surface still catching peaks?
  * cur_f_sustained(rows)   — rule G4: the lead held across >=2 reads AND the v3 obs timestamp
                              refreshed (a FROZEN valid_local is the London 07-11 stale
                              over-read; a refreshing one is Karachi/Jeddah, which banked).
  * lead_bank_rate(...)     — the MEASURED rate at which past uncorroborated leads ended up on
                              the settlement record, so the coin-flip line quotes a ledger
                              rate instead of two anecdotes.

Append-only JSONL at `ledger/intraday_tape.jsonl`, keyed (city, date). Labels/evidence only —
nothing here touches the served pmf/modal/running max (HARD RULE 2). KAT:
tests/test_intraday_tape.py.
"""
from __future__ import annotations

__all__ = ["TAPE_PATH", "append_read", "load_reads", "endpoint_motion",
           "cur_f_sustained", "lead_bank_rate"]

import json
import os

TAPE_PATH = "ledger/intraday_tape.jsonl"
# Consecutive reads (including the latest) a cur_f lead must hold to count as SUSTAINED.
SUSTAIN_K = 2


def append_read(city: str, date_iso: str, ts: str, *,
                endpoint_f: float | None, endpoint_n: int | None,
                cur_f: float | None, cur_ts: str | None,
                path: str = TAPE_PATH) -> None:
    """Persist one live read of the settlement surface. `ts` is the run's own UTC stamp
    (caller-supplied; keeps this module deterministic under test). No-op when there is
    nothing observed to record (both endpoint and cur_f absent)."""
    if endpoint_f is None and cur_f is None:
        return
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    row = {"city": (city or "").strip().lower(), "date": date_iso, "ts": ts,
           "endpoint_f": endpoint_f, "endpoint_n": endpoint_n,
           "cur_f": cur_f, "cur_ts": cur_ts}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def load_reads(city: str, date_iso: str, path: str = TAPE_PATH) -> list[dict]:
    """This city/day's reads, oldest-first (append order). Missing file / bad lines -> []."""
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
                    continue          # a JSON-valid non-dict ("null", "42") is a bad line
                if r.get("city") == key and r.get("date") == date_iso:
                    out.append(r)
    except OSError:
        pass
    return out


def endpoint_motion(rows: list[dict]) -> tuple[bool, bool]:
    """(rising, stable) of the daily-max endpoint across this day's reads.
    rising: max_f strictly increased on its most recent defined change — the settlement
    surface is still catching between-obs peaks (Karachi 90→91°F). stable: the last
    SUSTAIN_K defined reads share one max_f — the peak has stopped moving. Fewer than
    two defined reads -> (False, False): one read can neither rise nor be called stable,
    so the conservative answer is "cannot lock"."""
    m = [r["endpoint_f"] for r in rows if isinstance(r.get("endpoint_f"), (int, float))]
    if len(m) < 2:
        return False, False
    rising = m[-1] > m[-2] + 1e-9
    tail = m[-SUSTAIN_K:]
    stable = max(tail) - min(tail) <= 1e-9
    return rising, stable


def cur_f_sustained(rows: list[dict], k: int = SUSTAIN_K) -> bool:
    """Rule G4's corroboration threshold, made mechanical: the latest cur_f lead counts as
    SUSTAINED iff the last `k` reads with a defined cur_f (i) all sit at-or-above the latest
    whole-°F value and (ii) carry at least two DISTINCT v3 obs timestamps — i.e. the nowcast
    is refreshing, not a frozen register. A frozen valid_local across reads is exactly the
    London 07-11 stale over-read; a refreshing same-or-rising cur_f is Karachi 07-12 /
    Jeddah 07-11, which both banked. Missing timestamps -> False (cannot prove freshness)."""
    tail = [r for r in rows if isinstance(r.get("cur_f"), (int, float))][-k:]
    if len(tail) < k:
        return False
    latest = round(tail[-1]["cur_f"])
    if any(round(r["cur_f"]) < latest for r in tail):
        return False
    stamps = {r.get("cur_ts") for r in tail}
    return len(stamps - {None}) >= 2


def lead_bank_rate(path: str = TAPE_PATH, *, before_date: str,
                   city: str | None = None) -> tuple[int, int]:
    """(banked, total) over COMPLETED days on the tape: days where some read showed an
    uncorroborated lead (whole-°F cur_f above that read's endpoint), scored by whether the
    day's FINAL endpoint reached the led value. Only days strictly before `before_date`
    count (an open day cannot be scored). `city=None` pools all cities. This is the ledger
    that replaces the Karachi/Jeddah anecdotes with a measured rate."""
    groups: dict[tuple[str, str], list[dict]] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict):
                    continue
                d = r.get("date")
                if not d or d >= before_date:      # dateless rows can never be scored
                    continue
                if city is not None and r.get("city") != (city or "").strip().lower():
                    continue
                groups.setdefault((r.get("city", ""), d), []).append(r)
    except OSError:
        return 0, 0
    banked = total = 0
    for rows in groups.values():
        leads = [round(r["cur_f"]) for r in rows
                 if isinstance(r.get("cur_f"), (int, float))
                 and isinstance(r.get("endpoint_f"), (int, float))
                 and round(r["cur_f"]) > round(r["endpoint_f"])]
        if not leads:
            continue
        finals = [r["endpoint_f"] for r in rows
                  if isinstance(r.get("endpoint_f"), (int, float))]
        if not finals:
            continue
        total += 1
        if round(finals[-1]) >= max(leads):
            banked += 1
    return banked, total


def _self_test() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "tape.jsonl")

        # Karachi 07-12 replay: two runs at endpoint 90, cur_f 91 refreshing; then the
        # endpoint catches the peak (91, n grew). The tape must read: sustained lead,
        # endpoint rising at the third run — and the completed day scores lead-BANKED.
        append_read("karachi", "2026-07-12", "13:44Z", endpoint_f=90, endpoint_n=27,
                    cur_f=91, cur_ts="13:30", path=p)
        append_read("karachi", "2026-07-12", "13:59Z", endpoint_f=90, endpoint_n=27,
                    cur_f=91, cur_ts="13:55", path=p)
        rows = load_reads("karachi", "2026-07-12", path=p)
        assert cur_f_sustained(rows) is True                      # refreshed + held 91
        assert endpoint_motion(rows) == (False, True)             # 90,90: not rising, stable
        append_read("karachi", "2026-07-12", "16:40Z", endpoint_f=91, endpoint_n=34,
                    cur_f=91, cur_ts="16:37", path=p)
        rows = load_reads("karachi", "2026-07-12", path=p)
        assert endpoint_motion(rows) == (True, False)             # 90→91: RISING blocks lock

        # London 07-11 stale replay: cur_f repeats but valid_local is FROZEN -> not sustained.
        append_read("london", "2026-07-11", "17:00Z", endpoint_f=88, endpoint_n=20,
                    cur_f=90, cur_ts="16:20", path=p)
        append_read("london", "2026-07-11", "17:30Z", endpoint_f=88, endpoint_n=20,
                    cur_f=90, cur_ts="16:20", path=p)
        assert cur_f_sustained(load_reads("london", "2026-07-11", path=p)) is False

        # Measured lead rate over the two completed days: Karachi banked, London did not.
        assert lead_bank_rate(p, before_date="2026-07-13") == (1, 2)
        assert lead_bank_rate(p, before_date="2026-07-13", city="karachi") == (1, 1)
        # An open day never scores.
        assert lead_bank_rate(p, before_date="2026-07-12") == (0, 1)

        # One read is neither rising nor stable; empty tape loads clean.
        assert endpoint_motion(load_reads("london", "2026-07-11", path=p)[:1]) == (False, False)
        assert load_reads("nowhere", "2026-01-01", path=p) == []
    print("intraday_tape self-test PASSED — sustained needs a refreshing v3 stamp (frozen "
          "London 07-11 rejected); a rising endpoint reads as rising; the lead-bank ledger "
          "scores only completed days (Karachi banked 1/1, pooled 1/2).")


if __name__ == "__main__":
    _self_test()
