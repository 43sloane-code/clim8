#!/usr/bin/env python3
"""Local C7 accumulation loop — settle Hong Kong + London snapshots forward.

WHY THIS EXISTS
---------------
The C7 realized-outcome scoreboard (`weather_council/edge.py`, surfaced by
`run.py --edge`) can only certify a council-vs-market edge once at least
MIN_SETTLED *distinct days* have SETTLED. Market prices are never archived, so
that settled set can only grow FORWARD in time — there is no backfill. This is
the durable local accumulator that grows it on this machine, where the code and
the network egress actually live (the cloud routine's pushes proved unreliable).

WHAT EACH RUN DOES — all through the existing, tested, read-only CLI paths:
  1. For London and Hong Kong, log ONE *day-ahead* (``--lead 1``) council-vs-
     market snapshot — but only if today's snapshot for that city is not already
     on the ledger. The idempotency guard means the LaunchAgent can fire several
     times a day for robustness (in case the machine was asleep) WITHOUT writing
     correlated intraday duplicates that would inflate C7's ``n`` and falsely
     narrow its bootstrap CI.
  2. Settle every verdict/snapshot whose target day is now observable
     (``--verify``, ``--edge``), each against its OWN anchor station — EGLC for
     London, the Hong Kong Observatory for HK — exactly as the verdict was scored.
  3. Append a timestamped line to ``logs/accumulate.log`` and refresh
     ``reports/c7_status.txt`` with the latest C7 verdict, so forward progress
     toward the ``>=20 settled days`` bar is visible at a glance.

WHY DAY-AHEAD, ONCE A DAY (not every few hours)
-----------------------------------------------
The binding constraint on validation is *distinct settled calendar-days*, not
snapshot frequency. One snapshot per city per day keeps C7's ``n`` ~= the number
of real days, and ``--lead 1`` tests the FAIR question — can the council's
day-ahead forecast beat the day-ahead market — rather than a near-settled late-
day book the lead-time model could never match.

HARD BOUNDARIES (inherited, non-negotiable)
-------------------------------------------
Read-only / recommend-only. This orchestrates ``run.py``, which fetches public
data and writes the LOCAL ledger only. It never edits served verdict code or
numbers, never auto-applies a bias, and never places a trade or moves funds.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable                       # the interpreter running this script
DB = ROOT / "verdicts.db"
LOGS = ROOT / "logs"
LOG = LOGS / "accumulate.log"
STATUS = ROOT / "reports" / "c7_status.txt"
LOCK = LOGS / ".accumulate.lock"

# place.label() renders as "Manila, Philippines" / "Singapore, Singapore", so a
# LIKE 'City%' prefix matches the stored rows without hard-coding the suffix.
CITIES = ["Manila", "Singapore"]
LEAD = 1                                   # day-ahead: the fair edge test
TIMEOUT_S = 600                            # generous per-subprocess cap


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _log(msg: str) -> None:
    LOGS.mkdir(exist_ok=True)
    line = f"[{_now()}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def _run(args: list[str]) -> tuple[int, str]:
    """Invoke the read-only run.py CLI in a subprocess (PYTHONPATH pinned to the
    repo so it imports regardless of the launchd working directory)."""
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    try:
        p = subprocess.run(
            [PY, "run.py", *args], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=TIMEOUT_S,
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {TIMEOUT_S}s"


def _snapshotted_today(city: str) -> bool:
    """True if a market snapshot for this city was already issued today — the
    idempotency key that keeps repeated daily fires from writing duplicates."""
    if not DB.exists():
        return False
    today = dt.date.today().isoformat()
    con = sqlite3.connect(DB)
    try:
        row = con.execute(
            "SELECT 1 FROM market_snapshots WHERE place LIKE ? "
            "AND substr(issued_at,1,10)=? LIMIT 1",
            (city + "%", today),
        ).fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return False                        # table not created yet — first run
    finally:
        con.close()


def main() -> int:
    LOGS.mkdir(exist_ok=True)
    lock_fd = open(LOCK, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        _log("another accumulate run is active — exiting")
        return 0

    _log("=== accumulate start ===")

    # 1. Day-ahead snapshots, idempotent per city per day.
    for city in CITIES:
        if _snapshotted_today(city):
            _log(f"{city}: already have today's snapshot — skipping --market")
            continue
        rc, out = _run([city, "--lead", str(LEAD), "--market"])
        logged = _snapshotted_today(city)
        _log(f"{city}: --market lead{LEAD} rc={rc} snapshot_logged={logged}")
        if not logged:
            note = next(
                (l.strip() for l in out.splitlines()
                 if "withheld" in l.lower() or "no market" in l.lower()),
                "no matching market / comparison withheld",
            )
            _log(f"{city}: no snapshot — {note}")

    # 1b. Forward-log TWC's OWN day-ahead forecast (the candidate 9th council member earning a
    # backtestable record; recommend-only, never feeds the blend). Non-fatal — a TWC hiccup must
    # never break the core accrual loop, so it runs isolated and its failure is only logged.
    try:
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        p = subprocess.run([PY, "tools/twc_forecast_logger.py"], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=TIMEOUT_S)
        last = next((l for l in reversed((p.stdout or "").splitlines()) if l.strip()), "")
        _log(f"twc forecast log rc={p.returncode} | {last.strip()}")
    except Exception as e:                                   # noqa: BLE001 — non-fatal by design
        _log(f"twc forecast log failed (non-fatal): {type(e).__name__}: {e}")

    # 1c. Point-in-time PoP for the Singapore regime-split (pre-registered; recommend-only; the
    # [D14]-deferred lever's leak-free clock). Non-fatal, isolated — never breaks core accrual.
    try:
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        p = subprocess.run([PY, "tools/singapore_pop_logger.py"], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=TIMEOUT_S)
        last = next((l for l in reversed((p.stdout or "").splitlines()) if l.strip()), "")
        _log(f"singapore pop log rc={p.returncode} | {last.strip()}")
    except Exception as e:                                   # noqa: BLE001 — non-fatal by design
        _log(f"singapore pop log failed (non-fatal): {type(e).__name__}: {e}")

    # 2. Settle whatever is now observable, against each verdict's anchor station.
    rc, out = _run(["--verify"])
    last = out.strip().splitlines()[-1] if out.strip() else "(nothing ready)"
    _log(f"--verify rc={rc} | {last}")

    rc, edge_out = _run(["--edge"])
    _log(f"--edge rc={rc}")

    # 3. Refresh the at-a-glance C7 status file.
    try:
        STATUS.write_text(f"# C7 status as of {_now()}\n\n{edge_out}\n")
        _log(f"wrote {STATUS.relative_to(ROOT)}")
    except OSError as e:
        _log(f"status write failed: {e}")

    _log("=== accumulate done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
