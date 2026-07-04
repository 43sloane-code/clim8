"""eval_harness.py — the system's SELF-EVALUATION brief: the machine speaks for the agent.

Every narration failure this week had the same root: the agent (human or LLM) paraphrasing
ledgers from memory — "constantly affirmed 95%", "ensured 32", "coin-flip 31/32" — while the
ledgers said something more careful. This harness inverts the flow: it reads EVERY instrument
(lock certification ledger, live day-ahead scorecard, TWC pairs, PoP regime clock, dead-candidate
ledger) and emits a SPEAKABLE BRIEF — exact, citable sentences with the certainty vocabulary
enforced by code:

  * observation-grade words (banked / floor / guaranteed) ONLY for the running-max ratchet;
  * "final" ONLY post-sunset (~19:10 SGT — the heat engine off, physics not confidence);
  * conviction percentages ONLY as "backtest, uncertified" until the certification ledger
    reaches its frozen n>=20 bar, then only the MEASURED number;
  * every accruing clock reported as a count, never as a conclusion.

The agent's job becomes relaying this brief, not summarizing the system from memory. Read-only;
deterministic given the ledgers; stdlib-only. Runs standalone and as the last section of the
daily report (tools/daily_verdict.py).

Run:       PYTHONPATH=. python3 tools/eval_harness.py
Self-test: PYTHONPATH=. python3 tools/eval_harness.py --selftest
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
SUNSET_SGT_H = 19.2          # ~19:10 SGT year-round (equatorial); the PHYSICAL finality clock
TWC_GATE = 40                # settled pairs before the TWC member gate may run
POP_GATE_DRY = 15            # dry-day rows before the pre-registered regime split may be tested

try:
    from tools.lock_logger import load_rows, coverage, certify, N_FLOOR, TOL
except ImportError:                                   # direct-script execution path
    from lock_logger import load_rows, coverage, certify, N_FLOOR, TOL


# ---------------------------------------------------------------- gather ----

def gather(now_sgt: _dt.datetime | None = None) -> dict:
    """Read every ledger into one state dict. Read-only; missing ledgers -> honest absences."""
    now = now_sgt or _dt.datetime.now(ZoneInfo("Asia/Singapore"))
    state: dict = {"now_sgt": now.isoformat(timespec="minutes"),
                   "pre_sunset": (now.hour + now.minute / 60.0) < SUNSET_SGT_H}

    rows = load_rows()
    cov = coverage(rows)
    week_ago = (now.date() - _dt.timedelta(days=7)).isoformat()
    recent_hours = {h: sum(1 for r in rows if int(r["hour"]) == h
                           and r["target_date"] >= week_ago) for h in (12, 13, 14, 15, 16, 18)}
    state["lock"] = {"rows": len(rows),
                     "settled": sum(1 for r in rows if r.get("hit") is not None),
                     "cov": {h: dict(c) for h, c in cov.items()},
                     "status": certify(cov),
                     "recent_hours": recent_hours}
    today = now.date().isoformat()
    todays = [r for r in rows if r["target_date"] == today and r["kind"] == "sharpened"]
    state["lock"]["today"] = max(todays, key=lambda r: r["hour"]) if todays else None

    try:
        from weather_council.storage import live_bucket_scorecard
        state["scorecard"] = {c: live_bucket_scorecard(lbl)
                              for c, lbl in (("Singapore", "Singapore, Singapore"),
                                             ("Manila", "Manila, Philippines"))}
    except Exception:
        state["scorecard"] = {}

    try:
        con = sqlite3.connect(ROOT / "verdicts.db")
        twc = con.execute(
            "SELECT fc_high, actual_high FROM tracked_forecasts "
            "WHERE source='twc' AND actual_high IS NOT NULL").fetchall()
        con.close()
        hits = sum(1 for f, a in twc                     # settlement rule: round-half-up whole C
                   if math.floor(f + 0.5) == math.floor(a + 0.5))
        state["twc"] = {"n": len(twc), "hits": hits}
    except Exception:
        state["twc"] = {"n": 0, "hits": 0}

    dry = conv = 0
    try:
        for line in (ROOT / "ledger" / "singapore_pop.jsonl").read_text().splitlines():
            r = json.loads(line)
            dry += r.get("regime") == "dry"
            conv += r.get("regime") == "convective"
    except OSError:
        pass
    state["pop"] = {"dry": dry, "convective": conv}

    try:
        dead = [json.loads(l)["id"] for l in
                (ROOT / "ledger" / "dead_candidates.jsonl").read_text().splitlines() if l.strip()]
    except OSError:
        dead = []
    state["dead"] = dead
    return state


# ----------------------------------------------------------------- brief ----

def brief(state: dict) -> list[str]:
    """The speakable self-evaluation. Every sentence is citable as-is; vocabulary is enforced
    here so the agent cannot drift: no 'final' pre-sunset, no uncertified conviction as fact."""
    L = ["SYSTEM SELF-EVALUATION (machine-generated from the ledgers — relay verbatim)"]

    lk = state["lock"]
    L.append(f"  LOCK: {lk['rows']} point-in-time rows, {lk['settled']} settled. Certification "
             f"(frozen bar: n>={N_FLOOR}/hour, tol -{TOL:.0%}):")
    if not lk["cov"]:
        L.append("    no settled certification-hour rows yet — every conviction claim is "
                 "'backtest, UNCERTIFIED'.")
    for h in sorted(lk["cov"]):
        c, s = lk["cov"][h], lk["status"][h]
        cite = (f"cite as: 'measured {c['hit_rate']:.0%} (n={c['n']})'" if c["n"] >= N_FLOOR
                else f"cite as: 'backtest {c['mean_stated']:.0%}, live {int(c['hit_rate']*c['n'])}/"
                     f"{c['n']}, UNCERTIFIED'")
        L.append(f"    {h:02d}:00 — n={c['n']}, stated {c['mean_stated']:.0%}, "
                 f"hit {c['hit_rate']:.0%} [{s}] -> {cite}")
    t = lk.get("today")
    if t is not None:
        L.append(f"  TODAY'S LOCK ROW: hour {int(t['hour']):02d}, modal {t['modal_bucket']} @ "
                 f"{(t['modal_prob'] or 0)*100:.0f}% — "
                 + ("NOT FINAL (pre-sunset: only the running-max FLOOR is observation-grade; "
                    "'final' is a post-~19:10-SGT word)" if state["pre_sunset"]
                    else "post-sunset: the day's max is physically closed; 'final' is permitted"))

    for city, sc in (state.get("scorecard") or {}).items():
        if sc and sc.get("n"):
            L.append(f"  DAY-AHEAD {city}: served-vs-settled {sc['hits']}/{sc['n']} "
                     f"= {sc['rate']:.0%} — the single bucket is a boundary coin-flip by "
                     f"construction; the BAND is the deliverable; no day-ahead pinpoint exists "
                     f"({len(state['dead'])} levers dead).")

    twc = state["twc"]
    L.append(f"  TWC (candidate 9th member): {twc['n']}/{TWC_GATE} settled pairs "
             f"({twc['hits']} bucket-hits) — recommend-only; may be SHOWN in the cross-check, "
             f"may NOT be blended or cited as validated.")
    pop = state["pop"]
    L.append(f"  PoP REGIME CLOCK: {pop['dry']}/{POP_GATE_DRY} dry days (+{pop['convective']} "
             f"convective) — the pre-registered split is UNTESTED until the dry floor fills; "
             f"the tag is context, never a band-mover.")
    L.append(f"  DEAD LEDGER: {len(state['dead'])} closed candidates "
             f"({state['dead'][-1] if state['dead'] else '-'} latest) — do not relitigate.")
    L.append("  VOCABULARY GUARD: 'banked/floor/guaranteed' = running-max ratchet ONLY; "
             "'final' = post-sunset ONLY; percentages = the certification table ONLY "
             "(else say 'backtest, uncertified'); anything else is narration, not measurement.")
    return L


def directives(state: dict) -> list[str]:
    """NECESSARY NEXT — what actually buys accuracy, machine-ranked from the ledgers.
    The honest brief says where we ARE; this says the shortest path forward. Ranking logic:
    (1) uninstrumented measurement is free accuracy (you cannot certify or catch what you
    never read); (2) the one ADJUDICATED model defect; (3) clocks that only time can fill;
    (X) the proven dead ends, so effort is never spent there again."""
    L = ["NECESSARY NEXT (machine-ranked: what buys accuracy — everything else is waiting)"]
    rank = 1
    rec = state["lock"].get("recent_hours", {})
    dead_hours = sorted(h for h, n in rec.items() if n == 0)
    if dead_hours:
        L.append(f"  {rank}. UNINSTRUMENTED certification hours {dead_hours}: no scheduled read "
                 f"feeds these bins, so their conviction can never be certified and their-tail "
                 f"days (late climbs live in >16:00) go unwatched. Zero-cost fix — load:")
        if any(h >= 17 for h in dead_hours):
            L.append("       launchctl load ~/Library/LaunchAgents/com.weatherverdict.verdict-evening.plist   (18:15 SGT — settle-grade)")
        if any(h in (12, 13) for h in dead_hours):
            L.append("       launchctl load ~/Library/LaunchAgents/com.weatherverdict.verdict-midday.plist    (13:15 SGT — fills 12/13)")
        rank += 1
    L.append(f"  {rank}. SINGAPORE MODEL: no open defect — band coverage 90% (calibrated, "
             f"verify_skill), lock calibrated at its hour (replay 95.0% vs stated 94.1%, n=180). "
             f"The one deferred Singapore lever is the pre-registered PoP regime split "
             f"(weather-bound); its gate (frozen-A/B + disjoint folds) gets built WHEN the "
             f"dry-day clock fills, not before.")
    L.append("     (Manila: OUT OF SCOPE by user directive 2026-07-04 — its under-dispersion "
             "defect stays recorded in FINDINGS/HANDOFF, unworked.)")
    rank += 1
    twc = state["twc"]
    twc_eta = ("READY for the gate" if twc["n"] >= TWC_GATE
               else f"~{max(1, (TWC_GATE - twc['n'] + 1) // 2)}d at 2 pairs/day")
    lock15 = state["lock"]["cov"].get(15, {}).get("n", 0)
    L.append(f"  {rank}. CLOCKS (time-bound, no action accelerates them except not missing "
             f"days): lock 15:00 bin {lock15}/{N_FLOOR}; TWC {twc['n']}/{TWC_GATE} "
             f"({twc_eta}); PoP {state['pop']['dry']}/{POP_GATE_DRY} dry days "
             f"(rate-limited by the weather itself). Redundant logging already guards them.")
    L.append("  X. Spend NOTHING on: day-ahead point/conditioning levers (0/14, physics), "
             "consensus overrides (day-ahead market ties the council 44%=44%), retro-computed "
             "lock rows (feed-latency leak — 07-04's 91°F posted late; live-only rows), or "
             "relitigating the dead ledger.")
    return L


# -------------------------------------------------------------- selftest ----

def _selftest() -> int:
    base = {"now_sgt": "2026-07-04T16:00", "pre_sunset": True,
            "lock": {"rows": 10, "settled": 8,
                     "cov": {15: {"n": 3, "mean_stated": 0.95, "hit_rate": 2 / 3, "gap": -0.28}},
                     "status": {15: "ACCRUING"},
                     "today": {"hour": 15, "modal_bucket": 32, "modal_prob": 0.97,
                               "running_max_c": 32.2}},
            "scorecard": {"Singapore": {"n": 10, "hits": 5, "rate": 0.5}},
            "twc": {"n": 2, "hits": 1}, "pop": {"dry": 0, "convective": 2},
            "dead": ["D01", "D14"]}
    out = "\n".join(brief(base))
    assert "UNCERTIFIED" in out                       # n<20 -> conviction must be labeled
    assert "NOT FINAL" in out and "post-~19:10" in out  # pre-sunset vocabulary guard fires
    assert "backtest 95%, live 2/3" in out            # exact citable sentence generated
    assert "0/15 dry" in out and "2/40 settled" in out
    assert "guaranteed" not in out.replace("'banked/floor/guaranteed'", "")  # no loose certainty
    post = dict(base, pre_sunset=False)
    assert "'final' is permitted" in "\n".join(brief(post))
    cert = dict(base)
    cert["lock"] = dict(base["lock"],
                        cov={15: {"n": 25, "mean_stated": 0.95, "hit_rate": 0.92, "gap": -0.03}},
                        status={15: "CERTIFIED"})
    assert "measured 92% (n=25)" in "\n".join(brief(cert))  # certified -> measured number only
    # directives: uninstrumented hours + anti-directives + adjudicated defect
    d_state = dict(base)
    d_state["lock"] = dict(base["lock"], recent_hours={12: 0, 13: 0, 14: 2, 15: 3, 16: 1, 18: 0})
    d = "\n".join(directives(d_state))
    assert "UNINSTRUMENTED certification hours [12, 13, 18]" in d
    assert "verdict-midday.plist" in d and "verdict-evening.plist" in d
    assert "SINGAPORE MODEL: no open defect" in d and "OUT OF SCOPE" in d
    assert "Spend NOTHING on" in d and "0/14" in d
    full = dict(base); full["lock"] = dict(base["lock"], recent_hours={h: 2 for h in (12,13,14,15,16,18)})
    assert "UNINSTRUMENTED" not in "\n".join(directives(full))
    print("eval_harness selftest PASS (uncertified label, pre/post-sunset vocab, citable "
          "sentences, certified->measured, no loose certainty words, directives: gaps/"
          "anti-directives/defect ranked)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    state = gather()
    print("\n".join(brief(state)))
    print()
    print("\n".join(directives(state)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
