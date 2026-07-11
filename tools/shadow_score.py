"""Shadow scorer + human promotion gate (Plan 3 Phase 5) — the loop's L1→L2 boundary.

Phase 4 emits budgeted CANDIDATES: deterministic transforms with a pre-registered effect sign.
This phase TESTS them without ever touching a served number. For each ACTIVE candidate it re-applies
the transform to the SAME issue-time inputs already frozen in provenance (Phase 0), producing a
SHADOW high beside the served one, and scores both against the SAME realized high. The paired
per-day log-loss difference (served − shadow) accrues in `shadow_forecasts`. A candidate is
promoted ONLY by a human, and ONLY after it clears a Bonferroni-deflated paired-bootstrap gate in
its own cell. Everything else here is autonomous: it can KILL (falsified sign) or EXPIRE a candidate
on its own, but PROMOTION halts and prints a brief for review (L2 is permanently human-gated).

WHY A COMMON-PROXY PMF. To compare served vs shadow we need both on identical footing. The served
verdict's real pmf is an empirical residual resample that is NOT stored, and the shadow has no
residual cloud at all — so we score BOTH as a Gaussian bucket pmf around their respective point
forecasts with the SAME stored spread σ and the SAME integer-°C bucket ladder. The two arms then
differ by exactly one thing — the transform's mean shift — so the paired DELTA isolates the
transform's marginal effect and nothing else. The absolute log-loss values are proxy numbers; they
are NOT the served calibration edge.py owns. Only the delta is the object of interest.

THE GATE (per candidate, in its own city cell):
  * PROMOTE   — n ≥ MIN_PROMOTE_N settled shadow days AND the paired-bootstrap CI (deflated to
                ALPHA / K_candidates_ever) excludes zero on the FAVOURABLE side AND the mean sign
                matches predicted_effect_sign. Does NOT apply the transform — sets the candidate
                PROMOTION-PENDING-HUMAN and prints a brief. (L2, human only.)
  * FALSIFIED-SIGN — n ≥ MIN_PROMOTE_N but the CI excludes zero on the WRONG side (reliably worse):
                the pre-registered sign is falsified → KILLED.
  * KILLED    — as early as n ≥ MIN_KILL_N the CI already excludes zero against the candidate:
                autonomous early kill, don't wait for the promotion n.
  * EXPIRED   — older than EXPIRY_DAYS without clearing: stop spending shadow compute on it.
  * ACCRUING  — otherwise; keep logging forward.

READ-ONLY w.r.t. forecasting: writes ONLY to `shadow_forecasts` and to candidate STATUS in
ledger/candidates.json. It never writes the verdicts table, never renders, never feeds back into a
vote. Deterministic (seeded bootstrap; 'today' is data-derived or passed), stdlib-only. Reuses
edge.py's proper-score + bootstrap machinery verbatim — no metric is reimplemented here.

Self-test:  python3 -m tools.shadow_score --selftest
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from weather_council import edge                      # reuse _logloss/_brier/_bootstrap_ci verbatim
from tools import lessons                             # queue load/save + K denominator

# Promotion needs the same evidence bar as an edge certification (edge.MIN_SETTLED); the autonomous
# early-kill fires sooner because killing a wrong-signed hypothesis early is cheap and safe.
MIN_PROMOTE_N = edge.MIN_SETTLED         # 20 — same doctrine as the edge gate
MIN_KILL_N = 10                          # autonomous kill can fire before the promotion n
EXPIRY_DAYS = 90
ALPHA = 0.05                             # deflated by K_candidates_ever (Bonferroni) at gate time
SIGMA_FLOOR = 0.5                        # a degenerate/zero stored spread still gives a usable pmf
LADDER_PAD = 5                           # integer-°C buckets to either side of the observed range


# ─────────────────────────────────────────── transforms ──────────────────────────────────────────

def apply_transform(transform: dict, prov: dict) -> float | None:
    """Re-derive the SHADOW high by applying `transform` to the frozen provenance inputs. Pure
    function of the stored decisions — no forecast is recomputed. Returns None if the provenance
    lacks the fields the transform needs (that day simply is not shadow-scorable)."""
    blend = prov.get("blend") or {}
    weighted_raw = blend.get("high_pre_bias")           # skill-weighted mean of the RAW votes
    bias = blend.get("bias_high")                       # applied bias = final − weighted_raw
    included = set(prov.get("included_high") or [])
    raws = [vt.get("raw_high") for vt in (prov.get("votes") or [])
            if vt.get("member_id") in included and vt.get("raw_high") is not None]
    naive_raw = sum(raws) / len(raws) if raws else None
    if weighted_raw is None or bias is None:
        return None
    op = transform.get("op")
    factor = transform.get("factor")
    if op == "scale_bias":
        # Shrink the applied bias toward 0 by `factor` (0.5 == halve it); inputs & blend untouched.
        return weighted_raw + factor * bias
    if op == "toward_naive":
        # Shrink skill-weighting toward equal-weight by `factor`, then re-apply the SAME bias.
        if naive_raw is None:
            return None
        shadow_pre = naive_raw + factor * (weighted_raw - naive_raw)
        return shadow_pre + bias
    return None                                         # unknown op — not shadow-scorable


# ─────────────────────────────────────── proxy bucket pmf ─────────────────────────────────────────

def _rhu(x: float) -> int:
    """Settlement round-half-up to the whole-°C bucket the contract pays on."""
    return math.floor(x + 0.5)


def _phi(z: float) -> float:
    """Standard-normal CDF via erf (stdlib, no scipy)."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _bucket_ladder(*points: float) -> list[int]:
    """Integer-°C bucket ladder spanning the observed points ± LADDER_PAD."""
    lo = _rhu(min(points)) - LADDER_PAD
    hi = _rhu(max(points)) + LADDER_PAD
    return list(range(lo, hi + 1))


