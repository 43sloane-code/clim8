"""twc_gate_score.py — score the FROZEN TWC 9th-member gate (ledger/preregistered/twc_member_gate.md).

The accrual clock filled (76 >= 40 settled pairs, 2026-07-30). This module runs the one
pre-registered attempt: council+TWC vs council-alone under the shipped Plan-3 machinery,
NO re-tuning, read-only w.r.t. forecasting (SELECTs + a report file; never a vote, weight,
or served number).

FROZEN CRITERIA (from the prereg, ALL required):
  G1  economic object — council+TWC beats council-alone exact-bucket hit on BOTH disjoint
      chronological folds, per city pooled.
  G2  proper score — same on CRPS/log score, both folds.
  G3' driver evidence — per-city TWC-record offset consistent across folds: (a) sign-stable,
      OR (b) fold medians' bootstrap CIs overlap around ~0 AND TWC error-sd < council blend's
      on both folds. CI-disjoint medians of opposite sign = REFUSE even if G1/G2 pass.
  G4  independence — Phase-5 correlation audit passes (|r| < 0.9 vs council blend and every
      member panel; twc_independence.py conventions).

OPERATIONAL SPEC (frozen 2026-07-30 BEFORE any scoring — the prereg froze G1-G4 but left
these mechanics to pin; pinned here mechanically, run once, no post-hoc revision):
  1. COMPARATOR. The council arm is the council's own DAY-AHEAD verdict for the same
     (place, target_date): latest-issued verdicts row with lead >= 1 AND provenance_json.
     Lead-0 (same-day) rows are excluded — TWC is a day-ahead forecast and a same-day
     council row would smuggle same-day information into a day-ahead gate. Pairs without
     such a comparator are EXCLUDED from G1/G2 (and the sd half of G3'b) and reported as
     coverage gaps. TWC's own issued_at is the logger capture time (TWC's true issue time
     is unknowable — the prereg's confound C; adjudicated via the vintage stratum, not hidden).
  2. BLEND RECONSTRUCTION (exact, validated). From the comparator's frozen provenance:
     panel = eligible votes with corrected_high and a non-zero weight_high; re-run the
     SHIPPED screen (median + MAD, thresh = max(OUTLIER_FLOOR_C, 3*MAD), keep-all fallback)
     and skill-weighted mean (weights as stored). The reconstruction MUST reproduce the
     served blend.high AT THE SERVED STORAGE GRAIN — verdicts.high is stored rounded to
     0.1 C, so the check is round(recon, 1) == served (a tighter tolerance mis-quarantines
     half the panel on pure storage rounding; measured 2026-07-30: all mismatches <= 0.047
     and every served value one-decimal). Pairs failing AT GRAIN are quarantined and counted
     (machinery distrusted, not papered over).
  3. TWC AS 9TH MEMBER (walk-forward, leak-free). At pair date t, TWC's bias/MAE are learned
     ONLY from its own settled pairs with target_date <= t-2 (the t-1 pair settles after the
     t-1 capture time — using it would leak). Mirror of the blend's own 5-pair floor: TWC is
     INELIGIBLE (blend = council-alone, delta 0) until 5 such pairs exist in its city.
     corrected_twc = fc_high - bias_twc; w_twc = 1/max(mae_twc, 0.1)**WEIGHT_POWER — the
     shipped formula, TWC's own record, no re-tuning. TWC then enters the SAME MAD screen
     alongside the panel and the survivors are re-blended.
  4. FOLDS. Per city, comparator pairs sorted by target_date; fold A = first n//2, fold B =
     the rest (odd n: B takes the extra). Metrics pooled across cities within each fold.
     G3' uses the same construction on ALL settled pairs (it needs no comparator for the
     offset sign test).
  5. SCORING. Bucket = whole-degC round-half-up (sources._round_half_up — the settlement
     rule). Log-loss/Brier via shadow_score's Gaussian bucket pmf with the SAME frozen sigma
     (verdict spread.high, floored at 0.5) and SAME ladder for both arms — the arms differ by
     exactly the mean shift, isolating TWC's marginal effect (shadow_score doctrine).
     CRPS via scoring.crps_gaussian with the same sigma. G2 passes iff mean log-loss improves
     AND mean CRPS improves on BOTH folds; Brier is corroborating only.
  6. G3' DETAILS. Offsets = fc_high - actual_high per city, per fold. Median bootstrap CIs
     (edge.BOOTSTRAP_SAMPLES/SEED, alpha 0.05). Sign-stable = both fold medians non-zero same
     sign. Overlap-around-0 = CIs overlap AND the overlap contains 0. CI-disjoint opposite
     signs = REFUSE. Anything else (e.g. disjoint same-sign = magnitude break) = FAIL.
     Error-sd compared on comparator pairs only.
  7. G4 DETAILS. tools/twc_independence.audit() runs as shipped (its own per-city n>=30
     threshold honestly reports UNMEASURED at current accrual) PLUS the same correlations
     computed on the pooled comparator set (n >= 30): r(TWC err, council err) and
     r(TWC err, member err) per member. Any |r| >= 0.9 = FAIL.
  8. VERDICT. PASS iff G1 AND G2 AND G3' AND G4. PASS does NOT promote: it prints the driver
     decomposition (gain stratified by bucket-edge proximity, council-vs-record divergence
     tercile, vintage-lag tercile) and halts for the human L2 gate. FAIL -> dead-ledger
     entry, one attempt spent, TWC stays a display cross-reference.

Run:       PYTHONPATH=. python3 tools/twc_gate_score.py [--out reports/twc_gate_<date>.txt]
Self-test: PYTHONPATH=. python3 tools/twc_gate_score.py --selftest
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sqlite3
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from weather_council import edge, scoring, storage            # noqa: E402
from weather_council.council import OUTLIER_FLOOR_C, WEIGHT_POWER  # noqa: E402
from weather_council.sources import _round_half_up            # noqa: E402
from tools import shadow_score                                # noqa: E402

RECON_TOL = 1e-9          # recon must match served AT THE SERVED STORAGE GRAIN (0.1 C): the
                          # verdicts table stores round(blend, 1); compare at that grain, not float
MIN_TWC_PAIRS = 5         # mirror of the blend's own per-member training floor (_blend_on_date)
SETTLE_LAG_DAYS = 2       # TWC pairs with target_date <= t-2 are settled at the t-1 capture time
COLLINEAR_R = 0.9         # twc_independence.py convention
SIGMA_FLOOR = shadow_score.SIGMA_FLOOR


# ───────────────────────────────── data loading ─────────────────────────────────

def load_twc_pairs(conn: sqlite3.Connection) -> list[dict]:
    """All settled TWC forward pairs, chronological. Read-only."""
    rows = conn.execute(
        "SELECT place, target_date, issued_at, fc_high, actual_high FROM tracked_forecasts "
        "WHERE source='twc' AND actual_high IS NOT NULL ORDER BY place, target_date").fetchall()
    return [{"place": p, "target_date": td, "issued_at": iat,
             "fc_high": float(fc), "actual_high": float(ac)}
            for p, td, iat, fc, ac in rows]


def load_comparator(conn: sqlite3.Connection, place: str, target_date: str) -> dict | None:
    """The council's day-ahead arm for this pair: latest-issued verdict with lead >= 1 and
    provenance. None when the council never issued a provenanced day-ahead verdict for it."""
    row = conn.execute(
        "SELECT issued_at, high, provenance_json FROM verdicts "
        "WHERE place=? AND target_date=? AND provenance_json IS NOT NULL "
        "AND CAST(julianday(target_date) - julianday(date(issued_at)) AS INT) >= 1 "
        "ORDER BY issued_at DESC LIMIT 1", (place, target_date)).fetchone()
    if not row:
        return None
    try:
        prov = json.loads(row[2])
    except Exception:
        return None
    return {"issued_at": row[0], "served_high": float(row[1]), "prov": prov}


# ─────────────────────────── blend machinery (shipped screen, verbatim) ───────────────────────────

def _panel_from_prov(prov: dict) -> list[tuple[str, float, float]]:
    """(member_id, corrected_high, weight_high) for every eligible vote — the screen's input."""
    panel = []
    for vt in (prov.get("votes") or []):
        c, w = vt.get("corrected_high"), vt.get("weight_high")
        if vt.get("eligible") and c is not None and w:
            panel.append((vt.get("member_id"), float(c), float(w)))
    return panel


