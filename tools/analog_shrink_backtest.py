#!/usr/bin/env python3
"""Real-data leave-one-year-out head-to-head for the seasonal-analog bias treatments.

WHAT THIS ANSWERS
-----------------
The council, out of season, replaces each member's trailing-window bias with one
re-learned from same-day-of-year analog days and subtracts it at FULL STRENGTH
(weather_council/seasonal.py + council._apply_seasonal_analog).  `analog_shrink.py`
proposes a disciplined alternative — empirical-Bayes (Efron-Morris) pooling of those
per-member biases toward the panel mean — and argues, on first principles, that it
will NOT move the *blended* verdict because the inverse-variance blend already damps
per-member bias noise.  This tool puts that argument on REAL data: it drives the
council's own fetch path, runs the leave-one-year-out comparison, and prints the
numbers so the verdict can be checked without trusting the prose.

METHOD (leak-free)
------------------
For each city and variable (high, low):
  * resolve truth exactly as a live verdict does (council._resolve_truth), then the
    analog observed series exactly as the council does (council._analog_observed),
    capped strictly before the live window so nothing leaks;
  * for each of the 8 members, fetch its OWN past forecasts (fetch_history_series)
    over [archive floor .. window_start-1d] and keep the ±21-day day-of-year analog
    pairs — identical to seasonal_skill's inputs;
  * hand the per-member analog pairs to analog_shrink.analog_shrink_eval, which does
    the leave-one-year-out split internally (a held-out year's bias is never trained
    on itself), holds the inverse-variance member weights FIXED across treatments, and
    compares NONE / FULL (the live incumbent) / SHRINK0 / POOLED on held-out blended
    MAE with a seeded paired-bootstrap gate.

BOUNDARIES (non-negotiable)
---------------------------
RECOMMEND-ONLY.  This reads data and prints a report.  It NEVER edits code or tuned
constants, never changes a served verdict, never trades or moves funds, never writes
to git.  A surfaced CONSIDER is a suggestion for a human, nothing more.

Caveats it states rather than hides:
  * The committed live basket is 2 cities (London, Hong Kong); with so few cities the
    cross-city evidence is weak — read any CONSIDER as "look harder", not proof.
  * The eval omits the per-day outlier screen the live blend applies on the target
    day; that screen acts identically on all four treatments, so it does not bias the
    full-vs-pooled comparison, but the absolute MAEs here are analog-window numbers,
    not the served verdict's.

Stdlib only.  Usage:
    python3 tools/analog_shrink_backtest.py [--target YYYY-MM-DD] [City ...]
"""
from __future__ import annotations

import datetime as dt
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from weather_council.agents import COUNCIL                       # noqa: E402
from weather_council.analog_shrink import (                      # noqa: E402
    MIN_ANALOG, MIN_HELDOUT_DAYS, analog_shrink_eval, pool_member_biases,
)
from weather_council.council import WEIGHT_POWER, Council         # noqa: E402
from weather_council.seasonal import (                           # noqa: E402
    SEASON_ANALOG_ARCHIVE_FLOOR, SEASON_ANALOG_WINDOW_DAYS, doy_distance,
)

DEFAULT_BASKET = ["London", "Hong Kong"]
WINDOW = 120                      # matches tools/daily_healthcheck._resolve_truth window
ATTRS = (("high", 0), ("low", 1))


def _analog_pairs(hist, analog_obs, target, attr_idx):
    """The ±window day-of-year (forecast, observed) pairs for one variable — exactly
    the rows seasonal_skill would pair, tagged with their date for the LOYO split."""
    out = []
    for day, fc in hist.items():
        obs = analog_obs.get(day)
        if obs is None:
            continue
        dd = doy_distance(day, target)
        if dd is None or dd > SEASON_ANALOG_WINDOW_DAYS:
            continue
        f, o = fc[attr_idx], obs[attr_idx]
        if f is None or o is None:
            continue
        out.append((day, f, o))
    return out


def _member_bias_se(pairs):
    """Full-sample analog bias and its standard error, for the diagnostic table."""
    diffs = [f - o for _, f, o in pairs]
    bias = statistics.mean(diffs)
    sd = statistics.pstdev(diffs) if len(diffs) > 1 else 0.0
    se = sd / math.sqrt(len(diffs)) if diffs else float("inf")
    return bias, se, len(diffs)