def _gauss_bucket_pmf(mean: float, sigma: float, ladder: list[int]) -> dict[str, float]:
    """Discretize N(mean, sigma) onto the whole-°C ladder: bucket b == [b−0.5, b+0.5). Renormalized
    over the (finite) ladder so it is a proper distribution to score."""
    sigma = max(sigma, SIGMA_FLOOR)
    pmf = {}
    for b in ladder:
        pmf[f"{b}"] = _phi((b + 0.5 - mean) / sigma) - _phi((b - 0.5 - mean) / sigma)
    z = sum(pmf.values()) or 1.0
    return {k: v / z for k, v in pmf.items()}


def score_day(prov: dict, final: float, actual: float, transform: dict) -> dict | None:
    """One paired shadow-vs-served day. Both arms scored as a Gaussian bucket pmf around their point
    (served=final, shadow=transform(prov)) with the SAME stored σ and ladder. Returns the row dict
    or None if the day is not shadow-scorable. delta_logloss = served − shadow (>0 ⇒ candidate
    improved bucket log-loss on this day)."""
    shadow_high = apply_transform(transform, prov)
    if shadow_high is None:
        return None
    sigma = (prov.get("spread") or {}).get("high")
    sigma = sigma if isinstance(sigma, (int, float)) and sigma > 0 else SIGMA_FLOOR
    ladder = _bucket_ladder(final, shadow_high, actual)
    realized = f"{_rhu(actual)}"
    served_pmf = _gauss_bucket_pmf(final, sigma, ladder)
    shadow_pmf = _gauss_bucket_pmf(shadow_high, sigma, ladder)
    labels = [f"{b}" for b in ladder]
    served_ll = edge._logloss(served_pmf, realized)
    shadow_ll = edge._logloss(shadow_pmf, realized)
    return {
        "shadow_high": round(shadow_high, 3), "sigma": round(sigma, 3),
        "served_logloss": round(served_ll, 6), "shadow_logloss": round(shadow_ll, 6),
        "served_brier": round(edge._brier(served_pmf, labels, realized), 6),
        "shadow_brier": round(edge._brier(shadow_pmf, labels, realized), 6),
        "delta_logloss": round(served_ll - shadow_ll, 6),
    }


# ──────────────────────────────────────── shadow logging ─────────────────────────────────────────