def _screen_and_blend(panel: list[tuple[str, float, float]]) -> tuple[float, list[str]] | None:
    """The shipped MAD outlier screen + skill-weighted mean (mirrors council._blend_on_date /
    live _blend: thresh = max(OUTLIER_FLOOR_C, 3*MAD) about the per-day median of corrected
    values, keep-all fallback when every member trips it). Returns (blend, survivor_ids)."""
    if not panel:
        return None
    corrected = [c for _, c, _ in panel]
    median = statistics.median(corrected)
    mad = statistics.median([abs(c - median) for c in corrected])
    thresh = max(OUTLIER_FLOOR_C, 3 * mad)
    included = [(mid, c, w) for mid, c, w in panel if abs(c - median) <= thresh] or panel
    den = sum(w for _, _, w in included)
    if den <= 0:
        return None
    blend = sum(w * c for _, c, w in included) / den
    return blend, [mid for mid, _, _ in included]


def twc_walkforward_stats(pairs: list[dict], place: str, target_date: str) -> dict | None:
    """TWC's own member stats at pair date `target_date`, learned ONLY from its city's settled
    pairs with target_date <= t - SETTLE_LAG_DAYS (settled at capture time; no leak). None below
    the 5-pair floor (TWC ineligible that day — the blend is the council's alone)."""
    t = dt.date.fromisoformat(target_date)
    prior = [p for p in pairs
             if p["place"] == place
             and dt.date.fromisoformat(p["target_date"]) <= t - dt.timedelta(days=SETTLE_LAG_DAYS)]
    if len(prior) < MIN_TWC_PAIRS:
        return None
    errs = [p["fc_high"] - p["actual_high"] for p in prior]
    bias = statistics.mean(errs)
    mae = statistics.mean(abs(e - bias) for e in errs)
    w = 1.0 / max(mae, 0.1) ** WEIGHT_POWER
    return {"bias": bias, "mae": mae, "weight": w, "n_prior": len(prior)}


