"""Per-member bias-BREAK watch — executes ledger/preregistered/member_bias_break_watch.md.

The council's imported edge (NWP skill) dies locally through member pipeline changes: a
provider ships a new model cycle and that member's station bias regime RESETS. Watchdog
Duty 3b watches exactly this for one cell (ECMWF@Changi, pinned reference); this module is
that watch generalised to every (city, member) cell, from data already logged: the
issue-time provenance votes (`raw_high` per member) joined to the settled truth
(`actual_high`) on the verdicts table.

Alert-only, recommend-only (HARD RULE 2): a BREAK line routes a human to the fold-gated
recalibration path that already exists. It never adjusts a bias, a blend, or a pmf — a
break watch that silently re-biased members would be a served-number change wearing a
monitor's name.

THE BREAK TEST (same convention as the TWC driver-health monitor, twc_member_gate.md):
per cell, the FIRST R settled errors (raw_high − actual_high) become the FROZEN reference
(pinned to disk; a re-pin is a documented breakpoint). The null: "if the bias regime were
unchanged, a K-day mean error would fall inside the reference's bootstrap 99% CI of
K-means." The rolling mean of the K most recent errors outside that CI = BREAK. Seeded,
deterministic, stdlib.

Why this does NOT false-alarm on seasonal drift (the KAT'd control): the CI half-width is
~2.58·σ/√K; a recency-class seasonal drift moves the K-mean by a fraction of σ over any
K-window and stays inside, while a model-cycle STEP of ~2σ exits immediately. Slow drift
that eventually accumulates past the CI is a real, actionable regime change (the
recency-bias path exists for it) — flagged late is correct, invented early is not.

Cells with fewer than R settled provenance rows report ACCRUING (the settled∧provenance
join only began filling 2026-07-11; the watch arms itself as history accrues).
KATs: tests/test_member_break.py. CLI: tools/member_break_watch.py.
"""
from __future__ import annotations

__all__ = ["REF_N", "ROLL_K", "CI_LEVEL", "extract_errors", "pin_reference",
           "assess_cell", "assess_all"]

import random

REF_N = 20        # frozen reference window: first R settled errors per cell
ROLL_K = 10       # rolling window whose mean is tested
CI_LEVEL = 0.99   # two-sided bootstrap CI on K-means (conservative by design)
_BOOT = 2000
_SEED = 0


def extract_errors(rows: list[tuple[str, str, float, list[dict]]]
                   ) -> dict[tuple[str, str], list[tuple[str, float]]]:
    """PURE. rows = (place, target_date, actual_high, votes[]) for settled verdicts with
    provenance. Returns {(place, member_id): [(date, raw_err)] date-ascending} where
    raw_err = member raw_high − actual_high — the member's UNCORRECTED bias, the driver
    series a pipeline upgrade breaks (the correction consumes it downstream)."""
    out: dict[tuple[str, str], list[tuple[str, float]]] = {}
    for place, date, actual, votes in rows:
        if actual is None:
            continue
        for v in votes or []:
            m, rh = v.get("member_id"), v.get("raw_high")
            if m and isinstance(rh, (int, float)):
                out.setdefault((place, m), []).append((date, float(rh) - float(actual)))
    for k in out:
        out[k].sort()
    return out


def pin_reference(errs: list[float], ref_n: int = REF_N, roll_k: int = ROLL_K,
                  seed: int = _SEED, boot: int = _BOOT) -> dict | None:
    """PURE. Build the FROZEN reference from the FIRST ref_n errors: bootstrap the
    two-sided CI_LEVEL CI of roll_k-sized means under the reference distribution.
    Returns None below ref_n (cell still ACCRUING). Deterministic (seeded)."""
    if len(errs) < ref_n:
        return None
    ref = [float(e) for e in errs[:ref_n]]
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(ref) for _ in range(roll_k)) / roll_k
                   for _ in range(boot))
    a = (1.0 - CI_LEVEL) / 2.0
    lo = means[max(0, int(a * boot) - 1)]
    hi = means[min(boot - 1, int((1.0 - a) * boot))]
    return {"n_ref": ref_n, "ref_mean": sum(ref) / ref_n,
            "ci_lo": lo, "ci_hi": hi, "roll_k": roll_k}


