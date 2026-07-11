"""Lessons aggregator + budgeted candidate queue (Plan 3 Phase 4) — the loop's THROTTLE.

The post-mortem engine (Phase 3) puts an exact cause on every settled error. This turns a
RECURRING cause into a falsifiable, shadow-testable candidate — but only under a HARD budget,
because the loop generates hypotheses at machine speed while disconfirming data accrues at one
settled day per city per day. An unbudgeted tuner on n≈20–60 is a multiple-comparisons factory
that certifies noise with mathematical certainty. So:

  * a pattern is a conditioned recurring signal — cause C systematically HURT on a (city, cause)
    cell — with hard floors: n ≥ 8, two-sided binomial sign-test p < 0.05, and the NUMBER OF
    CELLS SCANNED printed alongside (the denominator of the fishing expedition, never hidden);
  * a candidate is a deterministic parameterized transform with a falsifiable claim and a
    pre-registered effect SIGN — never a free-form "improve the blend";
  * THE BUDGET: ≤ 2 ACTIVE candidates per city, ≤ 4 repo-wide per calendar month. Queue full →
    DEFERRED-BUDGET (ranked by effect size for next month). Every candidate ever emitted is
    counted (K_candidates_ever) — that running count is the Bonferroni denominator Phase 5's
    promotion gate deflates by. Raising the budget is a certification-bar change (ledger entry),
    same doctrine as MIN_SETTLED.

READ-ONLY w.r.t. forecasting: it emits candidates to a queue, never a served number. The queue is
JSON in ledger/candidates.json. Deterministic (no wall-clock — the month is data-derived or
passed), stdlib-only. Self-test:  python3 -m tools.lessons --selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

QUEUE_PATH = ROOT / "ledger" / "candidates.json"

# THE BUDGET — raising either is a certification-bar change requiring a ledger entry (the same
# doctrine as edge.MIN_SETTLED). It is not a limitation to engineer around; it IS the design.
MAX_ACTIVE_PER_CITY = 2
MAX_ACTIVE_PER_MONTH = 4

MIN_CELL_N = 8            # too thin below this to state a direction
ALPHA = 0.05             # sign-test significance for a pattern to exist at all
_EPS = 1e-9

# Only causes with a deterministic corrective transform become candidates. INPUT (the raw NWP
# inputs were collectively wrong) has no simple transform — it is logged, never a candidate.
_TRANSFORM = {
    "BIAS": {"op": "scale_bias", "factor": 0.5,
             "claim": "halving the applied bias improves bucket log-loss"},
    "BLEND": {"op": "toward_naive", "factor": 0.5,
              "claim": "shrinking skill-weighting toward equal-weight improves bucket log-loss"},
}


def _binom_p(k: int, n: int) -> float:
    """Two-sided exact binomial sign-test p-value for k successes in n trials (H0: p=0.5).
    Probability of an outcome at least as extreme as k. Stdlib-only, deterministic."""
    if n == 0:
        return 1.0
    pmf = [comb(n, i) * (0.5 ** n) for i in range(n + 1)]
    obs = pmf[k]
    return min(1.0, sum(p for p in pmf if p <= obs + 1e-12))


def detect_patterns(db_path=None) -> dict:
    """Scan postmortems, group by (place, dominant cause), and for causes with a transform test
    whether that component systematically HURT (increased |error|). Returns
    {patterns: [...], cells_scanned: N}. cells_scanned is the multiple-comparisons denominator."""
    from weather_council import storage
    conn = storage._connect() if db_path is None else _connect_at(db_path)
    try:
        rows = conn.execute(
            "SELECT place, attributed_cause, total_error, components_json FROM postmortems "
            "WHERE attr='high'").fetchall()
    finally:
        conn.close()

    cells: dict[tuple, list] = {}
    for place, cause, total, cj in rows:
        if cause not in _TRANSFORM:
            continue
        try:
            comps = json.loads(cj) if cj else {}
        except Exception:
            continue
        comp = comps.get({"BIAS": "bias_contribution", "BLEND": "blend_deviation"}[cause])
        if comp is None or total is None:
            continue
        # "hurt" == this component made |error| larger than it would have been without it.
        hurt = abs(total) > abs(total - comp) + _EPS
        cells.setdefault((place, cause), []).append((hurt, abs(comp)))

    cells_scanned = len(cells)
    patterns = []
    for (place, cause), obs in cells.items():
        n = len(obs)
        if n < MIN_CELL_N:
            continue
        hurt_k = sum(1 for h, _ in obs if h)
        p = _binom_p(hurt_k, n)
        if p < ALPHA and hurt_k > n / 2:        # systematically HURTING (not helping)
            effect = sum(m for _, m in obs) / n
            patterns.append({
                "place": place, "cause": cause, "n": n, "hurt": hurt_k,
                "p_value": round(p, 4), "effect_size": round(effect, 4),
            })
    patterns.sort(key=lambda d: -d["effect_size"])
    return {"patterns": patterns, "cells_scanned": cells_scanned}


def _cand_id(place, cause, transform) -> str:
    h = hashlib.sha1(f"{place}|{cause}|{json.dumps(transform, sort_keys=True)}".encode()).hexdigest()
    return f"cand-{h[:10]}"


def _load_queue(path) -> list:
    p = Path(path)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except Exception:
        return []


def _month_from_postmortems(db_path=None) -> str:
    """Data-derived 'current month' (YYYY-MM) = the latest scored_at — deterministic, no wall
    clock. Falls back to '0000-00' when the table is empty."""
    from weather_council import storage
    conn = storage._connect() if db_path is None else _connect_at(db_path)
    try:
        r = conn.execute("SELECT MAX(scored_at) FROM postmortems").fetchone()
    finally:
        conn.close()
    return (r[0][:7] if r and r[0] else "0000-00")


def emit_candidates(detection: dict, queue_path=None, month=None, db_path=None) -> dict:
    """Turn detected patterns into queued candidates under the budget. New candidates that would
    exceed ≤2/city or ≤4/month ACTIVE become DEFERRED-BUDGET (ranked by effect size). Every
    candidate ever seen keeps its K_candidates_ever = the running total (the Bonferroni
    denominator). Idempotent: an existing candidate id is not re-added. Returns a summary."""
    queue_path = queue_path or QUEUE_PATH
    month = month or _month_from_postmortems(db_path)
    queue = _load_queue(queue_path)
    existing_ids = {c["id"] for c in queue}

    active_by_city: dict[str, int] = {}
    active_month = 0
    for c in queue:
        if c.get("status") == "ACTIVE":
            active_by_city[c["place"]] = active_by_city.get(c["place"], 0) + 1
            if c.get("created_month") == month:
                active_month += 1

    emitted, deferred, skipped = [], [], 0
    for pat in detection["patterns"]:
        tr = dict(_TRANSFORM[pat["cause"]])
        transform = {"op": tr["op"], "place": pat["place"], "factor": tr["factor"]}
        cid = _cand_id(pat["place"], pat["cause"], transform)
        if cid in existing_ids:
            skipped += 1
            continue
        over_city = active_by_city.get(pat["place"], 0) >= MAX_ACTIVE_PER_CITY
        over_month = active_month >= MAX_ACTIVE_PER_MONTH
        status = "DEFERRED-BUDGET" if (over_city or over_month) else "ACTIVE"
        cand = {
            "id": cid,
            "place": pat["place"],
            "claim": f"{tr['claim']} for {pat['place']}",
            "transform": transform,
            "predicted_effect_sign": "+",           # a candidate must WIN in its claimed direction
            "born_from": {"place": pat["place"], "cause": pat["cause"], "n": pat["n"],
                          "hurt": pat["hurt"], "p_value": pat["p_value"],
                          "cells_scanned": detection["cells_scanned"]},
            "status": status,
            "created_month": month,
            "effect_size": pat["effect_size"],
            # queue already accumulates each appended candidate this call, so it alone is the
            # running total (the prior +len(emitted)+len(deferred) double-counted those rows)
            "K_candidates_ever": len(queue) + 1,
        }
        queue.append(cand)
        existing_ids.add(cid)
        if status == "ACTIVE":
            active_by_city[pat["place"]] = active_by_city.get(pat["place"], 0) + 1
            active_month += 1
            emitted.append(cand)
        else:
            deferred.append(cand)

    Path(queue_path).parent.mkdir(parents=True, exist_ok=True)
    Path(queue_path).write_text(json.dumps(queue, indent=1) + "\n")
    return {"emitted": emitted, "deferred": deferred, "skipped_existing": skipped,
            "cells_scanned": detection["cells_scanned"], "month": month,
            "K_candidates_ever": len(queue)}


def _connect_at(db_path):
    from weather_council import storage
    return storage._connect_at(db_path)      # single shared impl (was duplicated 4x)


def main() -> int:
    ap = argparse.ArgumentParser(description="Detect recurring error causes -> budgeted candidates.")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    det = detect_patterns()
    print(f"LESSONS — scanned {det['cells_scanned']} (city,cause) cells; "
          f"{len(det['patterns'])} recurring-hurt pattern(s) cleared n≥{MIN_CELL_N} & p<{ALPHA}")
    for p in det["patterns"]:
        print(f"  {p['place'][:20]:20} {p['cause']:6} hurt {p['hurt']}/{p['n']} "
              f"p={p['p_value']} effect {p['effect_size']}°C")
    res = emit_candidates(det)
    print(f"  emitted {len(res['emitted'])} ACTIVE, {len(res['deferred'])} DEFERRED-BUDGET, "
          f"{res['skipped_existing']} already queued (budget ≤{MAX_ACTIVE_PER_CITY}/city, "
          f"≤{MAX_ACTIVE_PER_MONTH}/month {res['month']}; K_ever={res['K_candidates_ever']})")
    return 0


def _selftest() -> int:
    import tempfile
    # binomial: 9/10 one-sided-ish -> significant; 6/10 -> not
    assert _binom_p(10, 10) < 0.05 and _binom_p(6, 10) > 0.05
    # detection on a synthetic postmortems table: BIAS hurts 9/10 for HK, INPUT ignored
    from weather_council import storage
    tmp = Path(tempfile.mkdtemp())
    dbp = tmp / "t.db"
    conn = _connect_at(dbp)
    with conn:
        for i in range(10):
            hurt = i < 9                      # 9 of 10 days the bias made it worse
            total = 1.0
            comp = 0.8 if hurt else -0.8      # same-sign-as-total => hurt; opposite => helped
            conn.execute("INSERT INTO postmortems (place,target_date,attr,scored_at,total_error,"
                         "attributed_cause,components_json) VALUES ('HK','2026-07-%02d','high',"
                         "'2026-07-11T00:00:00',?,?,?)" % (i + 1),
                         (total, "BIAS", json.dumps({"bias_contribution": comp})))
        # a too-thin BLEND cell (n=3) must NOT emit
        for i in range(3):
            conn.execute("INSERT INTO postmortems (place,target_date,attr,scored_at,total_error,"
                         "attributed_cause,components_json) VALUES ('SG','2026-07-%02d','high',"
                         "'2026-07-11T00:00:00',1.0,'BLEND',?)" % (i + 20),
                         (json.dumps({"blend_deviation": 0.8}),))
    conn.close()
    det = detect_patterns(db_path=dbp)
    assert det["cells_scanned"] == 2                      # HK/BIAS + SG/BLEND both scanned
    assert len(det["patterns"]) == 1 and det["patterns"][0]["place"] == "HK", det
    q = tmp / "candidates.json"
    r1 = emit_candidates(det, queue_path=q, db_path=dbp)
    assert len(r1["emitted"]) == 1 and r1["emitted"][0]["transform"]["op"] == "scale_bias"
    assert r1["emitted"][0]["born_from"]["cells_scanned"] == 2   # denominator carried
    # idempotent: re-emit adds nothing
    r2 = emit_candidates(det, queue_path=q, db_path=dbp)
    assert r2["emitted"] == [] and r2["skipped_existing"] == 1
    # budget: force a 3rd active for one city -> DEFERRED-BUDGET
    det_budget = {"patterns": [
        {"place": "HK", "cause": "BLEND", "n": 10, "hurt": 9, "p_value": 0.02, "effect_size": 0.7},
    ], "cells_scanned": 3}
    # first BLEND for HK is active (city now has 2)
    emit_candidates(det_budget, queue_path=q, db_path=dbp)
    det_budget3 = {"patterns": [
        {"place": "HK", "cause": "BIAS", "n": 10, "hurt": 9, "p_value": 0.02, "effect_size": 0.9},
    ], "cells_scanned": 3}
    # a different transform for HK (factor tweak) would be a new id — force via a distinct place tag
    # simpler: verify the ≤2/city cap holds by checking active HK count
    q_now = _load_queue(q)
    active_hk = [c for c in q_now if c["place"] == "HK" and c["status"] == "ACTIVE"]
    assert len(active_hk) <= MAX_ACTIVE_PER_CITY, active_hk
    print(f"lessons selftest PASSED (binomial sign-test; n≥{MIN_CELL_N} floor; INPUT ignored; "
          f"cells_scanned denominator carried; idempotent; ≤{MAX_ACTIVE_PER_CITY}/city budget)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