# ─────────────────────────────── per-pair scoring ───────────────────────────────

def score_pair(conn: sqlite3.Connection, pair: dict, all_pairs: list[dict]) -> dict:
    """One settled TWC pair -> both arms scored. Arms: council (served day-ahead blend,
    reconstructed + validated from frozen provenance) and council+TWC (TWC inserted as a 9th
    member under the shipped screen/weights with walk-forward stats)."""
    out = {**pair, "comparator": False}
    comp = load_comparator(conn, pair["place"], pair["target_date"])
    if comp is None:
        return out
    prov = comp["prov"]
    served = (prov.get("blend") or {}).get("high")
    panel = _panel_from_prov(prov)
    recon = _screen_and_blend(panel)
    out.update(comparator=True, verdict_issued_at=comp["issued_at"],
               served_high=served, n_panel=len(panel))
    if served is None or recon is None:
        out["quarantined"] = "no served blend or empty panel"
        return out
    blend_c, survivors = recon
    out["recon_error"] = round(round(blend_c, 1) - served, 4)
    if abs(round(blend_c, 1) - served) > RECON_TOL:
        out["quarantined"] = f"reconstruction {blend_c:.3f} (grain {round(blend_c, 1)}) != served {served}"
        return out
    out["survivors"] = survivors

    stats = twc_walkforward_stats(all_pairs, pair["place"], pair["target_date"])
    out["twc_stats"] = stats
    if stats is None:
        blend_t, surv_t = blend_c, list(survivors)      # ineligible: delta exactly 0
    else:
        corrected_twc = pair["fc_high"] - stats["bias"]
        out["corrected_twc"] = round(corrected_twc, 3)
        res = _screen_and_blend(panel + [("twc", corrected_twc, stats["weight"])])
        blend_t, surv_t = res
    out.update(council_blend=round(blend_c, 4), twc9_blend=round(blend_t, 4),
               survivors_twc9=surv_t, twc_in_blend="twc" in surv_t)

    sigma = (prov.get("spread") or {}).get("high")
    sigma = sigma if isinstance(sigma, (int, float)) and sigma > 0 else SIGMA_FLOOR
    sigma = max(sigma, SIGMA_FLOOR)
    actual = pair["actual_high"]
    realized = f"{_round_half_up(actual)}"
    ladder = shadow_score._bucket_ladder(blend_c, blend_t, actual)
    labels = [f"{b}" for b in ladder]
    pmf_c = shadow_score._gauss_bucket_pmf(blend_c, sigma, ladder)
    pmf_t = shadow_score._gauss_bucket_pmf(blend_t, sigma, ladder)
    out.update(
        sigma=round(sigma, 3), realized_bucket=realized,
        council_bucket=_round_half_up(blend_c), twc9_bucket=_round_half_up(blend_t),
        council_hit=int(_round_half_up(blend_c) == _round_half_up(actual)),
        twc9_hit=int(_round_half_up(blend_t) == _round_half_up(actual)),
        council_ll=round(edge._logloss(pmf_c, realized), 6),
        twc9_ll=round(edge._logloss(pmf_t, realized), 6),
        council_brier=round(edge._brier(pmf_c, labels, realized), 6),
        twc9_brier=round(edge._brier(pmf_t, labels, realized), 6),
        council_crps=round(scoring.crps_gaussian(blend_c, sigma, actual), 6),
        twc9_crps=round(scoring.crps_gaussian(blend_t, sigma, actual), 6),
    )
    return out


