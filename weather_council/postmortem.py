"""Post-mortem engine (Plan 3 Phase 3) — decompose every settled error into exact components.

For each settled verdict that carried issue-time provenance (Phase 0), split the HIGH forecast
error into three components that TELESCOPE exactly to the total, plus a settlement diagnostic:

    input_error       = naive-equal-weight raw consensus − actual   (were the raw inputs wrong?)
    blend_deviation   = weighted raw blend − naive raw              (did skill-weighting help/hurt?)
    bias_contribution = final − weighted raw blend                  (did the applied bias help/hurt?)
    ───────────────────────────────────────────────────────────────
    input + blend + bias  ==  final − actual  (== total error), asserted every run.

    settlement_divergence = actual − contract-settled bucket        (anchor vs payout; SEPARATE)

An identity that does not close means the provenance or the math is wrong, and a WRONG
attribution is worse than none — so that row is ABORTED, not stored. Attribution taxonomy
(strict precedence): SETTLEMENT (model right, contract paid a different bucket — no forecast fix
helps) → NONE-WITHIN-BUCKET (missed continuously but stayed in the right bucket) → the dominant
of INPUT/BLEND/BIAS when it exceeds 50% of the total and the miss crossed a bucket boundary →
MIXED. Rows without provenance are UNATTRIBUTABLE-PREPROVENANCE — counted, never guessed.

READ-ONLY: a diagnosis, never a served number. Deterministic, stdlib-only.
Self-test:  python3 -m weather_council.postmortem
"""
from __future__ import annotations

__all__ = ["EPS", "IdentityError", "build_postmortem", "run_postmortems",
           "attribution_histogram"]

import json

from .sources import _round_half_up

EPS = 1e-6


class IdentityError(ValueError):
    """Raised when the decomposed components do not sum to the total error — the attribution
    cannot be trusted, so no row is written (a wrong attribution is worse than none)."""


def build_postmortem(final: float, actual: float, prov: dict,
                     pm_resolved_bucket: int | None = None) -> dict:
    """Decompose one settled HIGH error off stored provenance `prov` (Phase 0 blob). Returns the
    row dict. Raises IdentityError if input+blend+bias does not equal final−actual (± EPS)."""
    blend = prov.get("blend") or {}
    weighted_raw = blend.get("high_pre_bias")          # weighted mean of the RAW votes
    bias = blend.get("bias_high")                      # = final − weighted_raw
    included = set(prov.get("included_high") or [])
    raws = [vt.get("raw_high") for vt in (prov.get("votes") or [])
            if vt.get("member_id") in included and vt.get("raw_high") is not None]
    naive_raw = sum(raws) / len(raws) if raws else None
    if weighted_raw is None or bias is None or naive_raw is None:
        raise IdentityError("provenance missing pre_bias / bias / raw votes")

    input_error = naive_raw - actual
    blend_deviation = weighted_raw - naive_raw
    bias_contribution = bias                            # the STORED applied bias (Σ w·(cor−raw))
    total_error = final - actual
    # The identity closes ONLY if the stored bias is consistent with final − weighted_raw; a
    # corrupt provenance blob (mismatched pre_bias / bias) fails here and aborts the row.
    if abs((input_error + blend_deviation + bias_contribution) - total_error) > 1e-4:
        raise IdentityError(
            f"components {input_error:+.4f}+{blend_deviation:+.4f}+{bias_contribution:+.4f} "
            f"!= total {total_error:+.4f}")

    verdict_bucket, actual_bucket = _round_half_up(final), _round_half_up(actual)
    crossed = verdict_bucket != actual_bucket
    margin = round(0.5 - abs(final - verdict_bucket), 4)   # small == fragile (near a boundary)

    settlement_div = None
    settlement_crossed = False
    if pm_resolved_bucket is not None:
        settlement_div = round(actual - pm_resolved_bucket, 4)
        settlement_crossed = pm_resolved_bucket != actual_bucket   # anchor ≠ contract payout

    # settlement_divergence is NOT a telescoping error component — it lives only in its own
    # top-level field/column (was also duplicated into comps/components_json).
    comps = {
        "input_error": round(input_error, 4),
        "blend_deviation": round(blend_deviation, 4),
        "bias_contribution": round(bias_contribution, 4),
    }
    cause = _attribute(comps, total_error, crossed, settlement_crossed)
    return {
        "final": round(final, 3), "actual": round(actual, 3),
        "total_error": round(total_error, 4),
        "attributed_cause": cause, "components": comps,
        "margin": margin, "crossed_boundary": int(crossed),
        "settlement_divergence": settlement_div,
        "pipeline_version": prov.get("pipeline_version"),
    }


