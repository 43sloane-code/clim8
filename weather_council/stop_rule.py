"""Candidate 44 — the codified STOP / RESTART rule for the harness-optimizer search
loop (the OUTER proposer loop whose memory is `.harness_opt/ledger.json`, NOT the
per-experiment gate in `loop.py`).

The discipline was being applied by hand: keep proposing levers until several in a
row die, then pause and wait for fresh data. This module turns that habit into an
enforceable, inspectable rule a driver reads each iteration:

  1. AUTO-SUSPEND  — after `max_consecutive_nobake` gating entries in a row come
     back NO-BAKE / FALSIFIED / UNDERPOWERED (no edge), the loop suspends: probing
     the same exhausted design space again only manufactures multiple-testing risk.
  2. AUTO-REARM    — a suspended loop restarts only once ≥ `rearm_min_new_rows`
     fresh verification rows have accrued for EVERY station (new held-out data is
     the only honest reason to re-test a dead lever).
  3. SETTLEMENT FREEZE — on a market settlement day the loop is frozen (no promote /
     bake), so a number can never move while a market it feeds is resolving.
  4. RE-AUDIT      — a FALSIFIED verdict that was actually declared on < `reaudit_min_n`
     paired rows is downgraded to UNDERPOWERED: you cannot falsify a hypothesis you
     never had the power to test (absence of evidence ≠ evidence of absence).

Pure stdlib, deterministic, self-tested. The config lives in a plain JSON file the
loop reads (`.harness_opt/stop_rule.json`); built-in defaults make the module work
even if that file is absent. Recommend-only: this decides whether to *propose*, it
never touches the served Verdict.
"""
from __future__ import annotations

__all__ = [
    "DEFAULT_CONFIG", "load_config", "classify_verdict", "consecutive_negative_streak",
    "effective_n", "reaudit_falsified", "loop_state", "format_state",
]

import json
import os
import re

DEFAULT_CONFIG = {
    "version": 1,
    "max_consecutive_nobake": 3,
    "rearm_min_new_rows_per_station": 30,
    "settlement_freeze": True,
    "reaudit_min_n": 30,
    "stations": ["london_high", "london_low", "hong_kong_high", "hong_kong_low"],
    # Verdict vocabulary actually seen in the ledger (legacy store_cli + v2 schema).
    # Matched as UPPERCASE substrings against verdict + kind + title.
    "positive_tokens": ["SHIP", "RECOMMEND", "PROMOTED", "PASS"],
    "negative_tokens": ["NO-BAKE", "NO_BAKE", "FALSIFIED", "UNDERPOWERED",
                        "NO_EDGE", "NO-EDGE", "REVERTED", "FAIL"],
    "falsified_tokens": ["FALSIFIED"],
}

_CONFIG_PATH = os.path.join(".harness_opt", "stop_rule.json")
_N_RE = re.compile(r"\bn\s*=\s*(\d+)\b", re.IGNORECASE)


def load_config(path: str = _CONFIG_PATH) -> dict:
    """Load the rule config, falling back to (and back-filling from) DEFAULT_CONFIG
    so a partial or missing file never crashes the loop."""
    cfg = dict(DEFAULT_CONFIG)
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


def _tokens(entry: dict) -> str:
    """The searchable verdict text for one ledger entry: verdict + kind + title,
    upper-cased. Covers both ledger eras (legacy `verdict` strings and the v2
    `kind`/`title` where the NO-BAKE/SHIP signal often lives)."""
    parts = [str(entry.get(f, "")) for f in ("verdict", "kind", "title")]
    return " ".join(parts).upper()


def classify_verdict(entry: dict, config: dict) -> str:
    """POSITIVE / NEGATIVE / NEUTRAL for one entry. Precedence is POSITIVE > NEGATIVE
    > NEUTRAL: an entry that ships *anything* counts as progress and resets the
    streak, even if it also no-bakes a different lever."""
    text = _tokens(entry)
    if any(tok in text for tok in config["positive_tokens"]):
        return "POSITIVE"
    if any(tok in text for tok in config["negative_tokens"]):
        return "NEGATIVE"
    return "NEUTRAL"


def consecutive_negative_streak(log: list, config: dict) -> dict:
    """The current trailing run of NEGATIVE gating entries, in append order. NEUTRAL
    entries (pure SIM / ANALYSIS / NOTE — not edge tests) are SKIPPED, not counted
    and not streak-breaking. A POSITIVE entry breaks the streak."""
    streak, ids = 0, []
    broke_on = None
    for entry in reversed(log):
        cls = classify_verdict(entry, config)
        if cls == "NEUTRAL":
            continue
        if cls == "NEGATIVE":
            streak += 1
            ids.append(entry.get("id"))
        else:  # POSITIVE
            broke_on = entry.get("id")
            break
    ids.reverse()
    return {"streak": streak, "ids": ids, "broke_on": broke_on}