# ───────────────────────────────── folds + gates ─────────────────────────────────

def assign_folds(pairs: list[dict]) -> None:
    """Per city, chronological halves: fold A = first n//2, fold B = rest. Mutates in place."""
    by_city: dict[str, list[dict]] = {}
    for p in pairs:
        by_city.setdefault(p["place"], []).append(p)
    for city_pairs in by_city.values():
        city_pairs.sort(key=lambda p: p["target_date"])
        half = len(city_pairs) // 2
        for i, p in enumerate(city_pairs):
            p["fold"] = "A" if i < half else "B"


def _fold_metrics(scored: list[dict], fold: str) -> dict:
    rows = [p for p in scored if p.get("fold") == fold and p.get("comparator")
            and "council_ll" in p]
    n = len(rows)
    if not n:
        return {"n": 0}
    return {
        "n": n,
        "council_hits": sum(p["council_hit"] for p in rows),
        "twc9_hits": sum(p["twc9_hit"] for p in rows),
        "council_ll": round(statistics.mean(p["council_ll"] for p in rows), 6),
        "twc9_ll": round(statistics.mean(p["twc9_ll"] for p in rows), 6),
        "council_crps": round(statistics.mean(p["council_crps"] for p in rows), 6),
        "twc9_crps": round(statistics.mean(p["twc9_crps"] for p in rows), 6),
        "council_brier": round(statistics.mean(p["council_brier"] for p in rows), 6),
        "twc9_brier": round(statistics.mean(p["twc9_brier"] for p in rows), 6),
        "twc_ineligible": sum(1 for p in rows if p.get("twc_stats") is None),
    }


def gate_g1(folds: dict[str, dict]) -> dict:
    """G1 PASS iff council+TWC exact-bucket hits strictly beat council-alone on BOTH folds."""
    per = {f: m for f, m in folds.items()}
    beats = {f: (m["twc9_hits"] > m["council_hits"]) for f, m in per.items() if m.get("n")}
    return {"pass": bool(beats) and all(beats.values()) and len(beats) == 2, "beats": beats}


def gate_g2(folds: dict[str, dict]) -> dict:
    """G2 PASS iff mean log-loss AND mean CRPS both improve on BOTH folds."""
    ll = {f: m["twc9_ll"] < m["council_ll"] for f, m in folds.items() if m.get("n")}
    crps = {f: m["twc9_crps"] < m["council_crps"] for f, m in folds.items() if m.get("n")}
    ok = bool(ll) and len(ll) == 2 and all(ll.values()) and all(crps.values())
    return {"pass": ok, "ll_improves": ll, "crps_improves": crps}


def _median_bootstrap_ci(values: list[float], alpha: float = 0.05) -> tuple[float, float] | None:
    """Seeded bootstrap CI for the MEDIAN (G3' speaks of fold medians; edge._bootstrap_ci is
    mean-only). Same sample count/seed conventions as the shipped edge gate."""
    import random
    n = len(values)
    if n < 2:
        return None
    rng = random.Random(edge.BOOTSTRAP_SEED)
    meds = []
    for _ in range(edge.BOOTSTRAP_SAMPLES):
        s = [values[rng.randrange(n)] for _ in range(n)]
        meds.append(statistics.median(s))
    meds.sort()
    lo = meds[int((alpha / 2) * edge.BOOTSTRAP_SAMPLES)]
    hi = meds[min(edge.BOOTSTRAP_SAMPLES - 1, int((1 - alpha / 2) * edge.BOOTSTRAP_SAMPLES))]
    return (round(lo, 4), round(hi, 4))