def _attribute(comps: dict, total: float, crossed: bool, settlement_crossed: bool) -> str:
    """Taxonomy in STRICT precedence — first match wins."""
    if settlement_crossed:                 # the model was right, the contract paid another bucket
        return "SETTLEMENT"                # (no forecasting improvement can fix this — ALARM)
    if not crossed:                        # continuous miss, but the served bucket was correct
        return "NONE-WITHIN-BUCKET"
    if abs(total) < EPS:
        return "MIXED"
    named = {"INPUT": comps["input_error"], "BLEND": comps["blend_deviation"],
             "BIAS": comps["bias_contribution"]}
    dom = max(named, key=lambda k: abs(named[k]))
    if abs(named[dom]) > 0.5 * abs(total):
        return dom
    return "MIXED"


def run_postmortems(db_path=None) -> dict:
    """Decompose every settled HIGH verdict that carries provenance; upsert into `postmortems`
    (idempotent per place+target). Returns a summary incl. the UNATTRIBUTABLE-PREPROVENANCE count
    (settled rows with no provenance — counted, never guessed). Read-only w.r.t. forecasting."""
    from . import storage
    from .storage import utc_now_iso
    conn = storage._connect() if db_path is None else storage._connect_at(db_path)
    scored, aborted, preprov = 0, 0, 0
    by_cause: dict[str, int] = {}
    try:
        rows = conn.execute(
            "SELECT place, target_date, high, actual_high, provenance_json "
            "FROM verdicts WHERE actual_high IS NOT NULL").fetchall()
        for place, tgt, final, actual, pj in rows:
            if pj is None:
                preprov += 1
                continue
            try:
                prov = json.loads(pj)
            except Exception:
                aborted += 1
                continue
            pm_bucket = _pm_resolved_bucket(conn, place, tgt)
            try:
                pm = build_postmortem(float(final), float(actual), prov, pm_bucket)
            except IdentityError:
                aborted += 1          # identity did not close -> no row (wrong attr worse than none)
                continue
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO postmortems "
                    "(place, target_date, attr, scored_at, final, actual, total_error, "
                    " attributed_cause, components_json, margin, crossed_boundary, "
                    " settlement_divergence, pipeline_version) "
                    "VALUES (?,?, 'high', ?,?,?,?,?,?,?,?,?,?)",
                    (place, tgt, utc_now_iso(), pm["final"], pm["actual"], pm["total_error"],
                     pm["attributed_cause"], json.dumps(pm["components"]), pm["margin"],
                     pm["crossed_boundary"], pm["settlement_divergence"], pm["pipeline_version"]))
            scored += 1
            by_cause[pm["attributed_cause"]] = by_cause.get(pm["attributed_cause"], 0) + 1
    finally:
        conn.close()
    return {"scored": scored, "aborted": aborted,
            "unattributable_preprovenance": preprov, "by_cause": by_cause}


def _pm_resolved_bucket(conn, place, target) -> int | None:
    """The contract's own settled bucket (int) from market_snapshots.pm_resolved_label, if any."""
    try:
        r = conn.execute(
            "SELECT pm_resolved_label FROM market_snapshots WHERE place=? AND target_date=? "
            "AND pm_resolved_label IS NOT NULL LIMIT 1", (place, target)).fetchone()
    except Exception:
        return None
    if not r or not r[0]:
        return None
    import re
    m = re.search(r"-?\d+", str(r[0]))
    return int(m.group()) if m else None


