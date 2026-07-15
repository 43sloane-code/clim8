"""Improvement analyzer — evidence-based, gate-respecting "what to do next".

Reads the LIVE record (storage.live_bucket_scorecard), the realized money picture
(paper_pnl), the model-vs-market edge (edge), and the dead-candidate ledger, then
prints an honest system status + GATED next-steps. Encodes the project's hard rule:
it NEVER proposes anything already in the dead ledger, and when the live sample is
too small to gate a change it says so instead of inventing a lever.

This is a completeness critic, not an idea generator: the only "improvement" it
ever endorses unprompted is "accrue more settled days" until the sample can gate.

    PYTHONPATH=. python3 tools/improvement_analyzer.py
    PYTHONPATH=. python3 tools/improvement_analyzer.py --propose "add AIFS as a member"
    PYTHONPATH=. python3 tools/improvement_analyzer.py --selftest
"""
from __future__ import annotations
import argparse
import json
import os

CITIES = {"Manila, Philippines": "Manila", "Singapore, Singapore": "Singapore",
          "London, United Kingdom": "London"}   # the tracked basket (London added 2026-07-15;
                                                # its omission blinded the live gate counts)
LIVE_N_TARGET = 20          # settled days/city before a live rate can gate anything
BACKTEST_DAYAHEAD = 0.54    # the backtested day-ahead bucket-hit (the optimistic ref)


def _wilson(hits: int, n: int, z: float = 1.96):
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = hits / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (p, max(0.0, c - h), min(1.0, c + h))


def analyze_misses(recent: list) -> dict:
    """recent: [(date, served_bucket, settled_bucket, hit)]. Classify the failure
    mode — off-by-one (boundary / σ-ceiling, intraday-resolvable) vs gross (a
    point-accuracy / feed problem), and the directional bias (under vs over-call)."""
    misses = [(sv, tr) for _d, sv, tr, h in recent if not h]
    off1 = sum(1 for sv, tr in misses if abs(sv - tr) == 1)
    return {
        "misses": len(misses),
        "off_by_one": off1,
        "gross": len(misses) - off1,
        "under": sum(1 for sv, tr in misses if sv < tr),   # served below settled
        "over": sum(1 for sv, tr in misses if sv > tr),
    }


def load_dead(repo: str) -> list:
    path = os.path.join(repo, "ledger", "dead_candidates.jsonl")
    out = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    except FileNotFoundError:
        pass
    return out


def check_proposal(text: str, dead: list):
    """Return the dead-ledger entry a proposal collides with (via its grep terms),
    or None. This is the gate that stops re-litigating closed candidates."""
    t = (text or "").lower()
    for c in dead:
        for term in c.get("grep", []):
            if term and term.lower() in t:
                return c
    return None


def _recommendations(per_city: dict, miss: dict, dead: list, pnl: dict) -> list:
    """The honest, evidence-gated next-steps. Refuses dead levers; defaults to
    'accrue' when the sample can't gate."""
    recs = []
    n_total = sum(c["n"] for c in per_city.values())
    confirmed = all(c["n"] >= LIVE_N_TARGET for c in per_city.values())
    if not confirmed:
        recs.append(f"ACCRUE — only {n_total} settled day(s) across the basket "
                    f"(need {LIVE_N_TARGET}/city). No change is gateable yet; the one "
                    f"honest action is to keep the daily loop running.")
    # failure-mode read
    if miss["misses"]:
        if miss["off_by_one"] >= miss["gross"]:
            recs.append("Misses are mostly OFF-BY-ONE (boundary / σ-ceiling): day-ahead "
                        "is information-capped. The resolving lever is INTRADAY (post-peak), "
                        "NOT a day-ahead corrector — those are DEAD in the ledger.")
        else:
            recs.append("Misses are mostly GROSS (≥2 buckets): a point-accuracy/feed issue, "
                        "not a boundary problem — check the feed/regime before anything else.")
        if miss["under"] >= 2 and miss["under"] > 2 * max(miss["over"], 1):
            recs.append("Persistent UNDER-call bias observed — BUT day-ahead bias correctors "
                        "are DEAD/bucket-neutral in the ledger (sub-bucket-scale, hurt CRPS). "
                        "Flag it, don't re-add a corrector; re-test only with a frozen A/B if n grows.")
    # money read
    if pnl.get("scored", 0) >= 5:
        mh, kh = pnl.get("model_hit_rate") or 0, pnl.get("market_hit_rate") or 0
        if kh >= mh:
            recs.append(f"No tradeable edge: market names the bucket ≥ model "
                        f"({kh*100:.0f}% vs {mh*100:.0f}%). Abstain; don't fade the market.")
    recs.append(f"Closed-candidate guard: {len(dead)} dead levers on file — run "
                f"`--propose \"<idea>\"` before building anything to avoid relitigating them.")
    return recs