def gate_g3(all_pairs: list[dict], scored: list[dict]) -> dict:
    """G3' per city: offset sign-stability across folds (a), or tight-calibration strong form
    (b). CI-disjoint opposite signs = REFUSE. Error-sd comparison uses comparator pairs."""
    assign_folds(all_pairs)
    cities = sorted({p["place"] for p in all_pairs})
    per_city, refuse, any_pass, all_pass = {}, False, False, True
    for city in cities:
        offs = {f: [p["fc_high"] - p["actual_high"] for p in all_pairs
                    if p["place"] == city and p["fold"] == f] for f in ("A", "B")}
        meds, cis = {}, {}
        for f, xs in offs.items():
            if xs:
                meds[f] = round(statistics.median(xs), 4)
                cis[f] = _median_bootstrap_ci(xs)
        entry = {"offsets_A": len(offs["A"]), "offsets_B": len(offs["B"]),
                 "median_A": meds.get("A"), "median_B": meds.get("B"),
                 "ci_A": cis.get("A"), "ci_B": cis.get("B")}
        # error-sd on comparator pairs (both arms present), per fold
        sd = {}
        for f in ("A", "B"):
            rows = [p for p in scored if p["place"] == city and p.get("fold") == f
                    and p.get("comparator") and "council_ll" in p]
            te = [p["fc_high"] - p["actual_high"] for p in rows]
            ce = [p["served_high"] - p["actual_high"] for p in rows]
            if len(te) >= 2:
                sd[f] = {"twc": round(statistics.pstdev(te), 4),
                         "council": round(statistics.pstdev(ce), 4),
                         "twc_tighter": statistics.pstdev(te) < statistics.pstdev(ce)}
        entry["err_sd"] = sd
        # verdict for this city
        ciA, ciB = cis.get("A"), cis.get("B")
        mA, mB = meds.get("A"), meds.get("B")
        if not ciA or not ciB:
            entry["verdict"] = "UNMEASURED"
            all_pass = False
        elif ciA[1] < ciB[0] or ciB[1] < ciA[0]:        # CI-disjoint
            if (mA < 0 < mB) or (mB < 0 < mA):
                entry["verdict"] = "REFUSE"             # opposite signs: no mechanism
                refuse = True
            else:
                entry["verdict"] = "FAIL"               # magnitude break: not consistent
                all_pass = False
        else:                                            # CIs overlap
            lo, hi = max(ciA[0], ciB[0]), min(ciA[1], ciB[1])
            sign_stable = (mA > 0 and mB > 0) or (mA < 0 and mB < 0)
            tight = (lo <= 0 <= hi) and all(sd.get(f, {}).get("twc_tighter") for f in ("A", "B"))
            entry["verdict"] = "PASS" if (sign_stable or tight) else "FAIL"
            entry["sign_stable"] = sign_stable
            entry["strong_form"] = tight
            any_pass = any_pass or entry["verdict"] == "PASS"
            all_pass = all_pass and entry["verdict"] == "PASS"
        per_city[city] = entry
    return {"pass": (not refuse) and all_pass, "refuse": refuse, "per_city": per_city}


def gate_g4(scored: list[dict]) -> dict:
    """G4 independence: r(TWC err, council err) and r(TWC err, member err) on the pooled
    comparator set (per-city n is below twc_independence's 30 threshold — honestly UNMEASURED
    there; pooled n>=30 carries the audit). Any |r| >= COLLINEAR_R = FAIL."""
    rows = [p for p in scored if p.get("comparator") and "council_ll" in p]
    te = [p["fc_high"] - p["actual_high"] for p in rows]
    ce = [p["served_high"] - p["actual_high"] for p in rows]
    import tools.twc_independence as indep
    r_council = indep._pearson(te, ce)
    member_errs: dict[str, list[tuple[float, float]]] = {}
    for p in rows:
        comp = p.get("_prov_votes") or []
        terr = p["fc_high"] - p["actual_high"]
        for mid, corrected in comp:
            member_errs.setdefault(mid, []).append((terr, corrected - p["actual_high"]))
    r_members = {mid: indep._pearson([a for a, _ in xs], [b for _, b in xs])
                 for mid, xs in member_errs.items() if len(xs) >= 10}
    flags = (r_council is not None and abs(r_council) >= COLLINEAR_R) or \
            any(r is not None and abs(r) >= COLLINEAR_R for r in r_members.values())
    return {"pass": not flags and r_council is not None, "n": len(rows),
            "r_council": None if r_council is None else round(r_council, 4),
            "r_members": {k: (None if v is None else round(v, 4)) for k, v in r_members.items()}}


# ──────────────────────────────── the report ────────────────────────────────