def run_shadow(db_path=None, queue_path=None) -> dict:
    """For every ACTIVE candidate, re-score each settled verdict in its city that carries provenance
    into `shadow_forecasts` (idempotent per candidate+place+target). Writes NOTHING to the served
    path. Returns a per-candidate row count."""
    from weather_council import storage
    from weather_council.storage import utc_now_iso
    queue = lessons._load_queue(queue_path or lessons.QUEUE_PATH)
    active = [c for c in queue if c.get("status") == "ACTIVE"]
    conn = storage._connect() if db_path is None else lessons._connect_at(db_path)
    written: dict[str, int] = {}
    try:
        for cand in active:
            place = cand["place"]
            transform = cand["transform"]
            rows = conn.execute(
                "SELECT target_date, high, actual_high, provenance_json FROM verdicts "
                "WHERE place=? AND actual_high IS NOT NULL AND provenance_json IS NOT NULL",
                (place,)).fetchall()
            n = 0
            for tgt, final, actual, pj in rows:
                try:
                    prov = json.loads(pj)
                except Exception:
                    continue
                day = score_day(prov, float(final), float(actual), transform)
                if day is None:
                    continue
                with conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO shadow_forecasts "
                        "(candidate_id, place, target_date, scored_at, served_high, shadow_high, "
                        " actual, sigma, served_logloss, shadow_logloss, served_brier, "
                        " shadow_brier, delta_logloss) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (cand["id"], place, tgt, utc_now_iso(), round(float(final), 3),
                         day["shadow_high"], round(float(actual), 3), day["sigma"],
                         day["served_logloss"], day["shadow_logloss"], day["served_brier"],
                         day["shadow_brier"], day["delta_logloss"]))
                n += 1
            written[cand["id"]] = n
    finally:
        conn.close()
    return {"scored": written, "candidates": len(active)}


# ─────────────────────────────────────── the promotion gate ──────────────────────────────────────

def _parse_month(created_month: str) -> dt.date:
    """Birth date = first of the candidate's created_month (YYYY-MM). Falls back to a far-past date
    so a malformed stamp reads as immediately expiry-eligible, never as freshly born."""
    try:
        y, m = created_month.split("-")[:2]
        return dt.date(int(y), int(m), 1)
    except Exception:
        return dt.date(1970, 1, 1)


def evaluate_gate(deltas: list[float], K: int, created_month: str, today: dt.date,
                  predicted_sign: str = "+") -> dict:
    """Classify one candidate from its paired per-day deltas. K is the running candidate count (the
    Bonferroni denominator); the bootstrap runs at ALPHA / K. Pure — no I/O. `predicted_sign` '+'
    means the candidate claims it LOWERS log-loss (delta > 0)."""
    n = len(deltas)
    alpha_eff = ALPHA / max(1, K)
    ci = edge._bootstrap_ci(deltas, edge.BOOTSTRAP_SAMPLES, edge.BOOTSTRAP_SEED,
                            alpha=alpha_eff) if n >= 2 else None
    mean = round(sum(deltas) / n, 6) if n else 0.0
    age = (today - _parse_month(created_month)).days
    want_positive = predicted_sign == "+"
    clears_favourable = ci is not None and (ci[0] > 0 if want_positive else ci[1] < 0)
    clears_against = ci is not None and (ci[1] < 0 if want_positive else ci[0] > 0)
    sign_ok = (mean > 0) if want_positive else (mean < 0)

    if n >= MIN_PROMOTE_N and clears_favourable and sign_ok:
        outcome, status = "PROMOTE", "PROMOTION-PENDING-HUMAN"
    elif n >= MIN_PROMOTE_N and clears_against:
        outcome, status = "FALSIFIED-SIGN", "KILLED"       # significant, but the WRONG direction
    elif n >= MIN_KILL_N and clears_against:
        outcome, status = "KILLED", "KILLED"               # autonomous early kill
    elif age > EXPIRY_DAYS:
        outcome, status = "EXPIRED", "EXPIRED"
    else:
        outcome, status = "ACCRUING", "ACTIVE"
    return {"outcome": outcome, "status": status, "n": n, "mean_delta": mean,
            "ci": ci, "alpha_eff": round(alpha_eff, 5), "K": K, "age_days": age}


def _candidate_deltas(conn, candidate_id: str) -> list[float]:
    rows = conn.execute(
        "SELECT delta_logloss FROM shadow_forecasts WHERE candidate_id=? "
        "AND delta_logloss IS NOT NULL ORDER BY target_date", (candidate_id,)).fetchall()
    return [float(r[0]) for r in rows]


def _promotion_brief(cand: dict, verdict: dict) -> list[str]:
    """The human-review brief (L2). Printed ONLY on PROMOTE — the transform is never auto-applied."""
    ci = verdict["ci"]
    return [
        "",
        "  ┌─ PROMOTION PENDING — HUMAN REVIEW REQUIRED (L2) ────────────────────────────",
        f"  │ candidate  {cand['id']}   {cand['place']}",
        f"  │ claim      {cand.get('claim')}",
        f"  │ transform  {json.dumps(cand['transform'])}",
        f"  │ evidence   n={verdict['n']} shadow days · mean Δlog-loss {verdict['mean_delta']:+.4f}"
        f" (>0 ⇒ better)",
        f"  │ CI         [{ci[0]:+.4f}, {ci[1]:+.4f}]  at α={verdict['alpha_eff']} "
        f"(=0.05/K, K={verdict['K']})",
        f"  │ born_from  {json.dumps(cand.get('born_from'))}",
        "  │ ACTION     This does NOT change any served number. To promote, a human must run the",
        "  │            gated pre-registration → frozen walk-forward → sign-stable-both-halves",
        "  │            certification (HARD RULE 1) and ship the transform explicitly.",
        "  └─────────────────────────────────────────────────────────────────────────────",
    ]