def _assemble_city(council: Council, city: str, target: dt.date):
    """Drive the council's OWN fetch path. Returns (truth_kind, n_obs, per_member,
    pairs_by_attr) or raises on a hard failure (no geocode / no truth)."""
    place = council.sources.geocode(city)
    fp, observed, w_start, w_end, truth = council._resolve_truth(place, target, WINDOW)
    analog_obs = council._analog_observed(fp, truth, w_start)
    a_end = w_start - dt.timedelta(days=1)

    pairs_by_attr = {name: {} for name, _ in ATTRS}
    per_member = []                       # (member_id, n_high, bias_high, se_high, note)
    for spec in COUNCIL:
        try:
            hist = council.sources.fetch_history_series(
                spec.model, fp, SEASON_ANALOG_ARCHIVE_FLOOR, a_end)
        except Exception as exc:          # per-member isolation — never abort the city
            per_member.append((spec.member_id, 0, None, None, f"history unavailable: {exc}"))
            continue
        n_high = 0
        bias_h = se_h = None
        note = None
        for name, idx in ATTRS:
            pairs = _analog_pairs(hist, analog_obs, target, idx)
            if len(pairs) >= MIN_ANALOG:
                pairs_by_attr[name][spec.member_id] = pairs
            if idx == 0:                  # high drives the diagnostic row
                n_high = len(pairs)
                if pairs:
                    bias_h, se_h, _ = _member_bias_se(pairs)
                else:
                    note = "no analog highs"
        if n_high and n_high < MIN_ANALOG:
            note = f"thin ({n_high} < {MIN_ANALOG} analog pairs) — sits out folds"
        per_member.append((spec.member_id, n_high, bias_h, se_h, note))

    return (truth.get("kind", "?"), len(analog_obs),
            (truth.get("season_gap_days")), per_member, pairs_by_attr)


def _fmt_eval(ev) -> str:
    if ev is None:
        return ("      insufficient held-out data (need >=2 analog years and "
                f">={MIN_HELDOUT_DAYS} scored days) — no verdict")
    flag = "CONSIDER pooling" if ev.recommend else "HOLD full-swap (live incumbent)"
    return (
        f"      n={ev.n_days}d/{ev.n_years}y  MAE: none {ev.mae_none:.3f} | "
        f"FULL {ev.mae_full:.3f} | shrink0 {ev.mae_shrink0:.3f} | pooled {ev.mae_pooled:.3f}\n"
        f"      pooled vs FULL: {ev.improvement_pooled:+.3f} °C "
        f"({ev.improvement_pooled_pct * 100:+.1f}%), 90% CI "
        f"[{ev.boot_lo:+.3f}, {ev.boot_hi:+.3f}], mean λ {ev.mean_lambda:.2f}  ->  {flag}"
    )


def _diagnostic_table(per_member, pairs_high) -> list[str]:
    """Show each member's full-sample analog bias, SE, and the λ pooling would assign.
    This is WHERE the redundancy is visible: tightly-measured members (small SE) get
    λ≈1 (kept) AND the largest blend weight — so pooling touches them least."""
    lines = ["    member   n   bias°C    SE°C    pool-λ   weight%"]
    items, ids = [], []
    for mid, n, bias, se, _ in per_member:
        if bias is not None and se is not None and mid in pairs_high:
            items.append((bias, se))
            ids.append(mid)
    lam_by = {}
    w_by = {}
    if len(items) >= 2:
        pooled = pool_member_biases(items)
        raw_w = {}
        for (bias, se), (_, lam), mid in zip(items, pooled, ids):
            lam_by[mid] = lam
            # mirror the live blend weight: inverse (analog mae_corrected)^power.
            # mae_corrected ~ SE*sqrt(n)/MAD_TO_SD; we have SE and n -> recover a weight
            # proxy from the member's own residual spread for display only.
        # weight proxy from residual SD (= SE*sqrt(n)); fall back to equal if degenerate
        for mid, n, bias, se, _ in per_member:
            if mid in lam_by:
                sd = se * math.sqrt(max(n, 1))
                mae_c = sd / math.sqrt(math.pi / 2.0) if sd > 0 else 0.1
                raw_w[mid] = 1.0 / max(mae_c, 0.1) ** WEIGHT_POWER
        tot = sum(raw_w.values()) or 1.0
        w_by = {k: 100.0 * v / tot for k, v in raw_w.items()}
    for mid, n, bias, se, note in per_member:
        if bias is None or se is None:
            lines.append(f"    {mid:<7} {n:>3}   {'—':>7} {'—':>7}   {'—':>5}   {'—':>6}"
                         f"   {note or ''}")
        else:
            lam = lam_by.get(mid)
            w = w_by.get(mid)
            lam_s = f"{lam:.2f}" if lam is not None else "—"
            w_s = f"{w:.1f}" if w is not None else "—"
            extra = f"   {note}" if note else ""
            lines.append(f"    {mid:<7} {n:>3}   {bias:+7.2f} {se:>7.3f}   "
                         f"{lam_s:>5}   {w_s:>6}{extra}")
    return lines


