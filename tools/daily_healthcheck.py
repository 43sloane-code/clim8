#!/usr/bin/env python3
"""Daily, data-driven health check for the weather council.

What this is — and is NOT:

  * It RE-EARNS the project's tuned choices on fresh data every day and writes a
    report a human can read. It is a *monitor and recommender*, never an editor.
  * It NEVER edits source, never trades, never moves funds, never touches the
    market/order side of anything. It only reads weather/observation archives
    through the project's own sandboxed client and writes a text report.

Each run:
  1. Walk-forward backtests the council across the 8-city basket (rolling-origin,
     no future leakage), exactly the evaluation the live verdict uses.
  2. Compares the four weighting/bias variants (bias mean|median × 1/MAE^1|^2)
     so the committed choice (mean bias, 1/MAE^2) is re-justified — or challenged
     — on today's data. A variant only "wins" if it beats current on the basket
     by MIN_IMPROVEMENT °C, so noise is not mistaken for signal.
  3. Compares the current method's basket MAE to the stored baseline and flags
     drift/regression beyond REGRESSION_TOL °C.
  4. Checks Meteostat bulk-archive freshness per city (the lag that drives the
     out-of-season confidence downgrade) and flags changes.

Recommendations are printed for human review. Applying them is a human decision.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from weather_council.agents import MIN_SAMPLES          # noqa: E402
from weather_council.council import Council, WEIGHT_POWER  # noqa: E402

# The basket — geographically diverse so a constant has to generalize, not fit
# one climate. Mirrors the sweep the weighting exponent was originally earned on.
BASKET = ["London", "Tokyo", "New York", "Sydney",
          "Berlin", "Chicago", "São Paulo", "Cairo"]

WINDOW = 120                 # days of history per city (bounded by archive lag)
WARMUP = MIN_SAMPLES         # walk-forward warmup = the live validation warmup
MIN_IMPROVEMENT = 0.03       # °C a challenger must beat current by on the basket
REGRESSION_TOL = 0.05        # °C of basket-MAE drift vs baseline worth flagging

# The committed live configuration, re-derived here so the report names what it
# is actually checking rather than hard-coding a guess.
CURRENT_BIAS = "mean"        # agents._skill uses statistics.mean for the bias
CURRENT_POWER = WEIGHT_POWER  # imported from the live module (single source)

REPORTS = ROOT / "reports"
BASELINE = REPORTS / "baseline.json"


def _blend_on_date(votes, attr, day, train, bias_method, power):
    """One held-out day's blend under a chosen (bias_method, power), using only
    `train` dates to learn each member's bias and weight. None if too sparse."""
    num = den = 0.0
    for v in votes:
        series = v.hist_high if attr == "high" else v.hist_low
        if day not in series:
            continue
        pairs = [series[d] for d in series if d in train]
        if len(pairs) < 5:
            continue
        diffs = [f - o for f, o in pairs]
        bias = (statistics.mean(diffs) if bias_method == "mean"
                else statistics.median(diffs))
        mae_c = statistics.mean(abs(f - o - bias) for f, o in pairs)
        w = 1.0 / max(mae_c, 0.1) ** power
        num += w * (series[day][0] - bias)
        den += w
    return (num / den) if den > 0 else None


def _walk_forward(votes, observed, bias_method, power):
    """Rolling-origin held-out MAE and hit-rate(±2°C) for one variant."""
    dates = sorted(observed)
    test = dates[WARMUP:]
    if len(test) < 5:
        return None, None, 0
    errs, hits, n = [], 0, 0
    for i, d in enumerate(test):
        obs = observed.get(d)
        if obs is None:
            continue
        train = set(dates[:WARMUP + i])
        for attr, idx in (("high", 0), ("low", 1)):
            pred = _blend_on_date(votes, attr, d, train, bias_method, power)
            if pred is None:
                continue
            e = abs(pred - obs[idx])
            errs.append(e)
            hits += 1 if e <= 2.0 else 0
            n += 1
    return (statistics.mean(errs) if errs else None,
            (hits / n) if n else None, n)