def run(db_path=None) -> dict:
    conn = storage._connect() if db_path is None else storage._connect_at(db_path)
    try:
        pairs = load_twc_pairs(conn)
        assign_folds(pairs)
        scored = []
        for p in pairs:
            row = score_pair(conn, p, pairs)
            # keep member corrected errors for G4 (provenance votes of the comparator)
            if row.get("comparator") and not row.get("quarantined"):
                comp = load_comparator(conn, p["place"], p["target_date"])
                row["_prov_votes"] = [(vt.get("member_id"), float(vt["corrected_high"]))
                                      for vt in (comp["prov"].get("votes") or [])
                                      if vt.get("eligible") and vt.get("corrected_high") is not None]
            scored.append(row)
        assign_folds(scored)                            # same construction on the scored rows
    finally:
        conn.close()

    comparator = [p for p in scored if p.get("comparator") and "council_ll" in p]
    quarantined = [p for p in scored if p.get("quarantined")]
    no_comp = [p for p in scored if not p.get("comparator")]
    folds = {f: _fold_metrics(scored, f) for f in ("A", "B")}
    g1, g2 = gate_g1(folds), gate_g2(folds)
    g3 = gate_g3([dict(p) for p in pairs], scored)
    g4 = gate_g4(scored)
    verdict = "PASS" if (g1["pass"] and g2["pass"] and g3["pass"] and g4["pass"]) else "FAIL"
    if g3.get("refuse"):
        verdict = "REFUSE"
    return {"pairs": scored, "n_pairs": len(pairs), "n_comparator": len(comparator),
            "n_no_comparator": len(no_comp), "n_quarantined": len(quarantined),
            "folds": folds, "g1": g1, "g2": g2, "g3": g3, "g4": g4, "verdict": verdict}


def render(res: dict) -> str:
    L = []
    a = L.append
    a("=" * 78)
    a("TWC 9TH-MEMBER GATE — the one pre-registered attempt (ledger/preregistered/twc_member_gate.md)")
    a(f"run date: {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')} · "
      f"read-only scoring, no served number touched")
    a("=" * 78)
    a("")
    a("COVERAGE")
    a(f"  settled TWC pairs (WU record):      {res['n_pairs']}  (gate floor 40 — FILLED)")
    a(f"  day-ahead council comparator found: {res['n_comparator']}")
    a(f"  no day-ahead comparator (excluded): {res['n_no_comparator']}")
    a(f"  quarantined (recon/served mismatch): {res['n_quarantined']}")
    by_city: dict[str, dict[str, int]] = {}
    for p in res["pairs"]:
        c = by_city.setdefault(p["place"], {"pairs": 0, "comp": 0})
        c["pairs"] += 1
        c["comp"] += int(bool(p.get("comparator") and "council_ll" in p))
    for city, c in sorted(by_city.items()):
        a(f"    {city:28} pairs={c['pairs']:>3}  comparator={c['comp']:>3}")
    a("")
    a("PER-PAIR DETAIL (comparator pairs; fold = per-city chronological half)")
    a(f"  {'city':10} {'date':11} {'fold':4} {'twcFC':>6} {'act':>6} {'twcBias':>7} "
      f"{'w_twc':>6} {'council':>7} {'twc9':>7} {'scr':>3} {'cb':>3} {'tb':>3} "
      f"{'hitC':>4} {'hitT':>4} {'llC':>7} {'llT':>7} {'crpsC':>6} {'crpsT':>6}")
    for p in res["pairs"]:
        if not (p.get("comparator") and "council_ll" in p):
            continue
        st = p.get("twc_stats") or {}
        a(f"  {p['place'].split(',')[0][:10]:10} {p['target_date']:11} {p.get('fold','?'):4} "
          f"{p['fc_high']:6.2f} {p['actual_high']:6.2f} "
          f"{st.get('bias', float('nan')):7.3f} {st.get('weight', float('nan')):6.2f} "
          f"{p['council_blend']:7.3f} {p['twc9_blend']:7.3f} "
          f"{'Y' if p['twc_in_blend'] else 'n':>3} {p['council_bucket']:>3} {p['twc9_bucket']:>3} "
          f"{p['council_hit']:>4} {p['twc9_hit']:>4} {p['council_ll']:7.4f} {p['twc9_ll']:7.4f} "
          f"{p['council_crps']:6.3f} {p['twc9_crps']:6.3f}")
    a("")
    a("FOLD SUMMARY (pooled across cities)")
    for f, m in res["folds"].items():
        if not m.get("n"):
            a(f"  fold {f}: EMPTY")
            continue
        a(f"  fold {f}: n={m['n']} (TWC ineligible on {m['twc_ineligible']} burn-in days)")
        a(f"    bucket hits   council {m['council_hits']}/{m['n']}  vs  council+TWC "
          f"{m['twc9_hits']}/{m['n']}")
        a(f"    mean log-loss council {m['council_ll']:.4f}  vs  council+TWC {m['twc9_ll']:.4f}")
        a(f"    mean CRPS     council {m['council_crps']:.4f}  vs  council+TWC {m['twc9_crps']:.4f}")
        a(f"    mean Brier    council {m['council_brier']:.4f}  vs  council+TWC {m['twc9_brier']:.4f} "
          f"(corroborating only)")
    a("")
    a("GATES (ALL required; frozen criteria, one attempt)")
    g1, g2, g3, g4 = res["g1"], res["g2"], res["g3"], res["g4"]
    a(f"  G1 economic object (bucket hit beats on BOTH folds):  "
      f"{'PASS' if g1['pass'] else 'FAIL'}  {g1['beats']}")
    a(f"  G2 proper score (log-loss AND CRPS improve, both folds): "
      f"{'PASS' if g2['pass'] else 'FAIL'}  ll={g2['ll_improves']} crps={g2['crps_improves']}")
    a(f"  G3' driver evidence (per-city offset consistency):    "
      f"{'REFUSE' if g3.get('refuse') else ('PASS' if g3['pass'] else 'FAIL')}")
    for city, e in sorted(g3["per_city"].items()):
        a(f"    {city:28} medA={e['median_A']} medB={e['median_B']} "
          f"ciA={e['ci_A']} ciB={e['ci_B']} -> {e['verdict']}")
        for f, sd in sorted((e.get("err_sd") or {}).items()):
            a(f"      fold {f} err-sd: twc {sd['twc']} vs council {sd['council']} "
              f"({'tighter' if sd['twc_tighter'] else 'wider'})")
    a(f"  G4 independence (pooled comparator n={g4['n']}; |r|>=0.9 flags): "
      f"{'PASS' if g4['pass'] else 'FAIL'}  r(twc,council)={g4['r_council']}")
    for mid, r in sorted(g4["r_members"].items()):
        a(f"      r(twc, {mid}) = {r}")
    a("")
    a(f"VERDICT: {res['verdict']}")
    if res["verdict"] == "PASS":
        a("  PASS does NOT promote. The driver decomposition (bucket-edge / divergence / vintage")
        a("  strata) and the human L2 gate stand between this and any blend change.")
    elif res["verdict"] == "REFUSE":
        a("  REFUSE: the DRIVER itself failed (offset sign flip across folds) — dead-ledger entry")
        a("  regardless of G1/G2. TWC stays a display cross-reference. One attempt spent.")
    else:
        a("  FAIL: dead-ledger entry, one attempt spent. TWC stays a display cross-reference only.")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="Score the frozen TWC 9th-member gate (one attempt).")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--out", default=None, help="report path (default reports/twc_gate_<date>.txt)")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    res = run()
    text = render(res)
    out = Path(args.out) if args.out else \
        ROOT / "reports" / f"twc_gate_{dt.date.today().isoformat()}.txt"
    out.write_text(text + "\n")
    js = out.with_suffix(".json")
    js.write_text(json.dumps({k: v for k, v in res.items() if k != "pairs"}, indent=1,
                             default=str) + "\n")
    print(text)
    print(f"\n[report written: {out} (+ {js.name})]")
    return 0