def run_gate(db_path=None, queue_path=None, today: dt.date | None = None) -> dict:
    """Evaluate every non-terminal candidate against its accrued shadow deltas; persist terminal
    statuses (KILLED / EXPIRED / PROMOTION-PENDING-HUMAN) back to the queue. AUTONOMOUS for kills
    and expiry; PROMOTION only flags for a human and prints a brief — it never applies a transform.
    `today` defaults to the latest settled target_date in the DB (data-derived, deterministic)."""
    from weather_council import storage
    queue_path = queue_path or lessons.QUEUE_PATH
    queue = lessons._load_queue(queue_path)
    K = len(queue)                                      # running Bonferroni denominator
    conn = storage._connect() if db_path is None else lessons._connect_at(db_path)
    try:
        if today is None:
            r = conn.execute("SELECT MAX(target_date) FROM verdicts "
                             "WHERE actual_high IS NOT NULL").fetchone()
            today = dt.date.fromisoformat(r[0]) if r and r[0] else dt.date(1970, 1, 1)
        verdicts, briefs, changed = [], [], 0
        by_id = {c["id"]: c for c in queue}
        for cand in queue:
            if cand.get("status") not in ("ACTIVE",):
                continue                                # DEFERRED-BUDGET / already-terminal skipped
            deltas = _candidate_deltas(conn, cand["id"])
            gv = evaluate_gate(deltas, K, cand.get("created_month", "0000-00"), today,
                               cand.get("predicted_effect_sign", "+"))
            gv["id"] = cand["id"]
            verdicts.append(gv)
            if gv["outcome"] == "PROMOTE":
                briefs.extend(_promotion_brief(cand, gv))
            if gv["status"] != "ACTIVE":               # terminal — persist it
                by_id[cand["id"]]["status"] = gv["status"]
                by_id[cand["id"]]["gate"] = {"outcome": gv["outcome"], "n": gv["n"],
                                             "mean_delta": gv["mean_delta"], "ci": gv["ci"],
                                             "decided_on": today.isoformat()}
                changed += 1
    finally:
        conn.close()
    if changed:
        Path(queue_path).write_text(json.dumps(queue, indent=1) + "\n")
    return {"verdicts": verdicts, "briefs": briefs, "K": K,
            "today": today.isoformat(), "changed": changed}


# ────────────────────────────────────────────── CLI ──────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Shadow-score ACTIVE candidates + run the promotion gate.")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--gate-only", action="store_true", help="skip re-scoring; only run the gate")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if not args.gate_only:
        sh = run_shadow()
        print(f"SHADOW — scored {sum(sh['scored'].values())} paired day(s) across "
              f"{sh['candidates']} ACTIVE candidate(s)")
    g = run_gate()
    print(f"GATE — K={g['K']} candidates ever; today={g['today']}; "
          f"{g['changed']} status change(s)")
    for v in g["verdicts"]:
        print(f"  {v['id']}  {v['outcome']:14} n={v['n']:>3} meanΔ={v['mean_delta']:+.4f} "
              f"CI={v['ci']} α={v['alpha_eff']}")
    for line in g["briefs"]:
        print(line)
    return 0