def main() -> int:
    ap = argparse.ArgumentParser(description="Evidence-based improvement analyzer.")
    ap.add_argument("--repo", default=os.environ.get("WX_REPO", "."))
    ap.add_argument("--propose", help="check an idea against the dead-candidate ledger")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()

    dead = load_dead(args.repo)

    if args.propose:
        hit = check_proposal(args.propose, dead)
        if hit:
            print(f"❌ DEAD — matches {hit['id']} \"{hit['candidate']}\" "
                  f"({hit['verdict']}): {hit['evidence']}\n   Do NOT relitigate.")
        else:
            print("◻ Not in the dead ledger. UNVALIDATED — gate it before trusting: "
                  "frozen-A/B (record/replay) + disjoint-fold sign-stability on CRPS AND "
                  "bucket-hit, must clear the run-to-run noise floor on BOTH halves.")
        return 0

    import sys
    sys.path.insert(0, os.path.abspath(args.repo))
    from weather_council import storage

    print("IMPROVEMENT ANALYZER — system status + gated next-steps")
    print("=" * 66)
    per_city, all_recent = {}, []
    print("LIVE PERFORMANCE (served vs the contract's own settled bucket):")
    for label, short in CITIES.items():
        sc = storage.live_bucket_scorecard(label)
        per_city[short] = {"n": sc["n"], "hits": sc["hits"]}
        p, lo, hi = _wilson(sc["hits"], sc["n"])
        tag = "CONFIRMED" if sc["n"] >= LIVE_N_TARGET else f"unproven (n={sc['n']}/{LIVE_N_TARGET})"
        vs = (f" vs backtest ~{BACKTEST_DAYAHEAD*100:.0f}%"
              if sc["n"] else "")
        print(f"  {short:10} {sc['hits']}/{sc['n']} = {p*100:4.0f}% [{lo*100:.0f},{hi*100:.0f}]{vs} — {tag}")
        all_recent += sc.get("recent", [])

    miss = analyze_misses(all_recent)
    print(f"\nFAILURE MODE (n={miss['misses']} misses): "
          f"off-by-one {miss['off_by_one']} | gross {miss['gross']} | "
          f"under-call {miss['under']} | over-call {miss['over']}")

    print("\nMONEY (realized paper P&L vs market — the edge question):")
    try:
        from tools.paper_pnl import simulate, _load_from_db
        pnl = simulate(_load_from_db())
        print(f"  {pnl['scored']} settled market days | model hit {pnl['model_hit_rate'] and round(pnl['model_hit_rate']*100)}% "
              f"vs market {pnl['market_hit_rate'] and round(pnl['market_hit_rate']*100)}% | "
              f"Brier {pnl['model_brier'] and round(pnl['model_brier'],3)} vs {pnl['market_brier'] and round(pnl['market_brier'],3)} | "
              f"robust P&L {simulate(_load_from_db(), floor=0.15)['model_pnl']:+.1f}u")
    except Exception as e:
        pnl = {}
        print(f"  (paper_pnl unavailable: {e})")

    print("\nGATED NEXT-STEPS (honest; dead levers excluded):")
    for i, r in enumerate(_recommendations(per_city, miss, dead, pnl), 1):
        print(f"  {i}. {r}")
    return 0


def _selftest() -> int:
    # off-by-one + under-call detection
    recent = [("d1", 31, 32, False), ("d2", 32, 33, False), ("d3", 31, 31, True),
              ("d4", 30, 34, False)]   # 3 misses: 2 off-by-one (both under), 1 gross
    m = analyze_misses(recent)
    assert m == {"misses": 3, "off_by_one": 2, "gross": 1, "under": 3, "over": 0}, m
    # proposal vs dead ledger
    dead = [{"id": "D02", "candidate": "AIFS member", "verdict": "DEAD",
             "evidence": "noise", "grep": ["aifs", "ecmwf_aifs025"]}]
    assert check_proposal("add AIFS as a 9th member", dead)["id"] == "D02"
    assert check_proposal("try a kalman filter", dead) is None
    # recommendations refuse dead levers + default to accrue on thin n
    recs = _recommendations({"Manila": {"n": 3, "hits": 1}, "Singapore": {"n": 5, "hits": 4}},
                            m, dead, {"scored": 0})
    assert any("ACCRUE" in r for r in recs), recs
    assert any("DEAD" in r for r in recs), recs
    print("improvement_analyzer self-test PASSED (miss classification; dead-ledger guard; accrue default)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