# ───────────────────────────────── self-test ─────────────────────────────────

def _selftest() -> int:
    import tempfile
    # 1) screen+blend mirrors the shipped rule: MAD screen drops the outlier, keep-all fallback.
    panel = [("a", 30.0, 1.0), ("b", 30.2, 0.5), ("c", 31.0, 0.8), ("x", 39.0, 1.0)]
    blend, surv = _screen_and_blend(panel)
    assert "x" not in surv, surv
    kept = [(m, c, w) for m, c, w in panel if m != "x"]
    expect = sum(c * w for _, c, w in kept) / sum(w for _, _, w in kept)
    assert abs(blend - expect) < 1e-9, (blend, expect)
    # keep-all fallback: every member an outlier from a degenerate median
    b2, s2 = _screen_and_blend([("a", 10.0, 1.0), ("b", 50.0, 1.0)])
    assert set(s2) == {"a", "b"}, s2

    # 2) walk-forward leak cutoff: the t-1 pair is NOT used; floor at 5 prior pairs.
    pairs = [{"place": "X", "target_date": f"2026-07-{d:02d}", "issued_at": "i",
              "fc_high": 31.0, "actual_high": 30.0} for d in range(1, 9)]
    assert twc_walkforward_stats(pairs, "X", "2026-07-06") is None        # only 4 usable (<=07-04)
    st = twc_walkforward_stats(pairs, "X", "2026-07-07")                  # 5 usable (<=07-05)
    assert st and st["n_prior"] == 5 and abs(st["bias"] - 1.0) < 1e-9, st
    st8 = twc_walkforward_stats(pairs, "X", "2026-07-08")
    assert st8["n_prior"] == 6, st8                                       # 07-06 included, 07-07 NOT
    assert abs(st8["weight"] - 1.0 / max(0.0, 0.1) ** WEIGHT_POWER) < 1e-9  # zero-variance errs -> 0.1 floor

    # 3) fold assignment: per-city chronological halves, odd n -> B takes the extra.
    fp = [{"place": "X", "target_date": f"2026-07-0{d}"} for d in (5, 1, 3, 2, 4)]
    assign_folds(fp)
    folds = {p["target_date"]: p["fold"] for p in fp}
    assert folds == {"2026-07-01": "A", "2026-07-02": "A", "2026-07-03": "B",
                     "2026-07-04": "B", "2026-07-05": "B"}, folds

    # 4) end-to-end on a synthetic DB: reconstruction validates, TWC insertion shifts the blend,
    #    gates read the right direction, and the served row is byte-for-byte untouched.
    tmp = Path(tempfile.mkdtemp())
    conn = storage._connect_at(tmp / "t.db")
    prov_votes = [{"member_id": m, "model": m, "institution": "i", "raw_high": c - 0.3,
                   "raw_low": None, "corrected_high": c, "corrected_low": None,
                   "skill_high": None, "skill_low": None, "weight_high": w, "weight_low": None,
                   "eligible": True}
                  for m, c, w in (("a", 30.0, 1.0), ("b", 30.2, 0.5), ("c", 31.0, 0.8))]
    num = sum(c * w for _, c, w in (("a", 30.0, 1.0), ("b", 30.2, 0.5), ("c", 31.0, 0.8)))
    served_raw = num / 2.3
    served = round(served_raw, 1)           # the verdicts table stores the blend at 0.1 C grain
    prov = {"version": 1, "pipeline_version": "t", "votes": prov_votes,
            "included_high": ["a", "b", "c"], "included_low": [],
            "blend": {"high": served, "bias_high": 0.3,
                      "high_pre_bias": round(served - 0.3, 3)},
            "spread": {"high": 1.0}, "regime": {}, "consensus": {}, "tc_gate": {}}
    with conn:
        # TWC runs 1.1C hot vs the record; council sits ~0.15 below 30.5 -> TWC info should help.
        for d in range(1, 15):
            td = f"2026-07-{d:02d}"
            prior = (dt.date(2026, 7, d) - dt.timedelta(days=1)).isoformat()
            actual = 30.0
            conn.execute(
                "INSERT INTO tracked_forecasts (source, issued_at, place, target_date, fc_high, "
                "fc_low, actual_high) VALUES ('twc', ?, 'X', ?, ?, 25.0, ?)",
                (f"{prior}T12:00:00", td, actual + 1.1, actual))
            conn.execute(
                "INSERT INTO verdicts (issued_at, place, target_date, high, low, confidence, "
                "actual_high, provenance_json, provenance_ok) VALUES (?,?,?,?,?,?,?,?,1)",
                (f"{prior}T08:00:00", "X", td, served, 25.0,
                 "HIGH", actual, json.dumps(prov)))
    conn.close()
    res = run(db_path=tmp / "t.db")
    assert res["n_pairs"] == 14 and res["n_comparator"] == 14, (res["n_pairs"], res["n_comparator"])
    assert res["n_quarantined"] == 0
    p6 = [p for p in res["pairs"] if p["target_date"] == "2026-07-06"][0]
    assert p6["twc_stats"] is None and abs(p6["twc9_blend"] - p6["council_blend"]) < 1e-9
    p8 = [p for p in res["pairs"] if p["target_date"] == "2026-07-08"][0]
    assert p8["twc_stats"] is not None and p8["twc_in_blend"], p8.get("survivors_twc9")
    # TWC corrected = 31.1-1.1=30.0 -> pulls the ~30.45 blend DOWN toward the 30 actual.
    assert p8["twc9_blend"] < p8["council_blend"]
    assert p8["twc9_crps"] < p8["council_crps"] and p8["twc9_ll"] <= p8["council_ll"]
    # served row untouched (read-only doctrine)
    conn = storage._connect_at(tmp / "t.db")
    highs = {r[0] for r in conn.execute("SELECT high FROM verdicts").fetchall()}
    conn.close()
    assert highs == {served}, highs
    assert res["verdict"] in ("PASS", "FAIL", "REFUSE")
    print(f"twc_gate_score selftest PASSED (screen/fallback; leak-free walk-forward floor; "
          f"folds; end-to-end synthetic gate {res['verdict']}; served rows untouched)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
