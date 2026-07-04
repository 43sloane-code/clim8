#!/usr/bin/env python3
"""Daily full-stack Singapore verdict -> a dated report under reports/.

Python (not a .sh) ON PURPOSE: macOS TCC blocks a launchd agent from reading/
executing a shell script in the Desktop folder ("Operation not permitted", exit
126), but python3 reading a .py from the same dir IS allowed — exactly how
accumulate.py runs. So the launchd jobs invoke `python3 tools/daily_verdict.py`.

Three sections per report: (1) council verdict (bias-corrected NWP blend + residual-cloud pmf +
live intraday lever + market compare + live scorecard), (2) WU-native intraday-
ceiling validation gate, (3) live+historical WU pattern. run.py targets the city's
own civil day, so lead 0 is always Singapore-today regardless of host clock.
"""
import datetime as dt
import os
import subprocess
from zoneinfo import ZoneInfo

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.environ.get("WX_PYTHON", "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3")


def _run(args: list[str]) -> str:
    env = dict(os.environ, PYTHONPATH=".")
    try:
        r = subprocess.run([PY, *args], cwd=REPO, env=env,
                           capture_output=True, text=True, timeout=600)
        return (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.returncode else "")
    except Exception as exc:
        return f"(command failed: {exc})\n"


def main() -> int:
    now = dt.datetime.now(ZoneInfo("Asia/Singapore"))
    os.makedirs(os.path.join(REPO, "reports"), exist_ok=True)
    out = os.path.join(REPO, "reports",
                       f"verdict-singapore-{now:%Y-%m-%d-%H%M}sgt.txt")
    sections = [
        f"===== SINGAPORE FULL-STACK VERDICT — {now:%Y-%m-%d %H:%M} SGT =====\n",
        "### 1. COUNCIL VERDICT (8 NWP + 92-member ensemble, gated bias correction + "
        "empirical residual-cloud bucket pmf, live intraday lever, market compare, live scorecard) ###",
        _run(["run.py", "Singapore", "--lead", "0", "--market", "--intraday"]),
        "### 2. INTRADAY VALIDATION (WU-native ceiling lever gate) ###",
        _run(["tools/intraday_ceiling_backtest.py", "--city", "singapore", "--hours", "13,14,15,16"]),
        "### 3. PATTERN RECOGNITION (live + historical Wunderground) ###",
        _run(["tools/wu_pattern.py", "--city", "singapore"]),
        "### 4. ACCRUAL LEDGERS (point-in-time; idempotent — run here AND in accumulate so one "
        "dead-network window cannot leave a permanent hole, per the 2026-07-02 outage) ###",
        _run(["tools/lock_logger.py"]),
        _run(["tools/twc_forecast_logger.py"]),
        _run(["tools/singapore_pop_logger.py"]),
        "### 5. SELF-EVALUATION (machine-generated brief — the agent relays this verbatim) ###",
        _run(["tools/eval_harness.py"]),
        f"===== END ({dt.datetime.now(ZoneInfo('Asia/Singapore')):%Y-%m-%dT%H:%M:%S%z}) =====\n",
    ]
    with open(out, "w") as f:
        f.write("\n".join(sections))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