def effective_n(entry: dict) -> int | None:
    """Best estimate of the paired sample size a verdict rested on. Prefers the v2
    `n_paired_rows` field (min across stations = the binding power); else digs an
    `n=NNN` out of nested window/note strings (legacy entries). None if nothing
    numeric is recorded."""
    npr = entry.get("n_paired_rows")
    if isinstance(npr, dict):
        vals = [v for v in npr.values() if isinstance(v, (int, float))]
        if vals:
            return int(min(vals))
    if isinstance(npr, (int, float)):
        return int(npr)
    # dig n=NNN out of any nested string value.
    found = []

    def _walk(o):
        if isinstance(o, dict):
            for vv in o.values():
                _walk(vv)
        elif isinstance(o, list):
            for vv in o:
                _walk(vv)
        elif isinstance(o, str):
            for m in _N_RE.findall(o):
                found.append(int(m))

    _walk({k: v for k, v in entry.items() if k != "n_paired_rows"})
    return min(found) if found else None


def reaudit_falsified(log: list, config: dict) -> list:
    """Scan for FALSIFIED verdicts declared on too-thin samples. Returns one record
    per entry whose effective n is known AND below `reaudit_min_n`, proposing a
    downgrade to UNDERPOWERED. An entry whose n is unknown is reported with
    `proposal='REVIEW'` (its power can't be verified from the logged trace) rather
    than silently downgraded."""
    out = []
    fal = config["falsified_tokens"]
    thr = config["reaudit_min_n"]
    for entry in log:
        text = _tokens(entry)
        if not any(tok in text for tok in fal):
            continue
        n = effective_n(entry)
        if n is None:
            out.append({"id": entry.get("id"), "n": None, "proposal": "REVIEW",
                        "reason": "FALSIFIED but no sample size recorded — power unverifiable"})
        elif n < thr:
            out.append({"id": entry.get("id"), "n": n, "proposal": "UNDERPOWERED",
                        "reason": f"FALSIFIED on n={n} < {thr} — cannot falsify what you can't test"})
        else:
            out.append({"id": entry.get("id"), "n": n, "proposal": "KEEP_FALSIFIED",
                        "reason": f"FALSIFIED on n={n} >= {thr} — adequately powered, verdict stands"})
    return out


def loop_state(log: list, config: dict, *, new_rows_since_suspend: dict | None = None,
               settlement_day: bool = False) -> dict:
    """Decide whether the search loop may propose a new lever this iteration.

    `new_rows_since_suspend` maps station -> count of fresh verification rows accrued
    since the loop last suspended (the driver supplies it). Returns a dict with the
    machine status (ACTIVE / SUSPENDED / FROZEN), whether a new lever may be
    proposed, and a human reason."""
    streak_info = consecutive_negative_streak(log, config)
    streak = streak_info["streak"]
    thr = config["max_consecutive_nobake"]

    if settlement_day and config["settlement_freeze"]:
        return {"status": "FROZEN", "can_propose": False, "streak": streak,
                "threshold": thr, "rearm_eligible": False, "settlement_day": True,
                "streak_ids": streak_info["ids"],
                "reason": "settlement-day freeze: no promote/bake while a fed market resolves"}

    if streak >= thr:
        rearm_min = config["rearm_min_new_rows_per_station"]
        stations = config["stations"]
        if new_rows_since_suspend:
            per = {s: int(new_rows_since_suspend.get(s, 0)) for s in stations}
            worst = min(per.values()) if per else 0
            if worst >= rearm_min:
                return {"status": "ACTIVE", "can_propose": True, "streak": streak,
                        "threshold": thr, "rearm_eligible": True, "settlement_day": False,
                        "streak_ids": streak_info["ids"],
                        "reason": f"REARMED: >= {rearm_min} new rows on every station "
                                  f"(worst={worst}) after a {streak}-deep no-bake streak"}
            return {"status": "SUSPENDED", "can_propose": False, "streak": streak,
                    "threshold": thr, "rearm_eligible": False, "settlement_day": False,
                    "streak_ids": streak_info["ids"],
                    "reason": f"SUSPENDED: {streak} consecutive no-bakes (>= {thr}); "
                              f"awaiting {rearm_min} new rows/station (worst so far {worst})"}
        return {"status": "SUSPENDED", "can_propose": False, "streak": streak,
                "threshold": thr, "rearm_eligible": False, "settlement_day": False,
                "streak_ids": streak_info["ids"],
                "reason": f"SUSPENDED: {streak} consecutive no-bakes (>= {thr}); "
                          f"awaiting {rearm_min} new rows/station before re-arming"}

    return {"status": "ACTIVE", "can_propose": True, "streak": streak, "threshold": thr,
            "rearm_eligible": False, "settlement_day": False,
            "streak_ids": streak_info["ids"],
            "reason": f"ACTIVE: {streak}/{thr} consecutive no-bakes — design space not exhausted"}