def assess_cell(pin: dict | None, errs: list[float], n_total: int) -> dict:
    """PURE. Status for one (city, member) cell:
      ACCRUING — no pin yet (n_total < REF_N), or pinned but < roll_k post-reference errors;
      OK       — rolling mean inside the frozen CI;
      BREAK    — rolling mean outside it (alert; a human adjudicates via the gated path)."""
    if pin is None:
        return {"status": "ACCRUING", "n": n_total, "need": REF_N}
    k = int(pin["roll_k"])
    post = errs[pin["n_ref"]:]
    if len(post) < k:
        return {"status": "ACCRUING", "n": n_total,
                "need": pin["n_ref"] + k, "pinned": True}
    recent = post[-k:]
    rm = sum(recent) / k
    broke = not (pin["ci_lo"] <= rm <= pin["ci_hi"])
    return {"status": "BREAK" if broke else "OK", "rolling_mean": rm,
            "ci": (pin["ci_lo"], pin["ci_hi"]), "ref_mean": pin["ref_mean"],
            "n": n_total}


def assess_all(cells: dict[tuple[str, str], list[tuple[str, float]]],
               pins: dict[str, dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    """PURE. Assess every cell; pin any cell that has just reached REF_N (a pin, once
    written, is NEVER moved here — re-pinning is a human, documented breakpoint).
    Returns (updated_pins, {cell_key: assessment}). cell_key = 'place|member'."""
    out: dict[str, dict] = {}
    pins = dict(pins)
    for (place, member), series in sorted(cells.items()):
        key = f"{place}|{member}"
        errs = [e for _d, e in series]
        if key not in pins:
            pin = pin_reference(errs)
            if pin is not None:
                pin["pinned_through"] = series[REF_N - 1][0]   # last ref date, auditability
                pins[key] = pin
        out[key] = assess_cell(pins.get(key), errs, len(errs))
    return pins, out


def _self_test() -> None:
    rng = random.Random(7)
    base = [rng.gauss(0.8, 1.0) for _ in range(REF_N)]          # stable warm-bias regime

    # 1. No break: post-reference errors from the SAME regime stay OK.
    same = base + [rng.gauss(0.8, 1.0) for _ in range(15)]
    pin = pin_reference(same)
    a = assess_cell(pin, same, len(same))
    assert a["status"] == "OK", a

    # 2. Model-cycle STEP (+2σ) breaks immediately.
    step = base + [rng.gauss(2.8, 1.0) for _ in range(ROLL_K)]
    a = assess_cell(pin_reference(step), step, len(step))
    assert a["status"] == "BREAK", a

    # 3. Seasonal-drift control (the registration's no-false-alarm KAT): a slow drift
    #    (0.03σ/day — recency-class) does NOT alarm over a month of post-reference days.
    drift = base + [rng.gauss(0.8 + 0.03 * i, 1.0) for i in range(30)]
    a = assess_cell(pin_reference(drift), drift, len(drift))
    assert a["status"] == "OK", a

    # 4. ACCRUING below the reference floor, and below pin+K post-reference.
    assert assess_cell(pin_reference(base[:10]), base[:10], 10)["status"] == "ACCRUING"
    short_post = base + [0.8] * (ROLL_K - 1)
    assert assess_cell(pin_reference(short_post), short_post,
                       len(short_post))["status"] == "ACCRUING"

    # 5. Pins are frozen: assess_all never moves an existing pin, even given new data.
    cells = {("X", "ecmwf"): [(f"d{i:03d}", e) for i, e in enumerate(same)]}
    pins1, _ = assess_all(cells, {})
    cells2 = {("X", "ecmwf"): [(f"d{i:03d}", e) for i, e in enumerate(step)]}
    pins2, res2 = assess_all(cells2, pins1)
    assert pins2["X|ecmwf"] == pins1["X|ecmwf"]                 # frozen
    assert res2["X|ecmwf"]["status"] == "BREAK"

    # 6. extract_errors: raw−actual, member-keyed, unsettled rows skipped.
    rows = [("X", "2026-07-01", 30.0,
             [{"member_id": "ecmwf", "raw_high": 31.0},
              {"member_id": "gfs", "raw_high": 29.5}]),
            ("X", "2026-07-02", None, [{"member_id": "ecmwf", "raw_high": 31.0}])]
    e = extract_errors(rows)
    assert e[("X", "ecmwf")] == [("2026-07-01", 1.0)]
    assert e[("X", "gfs")][0][1] == -0.5

    print("member_break self-test PASSED — same-regime OK; +2σ step BREAKS; recency-class "
          "seasonal drift stays silent (CI test, not vs-zero); ACCRUING below floors; pins "
          "frozen once written; raw−actual extraction correct.")


if __name__ == "__main__":
    _self_test()