def attribution_histogram(db_path=None, hours=None) -> dict:
    """{cause: count} over the postmortems table (all, or last `hours`). Read-only summary the
    healthcheck surfaces; SETTLEMENT is always an ALARM tier there."""
    from . import storage
    conn = storage._connect() if db_path is None else storage._connect_at(db_path)
    try:
        q = "SELECT attributed_cause, COUNT(*) FROM postmortems"
        args = ()
        if hours is not None:
            import datetime as dt
            cutoff = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
                      - dt.timedelta(hours=hours)).isoformat(timespec="seconds")
            q += " WHERE scored_at >= ?"
            args = (cutoff,)
        q += " GROUP BY attributed_cause"
        rows = conn.execute(q, args).fetchall()
    finally:
        conn.close()
    return {c: n for c, n in rows}


def _selftest() -> None:
    # A provenance blob: 2 raw votes 30.0/31.0 (naive 30.5), weighted-raw 30.7, bias +0.8 -> final 31.5
    prov = {"pipeline_version": "t", "included_high": ["a", "b"],
            "votes": [{"member_id": "a", "raw_high": 30.0}, {"member_id": "b", "raw_high": 31.0}],
            "blend": {"high_pre_bias": 30.7, "bias_high": 0.8}}
    # actual 30.0 -> total error +1.5; input = 30.5-30 = +0.5, blend = 30.7-30.5 = +0.2, bias = +0.8
    pm = build_postmortem(31.5, 30.0, prov)
    c = pm["components"]
    assert c["input_error"] == 0.5 and c["blend_deviation"] == 0.2 and c["bias_contribution"] == 0.8
    assert abs(c["input_error"] + c["blend_deviation"] + c["bias_contribution"] - 1.5) < 1e-9
    assert pm["crossed_boundary"] == 1                        # 31.5 vs 30 bucket -> crossed
    # BIAS is the dominant component (0.8 > 0.5*1.5=0.75) -> attributed BIAS
    assert pm["attributed_cause"] == "BIAS", pm["attributed_cause"]

    # within-bucket miss (final 30.4, actual 30.1 -> both bucket 30) -> NONE-WITHIN-BUCKET
    pm2 = build_postmortem(30.4, 30.1, {**prov, "blend": {"high_pre_bias": 29.9, "bias_high": 0.5}})
    assert pm2["crossed_boundary"] == 0 and pm2["attributed_cause"] == "NONE-WITHIN-BUCKET"

    # settlement divergence: model bucket == actual bucket (31) but contract paid 30 -> SETTLEMENT
    pm3 = build_postmortem(31.2, 31.0, {**prov, "blend": {"high_pre_bias": 30.5, "bias_high": 0.7}},
                           pm_resolved_bucket=30)
    assert pm3["attributed_cause"] == "SETTLEMENT" and pm3["settlement_divergence"] == 1.0

    # identity violation aborts
    try:
        build_postmortem(31.5, 30.0, {**prov, "blend": {"high_pre_bias": 99.0, "bias_high": 0.8}})
        raise AssertionError("expected IdentityError")
    except IdentityError:
        pass

    # MIXED: crossed, no component > 50% (each ~1/3 of the total)
    prov_m = {"pipeline_version": "t", "included_high": ["a", "b"],
              "votes": [{"member_id": "a", "raw_high": 30.4}, {"member_id": "b", "raw_high": 30.6}],
              "blend": {"high_pre_bias": 30.8, "bias_high": 0.4}}
    pm4 = build_postmortem(31.2, 30.3, prov_m)   # total 0.9; input 0.2, blend 0.3, bias 0.4 — none>0.45
    assert pm4["crossed_boundary"] == 1 and pm4["attributed_cause"] == "MIXED", pm4["attributed_cause"]
    print("postmortem selftest PASSED (telescoping identity; BIAS/NONE-WITHIN-BUCKET/SETTLEMENT/"
          "MIXED taxonomy; identity-violation abort)")


if __name__ == "__main__":
    _selftest()