def format_state(state: dict) -> str:
    flag = "may propose" if state["can_propose"] else "HOLD"
    return (f"[{state['status']}] {flag} — {state['reason']} "
            f"(streak {state['streak']}/{state['threshold']}, ids={state['streak_ids']})")


def _self_test() -> None:
    cfg = load_config(path="")  # defaults only — deterministic regardless of repo

    def E(id_, verdict="", kind="", title="", **kw):
        d = {"id": id_, "verdict": verdict, "kind": kind, "title": title}
        d.update(kw)
        return d

    # classification precedence.
    assert classify_verdict(E(1, kind="NO-BAKE+SIM"), cfg) == "NEGATIVE"
    assert classify_verdict(E(2, kind="ANALYSIS+NO-BAKE"), cfg) == "NEGATIVE"
    assert classify_verdict(E(3, verdict="SHIP_RECOMMEND_ONLY"), cfg) == "POSITIVE"
    assert classify_verdict(E(4, kind="SIM"), cfg) == "NEUTRAL"
    assert classify_verdict(E(5, verdict="SHIP", kind="also NO-BAKE"), cfg) == "POSITIVE"

    # streak: SIM/ANALYSIS-only entries are skipped; a SHIP resets.
    log = [E(40, verdict="NO_BAKE"), E(41, kind="SIM"), E(42, verdict="NO_BAKE"),
           E(43, verdict="NO_BAKE")]
    s = consecutive_negative_streak(log, cfg)
    assert s["streak"] == 3 and s["ids"] == [40, 42, 43], s  # 41 (SIM) skipped

    log2 = log + [E(44, verdict="SHIP")]
    assert consecutive_negative_streak(log2, cfg)["streak"] == 0

    # AUTO-SUSPEND at the threshold; ACTIVE below it.
    st = loop_state(log, cfg)
    assert st["status"] == "SUSPENDED" and not st["can_propose"], st
    assert loop_state(log[:2], cfg)["status"] == "ACTIVE"

    # AUTO-REARM only when EVERY station clears the fresh-row bar.
    rows_thin = dict.fromkeys(cfg["stations"], 30); rows_thin["london_low"] = 12
    assert loop_state(log, cfg, new_rows_since_suspend=rows_thin)["status"] == "SUSPENDED"
    rows_ok = dict.fromkeys(cfg["stations"], 30)
    rearmed = loop_state(log, cfg, new_rows_since_suspend=rows_ok)
    assert rearmed["status"] == "ACTIVE" and rearmed["rearm_eligible"], rearmed

    # SETTLEMENT FREEZE dominates even an ACTIVE, healthy loop.
    fr = loop_state(log[:1], cfg, settlement_day=True)
    assert fr["status"] == "FROZEN" and not fr["can_propose"], fr

    # RE-AUDIT: FALSIFIED on a thin sample downgrades; on a fat sample it stands;
    # with no recorded n it is flagged for REVIEW (never silently downgraded).
    ra = reaudit_falsified([
        E(90, title="sigma FALSIFIED", n_paired_rows={"a": 18, "b": 25}),   # thin
        E(91, title="drift FALSIFIED", n_paired_rows=460),                  # fat
        E(92, title="regime FALSIFIED",                                     # nested n=460
          wet_sigma_backtest={"window": "JJA 2021-25 n=460"}),
        E(93, title="mystery FALSIFIED"),                                   # unknown n
    ], cfg)
    by = {r["id"]: r for r in ra}
    assert by[90]["proposal"] == "UNDERPOWERED" and by[90]["n"] == 18, by[90]
    assert by[91]["proposal"] == "KEEP_FALSIFIED", by[91]
    assert by[92]["proposal"] == "KEEP_FALSIFIED" and by[92]["n"] == 460, by[92]
    assert by[93]["proposal"] == "REVIEW" and by[93]["n"] is None, by[93]

    print("stop_rule self-test PASSED "
          "(classify precedence; SIM/ANALYSIS skipped, SHIP resets; suspend at "
          "threshold; rearm needs every station; settlement freeze dominates; "
          "re-audit downgrades thin FALSIFIED, keeps fat, flags unknown-n)")


if __name__ == "__main__":
    _self_test()