def _city_votes(city, target):
    """Resolve truth + collect each member's votes for one city. Uses a FRESH
    Council (and thus a fresh sandbox request budget) per city, since one client
    across the whole basket would exceed MAX_REQUESTS_PER_RUN. Returns
    (observed, votes, freshness_dict, requests_made) or raises."""
    council = Council()
    place = council.sources.geocode(city)
    fp, observed, w_start, w_end, truth = council._resolve_truth(
        place, target, WINDOW)
    votes = [m.analyze(fp, target, w_start, w_end, observed)
             for m in council.members]
    fresh = {
        "kind": truth.get("kind"),
        "window_end": truth.get("window_end"),
        "season_gap_days": truth.get("season_gap_days"),
        "sample_days": truth.get("sample_days"),
    }
    return observed, votes, fresh, council.sources.http.requests_made


VARIANTS = [("mean", 1), ("median", 1), ("mean", 2), ("median", 2)]


def main() -> int:
    today = dt.date.today()
    target = today

    per_city = {}
    basket_acc = {v: [] for v in VARIANTS}
    basket_hit = {v: [] for v in VARIANTS}
    freshness = {}
    total_requests = 0

    for city in BASKET:
        try:
            observed, votes, fresh, reqs = _city_votes(city, target)
            total_requests += reqs
        except Exception as exc:                       # one city must not kill the run
            per_city[city] = {"error": str(exc)}
            continue
        freshness[city] = fresh
        res = {}
        for variant in VARIANTS:
            mae, hit, n = _walk_forward(votes, observed, *variant)
            res[variant] = (mae, hit, n)
            if mae is not None:
                basket_acc[variant].append(mae)
            if hit is not None:
                basket_hit[variant].append(hit)
        per_city[city] = res

    # Basket means per variant.
    basket = {}
    for variant in VARIANTS:
        maes = basket_acc[variant]
        hits = basket_hit[variant]
        basket[variant] = (
            statistics.mean(maes) if maes else None,
            statistics.mean(hits) if hits else None,
            len(maes),
        )

    cur = (CURRENT_BIAS, CURRENT_POWER)
    cur_mae = basket.get(cur, (None,))[0]
    # Best challenger by basket MAE.
    ranked = sorted(
        (v for v in VARIANTS if basket[v][0] is not None),
        key=lambda v: basket[v][0],
    )
    best = ranked[0] if ranked else None

    # Baseline drift.
    baseline = None
    if BASELINE.exists():
        try:
            baseline = json.loads(BASELINE.read_text())
        except Exception:
            baseline = None

    lines = []
    lines.append(f"WEATHER COUNCIL — DAILY HEALTH CHECK  ({today.isoformat()})")
    lines.append("=" * 64)
    lines.append(f"basket: {len(BASKET)} cities | window {WINDOW}d | warmup {WARMUP} | "
                 f"current = bias {CURRENT_BIAS}, 1/MAE^{CURRENT_POWER}")
    lines.append("")

    lines.append("PER-CITY held-out MAE (current variant) + truth freshness")
    for city in BASKET:
        r = per_city.get(city, {})
        if "error" in r:
            lines.append(f"  {city:12} ERROR: {r['error']}")
            continue
        mae, hit, n = r.get(cur, (None, None, 0))
        f = freshness.get(city, {})
        mae_s = f"{mae:.3f}" if mae is not None else "  -  "
        hit_s = f"{hit*100:.0f}%" if hit is not None else " - "
        lines.append(f"  {city:12} MAE {mae_s}  hit {hit_s:>4}  n={n:3} | "
                     f"truth={f.get('kind','?')} end={f.get('window_end','?')} "
                     f"season_gap={f.get('season_gap_days','?')}d")
    lines.append("")

    lines.append("VARIANT SWEEP (basket-averaged held-out MAE / hit)")
    for variant in VARIANTS:
        mae, hit, ncity = basket[variant]
        tag = "  <- CURRENT" if variant == cur else ""
        mae_s = f"{mae:.4f}" if mae is not None else "  -   "
        hit_s = f"{hit*100:.1f}%" if hit is not None else "  -  "
        lines.append(f"  bias {variant[0]:<6} 1/MAE^{variant[1]}  "
                     f"MAE {mae_s}  hit {hit_s:>6}  cities={ncity}{tag}")
    lines.append("")

    # Recommendation on the variant.
    lines.append("RECOMMENDATION (constants — human review required)")
    if best is None or cur_mae is None:
        lines.append("  insufficient data to evaluate variants today.")
    elif best == cur:
        lines.append(f"  HOLD. Current (bias {cur[0]}, 1/MAE^{cur[1]}) is still best on "
                     f"the basket (MAE {cur_mae:.4f}). No change recommended.")
    else:
        delta = cur_mae - basket[best][0]
        if delta >= MIN_IMPROVEMENT:
            lines.append(f"  CONSIDER: bias {best[0]}, 1/MAE^{best[1]} beats current by "
                         f"{delta:.4f} °C basket MAE ({basket[best][0]:.4f} vs "
                         f"{cur_mae:.4f}) — exceeds the {MIN_IMPROVEMENT} °C floor. "
                         f"Worth a human re-evaluation; do NOT auto-apply.")
        else:
            lines.append(f"  HOLD. Best challenger (bias {best[0]}, 1/MAE^{best[1]}) "
                         f"leads by only {delta:.4f} °C (< {MIN_IMPROVEMENT} floor) — "
                         f"noise, not signal. Keep current.")
    lines.append("")

    # Baseline drift.
    lines.append("BASELINE DRIFT (current variant basket MAE)")
    if cur_mae is None:
        lines.append("  no current-variant MAE today; baseline unchanged.")
    elif baseline is None:
        lines.append(f"  no baseline on file — writing today's {cur_mae:.4f} °C as the "
                     f"baseline.")
    else:
        prev = baseline.get("basket_mae_current")
        prev_date = baseline.get("date", "?")
        if prev is None:
            lines.append(f"  malformed baseline — rewriting with {cur_mae:.4f} °C.")
        else:
            drift = cur_mae - prev
            flag = "  ⚠ REGRESSION" if drift > REGRESSION_TOL else (
                   "  ✓ improved" if drift < -REGRESSION_TOL else "  (stable)")
            lines.append(f"  today {cur_mae:.4f} vs baseline {prev:.4f} "
                         f"(set {prev_date}) -> {drift:+.4f} °C{flag}")
    lines.append("")

    # Freshness summary.
    lines.append("DATA FRESHNESS (Meteostat archive lag drives the season downgrade)")
    gaps = [f.get("season_gap_days") for f in freshness.values()
            if f.get("season_gap_days") is not None]
    if gaps:
        n_out = sum(1 for g in gaps if g > 31)
        lines.append(f"  {n_out}/{len(gaps)} cities out-of-season (gap > 31d). "
                     f"max gap {max(gaps)}d, min {min(gaps)}d.")
    else:
        lines.append("  no freshness data resolved today.")
    lines.append("")
    lines.append(f"requests made this run: {total_requests} "
                 f"(across {len(BASKET)} per-city clients)")

    report = "\n".join(lines)
    print(report)

    # Persist: timestamped report + latest pointer. Baseline written only when
    # absent (first run) — never silently moved, so drift stays measurable.
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / f"healthcheck_{today.isoformat()}.txt").write_text(report + "\n")
    (REPORTS / "latest.txt").write_text(report + "\n")
    if cur_mae is not None and baseline is None:
        BASELINE.write_text(json.dumps(
            {"basket_mae_current": cur_mae, "date": today.isoformat(),
             "variant": list(cur)}, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
