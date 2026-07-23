"""twc_independence.py — TWC error-correlation audit (Plan 4 Phase 5, read-only).

BEFORE anyone gets ideas about promoting TWC into the blend: measure whether TWC's forecast errors
carry information the council does not already have. TWC shares corporate lineage with the WU
forecast family; if its signed errors are ~collinear with the council's blend error (or with an
existing member's error), its MARGINAL information for any future blend is near zero regardless of
how good its standalone MAE looks — a low-MAE-but-redundant input adds nothing but overfitting risk.

The audit joins settled TWC rows (tracked_forecasts) with the council's stored issue-time provenance
(Plan 3 Phase 0) on (place, target_date), and per city computes the Pearson correlation of the TWC
signed error against:
  (a) the council's blended-forecast error   (final − actual), and
  (b) each individual member's error         (corrected member forecast − actual).
A |r| ≥ COLLINEAR_R cell is flagged: TWC ≈ that signal, marginal information ~0.

GATES (both mandatory, honest until met): a correlation is reported for a city only once it has
≥ THRESHOLD_N paired settled days AND provenance is present. Below that it reads UNMEASURED(n=k) —
correct output, not a bug (provenance capture is recent; paired days accrue forward).

STRICTLY READ-ONLY: this module issues SELECTs and prints a report. It NEVER writes a vote, a
weight, a served probability, or any forecast — grep-provable (KAT test_no_write_statements), and
any actual blend inclusion is a Plan 3 candidate (deterministic transform, shadow-scored,
budget-consuming, human-promoted), restated here so no one shortcuts it through this plan.

Run:  PYTHONPATH=. python3 tools/twc_independence.py [--source twc]
Self-test:  PYTHONPATH=. python3 tools/twc_independence.py --selftest
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from weather_council import storage

THRESHOLD_N = 30           # paired settled days before a correlation is reported (else UNMEASURED)
COLLINEAR_R = 0.9          # |r| at/above this ⇒ TWC's error ≈ that signal ⇒ marginal info ~0
MIN_MEMBER_N = THRESHOLD_N  # a member correlation also needs THRESHOLD_N aligned days to be quoted


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson r over paired (xs, ys). None if <3 points or either side has zero variance."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def audit(source: str = "twc", db_path=None) -> dict:
    """Per-city error-correlation audit of `source` vs the council + its members. Read-only."""
    conn = storage._connect() if db_path is None else storage._connect_at(db_path)
    try:
        rows = conn.execute(
            "SELECT v.place, v.target_date, v.actual_high, v.provenance_json, t.fc_high "
            "FROM verdicts v JOIN tracked_forecasts t "
            "  ON v.place = t.place AND v.target_date = t.target_date "
            "WHERE v.provenance_json IS NOT NULL AND v.actual_high IS NOT NULL "
            "  AND t.source = ? AND t.fc_high IS NOT NULL",
            (source,)).fetchall()
    finally:
        conn.close()

    # place -> {twc:[], council:[], members:{mid:[(twc_err, member_err)]}}
    acc: dict[str, dict] = {}
    for place, tgt, actual, pj, fc_high in rows:
        try:
            prov = json.loads(pj)
        except Exception:
            continue
        blend = prov.get("blend") or {}
        final = blend.get("high")
        if final is None or actual is None or fc_high is None:
            continue
        twc_err = float(fc_high) - float(actual)
        council_err = float(final) - float(actual)
        cell = acc.setdefault(place, {"twc": [], "council": [], "members": {}})
        cell["twc"].append(twc_err)
        cell["council"].append(council_err)
        included = set(prov.get("included_high") or [])
        for vt in (prov.get("votes") or []):
            mid = vt.get("member_id")
            cor = vt.get("corrected_high")
            if mid in included and isinstance(cor, (int, float)):
                cell["members"].setdefault(mid, []).append((twc_err, float(cor) - float(actual)))

    out = {"source": source, "threshold_n": THRESHOLD_N, "cities": {}}
    for place in sorted(acc):
        cell = acc[place]
        n = len(cell["twc"])
        entry = {"n_paired": n, "status": "MEASURED" if n >= THRESHOLD_N else "UNMEASURED",
                 "corr_council": None, "members": [], "flagged_collinear": []}
        if n >= THRESHOLD_N:
            entry["corr_council"] = _round(_pearson(cell["twc"], cell["council"]))
            for mid, pairs in cell["members"].items():
                if len(pairs) < MIN_MEMBER_N:
                    continue
                r = _pearson([p[0] for p in pairs], [p[1] for p in pairs])
                entry["members"].append({"member": mid, "n": len(pairs), "r": _round(r)})
            # strongest |r| first; members with r=None (unmeasurable) sort LAST (was a -1 sentinel
            # that inverted to |r|=1 and pushed them to the top)
            entry["members"].sort(key=lambda m: (m["r"] is None, -abs(m["r"]) if m["r"] is not None else 0.0))
            # collinearity flags: TWC ≈ the council blend, or ≈ any individual member.
            if entry["corr_council"] is not None and abs(entry["corr_council"]) >= COLLINEAR_R:
                entry["flagged_collinear"].append("council-blend")
            entry["flagged_collinear"] += [m["member"] for m in entry["members"]
                                           if m["r"] is not None and abs(m["r"]) >= COLLINEAR_R]
        out["cities"][place] = entry
    return out


def _round(r):
    return round(r, 4) if isinstance(r, (int, float)) else None


def report_lines(result: dict) -> list[str]:
    L = [f"TWC INDEPENDENCE AUDIT — error correlation vs the council (read-only; needs "
         f"n≥{result['threshold_n']} paired days + provenance; blend inclusion is a Plan-3 "
         f"candidate, never this plan)"]
    if not result["cities"]:
        L.append("  (no paired provenance+TWC settled days yet — accruing; provenance capture is "
                 "recent, pairs accrue forward)")
        return L
    for place, e in result["cities"].items():
        if e["status"] == "UNMEASURED":
            L.append(f"  {place[:22]:22} UNMEASURED (n={e['n_paired']}/{result['threshold_n']})")
            continue
        L.append(f"  {place[:22]:22} n={e['n_paired']}  corr(TWC, council-blend)={e['corr_council']}")
        for m in e["members"][:4]:
            L.append(f"      vs {m['member'][:18]:18} r={m['r']} (n={m['n']})")
        if e["flagged_collinear"]:
            L.append(f"      ⚠ COLLINEAR (|r|≥{COLLINEAR_R}) with {', '.join(e['flagged_collinear'])} "
                     f"— marginal information ~0; a low standalone MAE would NOT justify blend "
                     f"inclusion (route through the Plan-3 gate regardless).")
        else:
            L.append("      no signal is collinear at this threshold — TWC MAY carry marginal "
                     "information; still a Plan-3 candidate before any inclusion.")
    return L


def _selftest() -> int:
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    dbp = tmp / "t.db"
    conn = storage._connect_at(dbp)
    # 32 paired days: member 'collin' error == TWC error exactly (r=1); 'indep' alternates
    # independently; council blend is the mean of the two members.
    with conn:
        for i in range(32):
            actual = 30.0
            twc_fc = actual + (0.6 if i % 2 else -0.4)          # TWC signed error alternates
            twc_err = twc_fc - actual
            collin_cor = actual + twc_err                       # collinear member (err == twc err)
            indep_cor = actual + (0.5 if (i // 2) % 2 else -0.5)  # independent pattern
            final = (collin_cor + indep_cor) / 2
            prov = {"blend": {"high": final}, "included_high": ["collin", "indep"],
                    "votes": [{"member_id": "collin", "corrected_high": collin_cor},
                              {"member_id": "indep", "corrected_high": indep_cor}]}
            conn.execute(
                "INSERT INTO verdicts (issued_at, place, target_date, high, low, confidence, "
                " actual_high, provenance_json) VALUES ('t','Testville',?,?,?, 'HIGH', ?, ?)",
                (f"2026-05-{i+1:02d}", final, 25.0, actual, json.dumps(prov)))
            conn.execute(
                "INSERT INTO tracked_forecasts (source, issued_at, place, target_date, fc_high, "
                " fc_low, actual_high) VALUES ('twc','t','Testville',?,?,?,?)",
                (f"2026-05-{i+1:02d}", twc_fc, twc_fc - 6, actual))
    conn.close()
    res = audit("twc", db_path=dbp)
    city = res["cities"]["Testville"]
    assert city["status"] == "MEASURED" and city["n_paired"] == 32, city
    rs = {m["member"]: m["r"] for m in city["members"]}
    assert abs(rs["collin"] - 1.0) < 1e-9, rs                   # exact collinearity recovered
    assert abs(rs["indep"]) < 0.5, rs                           # independent member ~uncorrelated
    assert "collin" in city["flagged_collinear"] and "indep" not in city["flagged_collinear"]

    # below-threshold city reads UNMEASURED
    conn = storage._connect_at(dbp)
    with conn:
        for i in range(5):
            prov = {"blend": {"high": 20.0}, "included_high": ["a"],
                    "votes": [{"member_id": "a", "corrected_high": 20.0}]}
            conn.execute("INSERT INTO verdicts (issued_at, place, target_date, high, low, "
                         "confidence, actual_high, provenance_json) VALUES "
                         "('t','Thinsville',?,20.0,15.0,'HIGH',20.0,?)",
                         (f"2026-06-{i+1:02d}", json.dumps(prov)))
            conn.execute("INSERT INTO tracked_forecasts (source, issued_at, place, target_date, "
                         "fc_high, fc_low, actual_high) VALUES ('twc','t','Thinsville',?,21.0,15.0,20.0)",
                         (f"2026-06-{i+1:02d}",))
    conn.close()
    res2 = audit("twc", db_path=dbp)
    assert res2["cities"]["Thinsville"]["status"] == "UNMEASURED", res2["cities"]["Thinsville"]
    print("twc_independence selftest PASSED (Pearson r; exact-collinear member flagged, independent "
          f"not; n≥{THRESHOLD_N} gate -> UNMEASURED below; read-only)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="TWC error-correlation independence audit (read-only).")
    ap.add_argument("--source", default="twc")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    for line in report_lines(audit(args.source)):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