def main(argv: list[str]) -> int:
    target = dt.date.today()
    cities: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--target" and i + 1 < len(argv):
            target = dt.date.fromisoformat(argv[i + 1])
            i += 2
            continue
        cities.append(a)
        i += 1
    basket = cities or DEFAULT_BASKET

    print("=" * 74)
    print("  analog-bias shrinkage — real-data leave-one-year-out backtest")
    print(f"  target {target.isoformat()} (day-of-year analog centre) | "
          f"±{SEASON_ANALOG_WINDOW_DAYS}d window | WEIGHT_POWER={WEIGHT_POWER}")
    print(f"  basket: {', '.join(basket)}"
          + ("" if len(basket) > 2 else "   [caveat: thin basket — weak cross-city evidence]"))
    print("  RECOMMEND-ONLY: reads data, prints a report. Edits nothing; trades nothing.")
    print("=" * 74)

    council = Council()
    any_consider = False
    any_scored = False
    degraded = []

    for city in basket:
        print(f"\n## {city}")
        try:
            kind, n_obs, season_gap, per_member, pairs_by_attr = _assemble_city(
                council, city, target)
        except Exception as exc:
            print(f"   [degraded] could not assemble city: {exc}")
            degraded.append(city)
            continue

        gap_s = f"{season_gap}d" if season_gap is not None else "?"
        print(f"   truth source: {kind} | analog observed days: {n_obs} | "
              f"trailing-window season gap: {gap_s}")
        for ln in _diagnostic_table(per_member, pairs_by_attr["high"]):
            print(ln)

        for name, _ in ATTRS:
            mp = pairs_by_attr[name]
            usable = len(mp)
            print(f"   [{name}] members with >= {MIN_ANALOG} analog pairs: {usable}")
            if usable < 2:
                print("      insufficient members to blend — no verdict")
                continue
            ev = analog_shrink_eval(mp, weight_power=WEIGHT_POWER)
            print(_fmt_eval(ev))
            if ev is not None:
                any_scored = True
                any_consider = any_consider or ev.recommend

    print("\n" + "=" * 74)
    if degraded:
        print(f"  DEGRADED: {len(degraded)}/{len(basket)} cities unusable this run "
              f"({', '.join(degraded)}) — likely upstream throttling; re-run to warm cache.")
    if not any_scored:
        print("  RESULT: no city produced a scored verdict (network/throttle?). "
              "Nothing to recommend.")
        print("=" * 74)
        return 1
    if any_consider:
        print("  RESULT: at least one CONSIDER surfaced. Treat as 'look harder', NOT proof.")
        print("  FOR HUMAN REVIEW: inspect the city/variable above; nothing was applied.")
    else:
        print("  RESULT: HOLD across the basket. Empirical-Bayes pooling did not beat the")
        print("  full-strength analog swap on held-out BLENDED MAE past the 0.03 °C floor.")
        print("  This matches the structural argument: the inverse-variance blend already")
        print("  damps per-member bias noise, so member-level shrinkage does not survive it.")
    print("  (Recommend-only. No code, constant, verdict, trade, or commit was touched.)")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