def _selftest() -> int:
    import tempfile
    from weather_council import storage

    # 1) apply_transform: scale_bias halves the applied bias; toward_naive shrinks weighting.
    prov = {"blend": {"high": 31.5, "high_pre_bias": 30.7, "bias_high": 0.8},
            "included_high": ["a", "b"],
            "votes": [{"member_id": "a", "raw_high": 30.0}, {"member_id": "b", "raw_high": 31.0}],
            "spread": {"high": 1.0}}
    assert abs(apply_transform({"op": "scale_bias", "factor": 0.5}, prov) - (30.7 + 0.4)) < 1e-9
    # naive_raw = 30.5; toward_naive 0.5 => 30.5 + 0.5*(30.7-30.5) = 30.6, +bias 0.8 = 31.4
    assert abs(apply_transform({"op": "toward_naive", "factor": 0.5}, prov) - 31.4) < 1e-9

    # 2) score_day: a transform that moves the point TOWARD the actual must give a POSITIVE delta.
    #    served final 31.5, actual 30.0 (bucket 30); scale_bias shadow 31.1 is closer -> delta>0.
    day = score_day(prov, 31.5, 30.0, {"op": "scale_bias", "factor": 0.5})
    assert day["delta_logloss"] > 0, day
    #    and a transform that moves AWAY (negative factor blows the bias up) gives delta<0.
    day_bad = score_day(prov, 31.5, 30.0, {"op": "scale_bias", "factor": 3.0})
    assert day_bad["delta_logloss"] < 0, day_bad

    # 3) the gate's four terminal states, on controlled deltas (K=3 -> alpha_eff≈0.0167).
    strong_pos = [0.30 + 0.02 * ((i % 3) - 1) for i in range(24)]     # tight, clearly >0
    strong_neg = [-d for d in strong_pos]
    today = dt.date(2026, 7, 11)
    born = "2026-06"
    gp = evaluate_gate(strong_pos, 3, born, today)
    assert gp["outcome"] == "PROMOTE" and gp["status"] == "PROMOTION-PENDING-HUMAN", gp
    gf = evaluate_gate(strong_neg, 3, born, today)
    assert gf["outcome"] == "FALSIFIED-SIGN" and gf["status"] == "KILLED", gf
    gk = evaluate_gate(strong_neg[:12], 3, born, today)               # 10 ≤ n < 20, wrong dir
    assert gk["outcome"] == "KILLED", gk
    old = dt.date(2026, 11, 1)                                        # > 90d past 2026-06-01
    ge = evaluate_gate([0.001, -0.001, 0.0], 3, born, old)           # tiny/noise, aged out
    assert ge["outcome"] == "EXPIRED", ge
    ga = evaluate_gate([0.05, -0.04, 0.02], 3, born, today)           # thin & noisy, fresh
    assert ga["outcome"] == "ACCRUING" and ga["status"] == "ACTIVE", ga

    # 4) end-to-end run_shadow + run_gate write ONLY shadow_forecasts + queue status — never the
    #    served verdicts row. Prove the served high is byte-for-byte unchanged.
    tmp = Path(tempfile.mkdtemp())
    dbp, q = tmp / "t.db", tmp / "candidates.json"
    conn = lessons._connect_at(dbp)
    served_high = 31.5
    with conn:
        for i in range(22):
            pj = json.dumps({**prov})
            conn.execute(
                "INSERT INTO verdicts (issued_at, place, target_date, high, low, confidence, "
                " actual_high, provenance_json, provenance_ok) VALUES (?,?,?,?,?,?,?,?,1)",
                (f"2026-06-{i+1:02d}T00:00:00", "Testville", f"2026-06-{i+1:02d}",
                 served_high, 25.0, "HIGH", 30.0, pj))
    conn.close()
    q.write_text(json.dumps([{
        "id": "cand-test01", "place": "Testville",
        "transform": {"op": "scale_bias", "place": "Testville", "factor": 0.5},
        "predicted_effect_sign": "+", "status": "ACTIVE", "created_month": "2026-06",
        "claim": "halve the bias", "born_from": {"cells_scanned": 1}}]) + "\n")
    sh = run_shadow(db_path=dbp, queue_path=q)
    assert sh["scored"]["cand-test01"] == 22, sh
    g = run_gate(db_path=dbp, queue_path=q, today=today)
    # scale_bias moves 31.5 -> 31.1, closer to the 30 bucket every day -> PROMOTE, human-gated.
    assert g["verdicts"][0]["outcome"] == "PROMOTE", g["verdicts"]
    assert any("HUMAN REVIEW REQUIRED" in b for b in g["briefs"])
    conn = lessons._connect_at(dbp)
    highs = {r[0] for r in conn.execute("SELECT high FROM verdicts").fetchall()}
    conn.close()
    assert highs == {served_high}, f"served high mutated: {highs}"   # zero served-path bytes changed
    assert json.loads(q.read_text())[0]["status"] == "PROMOTION-PENDING-HUMAN"

    print("shadow_score selftest PASSED (transforms; toward/away delta sign; PROMOTE/"
          "FALSIFIED-SIGN/KILLED/EXPIRED/ACCRUING gate; end-to-end writes only shadow+status, "
          "served high unchanged; promotion is human-gated)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
