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
  3. Sweeps OUTLIER_FLOOR_C (the member-rejection floor) on the outlier-screened
     blend, re-justifying the committed 4.0 °C against fresh data under the same
     MIN_IMPROVEMENT noise floor.
  4. Validates the DISP confidence thresholds (DISP_NORMAL / DISP_ELEVATED) by
     checking that held-out error actually rises with member dispersion — i.e.
     the tiers still discriminate — and flags it if the ordering breaks.
  5. Compares the current method's basket MAE to the stored baseline and flags
     drift/regression beyond REGRESSION_TOL °C.
  6. Checks Meteostat bulk-archive freshness per city (the lag that drives the
     out-of-season confidence downgrade) and flags changes.
  7. Settles logged council-vs-market snapshots against the anchor station and
     prints the C7 realized-outcome edge verdict (read-only).

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
from weather_council.council import (Council, WEIGHT_POWER,  # noqa: E402
                                     OUTLIER_FLOOR_C, DISP_NORMAL, DISP_ELEVATED,
                                     _weighted_std)
from weather_council.edge import report_lines as edge_report_lines  # noqa: E402
from weather_council.edge import score_snapshots  # noqa: E402
from weather_council.scoring import crps_sample, interval_coverage  # noqa: E402
from weather_council.storage import (fetch_settled_snapshots,  # noqa: E402
                                     settle_market_snapshots)

CRPS_MIN = 10               # residuals needed before a predictive CRPS is trusted

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
CURRENT_FLOOR = OUTLIER_FLOOR_C  # live member-outlier rejection floor (°C)

# Candidate outlier-rejection floors to re-justify CURRENT_FLOOR on fresh data.
# A challenger only "wins" if it beats current basket MAE by MIN_IMPROVEMENT °C,
# the same noise floor the bias/power sweep uses. CURRENT_FLOOR MUST appear here.
OUTLIER_FLOORS = sorted({3.0, 4.0, 5.0, 6.0, float(CURRENT_FLOOR)})

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
    """Rolling-origin held-out MAE, hit-rate(±2°C), and PROBABILISTIC skill for
    one variant. The probabilistic part dresses each held-out day with only the
    residuals of strictly-earlier held-out days (leak-free) and scores it with
    CRPS — a strictly proper rule — against a dressed-climatology baseline, plus
    the empirical 80% interval coverage. This re-checks daily that the council's
    *distribution* (the bucket probabilities it sells), not just its point, still
    beats the naive baseline and stays calibrated.

    Returns (mae, hit_rate, n, crps_skill_vs_climatology, coverage_80)."""
    dates = sorted(observed)
    test = dates[WARMUP:]
    if len(test) < 5:
        return None, None, 0, None, None
    errs, hits, n = [], 0, 0
    resid = {"high": [], "low": []}          # signed council residuals, in order
    clim_resid = {"high": [], "low": []}     # signed climatology residuals, in order
    crps_c_sum = crps_clim_sum = 0.0
    crps_count = cover_hits = cover_count = 0
    for i, d in enumerate(test):
        obs = observed.get(d)
        if obs is None:
            continue
        train = set(dates[:WARMUP + i])
        clim = {"high": statistics.mean(observed[t][0] for t in train),
                "low": statistics.mean(observed[t][1] for t in train)}
        for attr, idx in (("high", 0), ("low", 1)):
            pred = _blend_on_date(votes, attr, d, train, bias_method, power)
            if pred is None:
                continue
            e = abs(pred - obs[idx])
            errs.append(e)
            hits += 1 if e <= 2.0 else 0
            n += 1
            r = obs[idx] - pred
            rc = obs[idx] - clim[attr]
            pr, pc = resid[attr], clim_resid[attr]
            if len(pr) >= CRPS_MIN and len(pc) >= CRPS_MIN:
                crps_c_sum += crps_sample(pr, r)
                crps_clim_sum += crps_sample(pc, rc)
                crps_count += 1
                covered, _w = interval_coverage(pr, r)
                cover_hits += 1 if covered else 0
                cover_count += 1
            pr.append(r)
            pc.append(rc)
    skill = (1.0 - crps_c_sum / crps_clim_sum
             if crps_count and crps_clim_sum > 0 else None)
    cover = (cover_hits / cover_count) if cover_count else None
    return (statistics.mean(errs) if errs else None,
            (hits / n) if n else None, n, skill, cover)


def _screened_blend_on_date(votes, attr, day, train, bias_method, power, floor):
    """One held-out day's blend that MIRRORS the live council `_blend`: learn each
    member's bias + skill-weight from `train` only, bias-correct, then apply the
    member-outlier screen — exclude any member whose corrected value sits more
    than max(`floor`, 3·MAD) from the panel median — before the skill-weighted
    average. Returns (prediction, within-system dispersion, n_included), where
    dispersion is the weight-aware σ of the *included* members (the same
    `_weighted_std` axis the live confidence tier reads). None if too sparse.

    Unlike `_blend_on_date`, this one applies the outlier floor, so it is the
    faithful engine for re-justifying OUTLIER_FLOOR_C and for relating dispersion
    to held-out error."""
    cands: list[tuple[float, float]] = []          # (corrected_value, weight)
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
        cands.append((series[day][0] - bias, w))
    if not cands:
        return None, None, 0
    vals = [c for c, _ in cands]
    median = statistics.median(vals)
    mad = statistics.median([abs(x - median) for x in vals]) or 0.0
    thresh = max(floor, 3 * mad)
    included = [(c, w) for c, w in cands if abs(c - median) <= thresh]
    if not included:                               # everyone disagreed wildly; keep all
        included = cands
    wsum = sum(w for _, w in included) or 1.0
    pred = sum(c * w for c, w in included) / wsum
    return pred, _weighted_std(included), len(included)


def _walk_forward_screened(votes, observed, bias_method, power, floor):
    """Rolling-origin held-out MAE / hit-rate(±2°C) using the outlier-screened
    blend, plus the per-day (|error|, dispersion) pairs the DISP-threshold check
    needs. Leak-free: each day learns only from strictly-earlier observed days.
    Returns (mae, hit_rate, n, disp_pairs)."""
    dates = sorted(observed)
    test = dates[WARMUP:]
    if len(test) < 5:
        return None, None, 0, []
    errs, hits, n = [], 0, 0
    disp_pairs: list[tuple[float, float]] = []     # (abs_error, dispersion)
    for i, d in enumerate(test):
        obs = observed.get(d)
        if obs is None:
            continue
        train = set(dates[:WARMUP + i])
        for attr, idx in (("high", 0), ("low", 1)):
            pred, disp, _ninc = _screened_blend_on_date(
                votes, attr, d, train, bias_method, power, floor)
            if pred is None:
                continue
            e = abs(pred - obs[idx])
            errs.append(e)
            hits += 1 if e <= 2.0 else 0
            n += 1
            if disp is not None:
                disp_pairs.append((e, disp))
    return (statistics.mean(errs) if errs else None,
            (hits / n) if n else None, n, disp_pairs)


def _city_convergence(council, fp, target, votes, observed, c7_validated):
    """Run the recommend-only mechanism-convergence layer for one basket city by
    calling the council's OWN methods — the same path deliberate() takes — so the
    health-check tally can never diverge from what a live verdict reports. Adds
    only the council's blend/naive/validate (pure) and one climatology fetch for
    the seasonal normal. Returns {"high": Convergence, "low": Convergence} (either
    may be None) or None when the blend has no usable members."""
    high, _ih, _sh, _wh = council._blend(votes, "high")
    low, _il, _sl, _wl = council._blend(votes, "low")
    naive_h = council._naive(votes, "high")
    naive_l = council._naive(votes, "low")
    validation = council._validate(votes, observed)
    records = council._records(fp, target, round(high, 1))
    ci = council._convergence(observed, high, low, naive_h, naive_l,
                              records, validation, target)
    if not ci:
        return None
    return {
        "high": ci["high"].decide(c7_validated) if ci.get("high") else None,
        "low": ci["low"].decide(c7_validated) if ci.get("low") else None,
    }


def _diagnose_no_holdout(votes, observed):
    """When a city scores n=0 held-out days, say WHY — from data already fetched,
    so an opaque blank line becomes an actionable signal. Distinguishes a
    transient fetch failure (throttle/DNS) from a genuine station↔forecast archive
    overlap gap from a real blend defect. Returns a short human reason string.

    `max_pairs` is the most paired (forecast, observed) days any single member has
    over the truth window — the precondition for ANY held-out prediction, since
    _blend_on_date needs ≥5 training pairs and the walk-forward needs > WARMUP
    observed days before the first testable origin."""
    floor = WARMUP + 5
    counts = [min(sum(1 for d in v.hist_high if d in observed),
                  sum(1 for d in v.hist_low if d in observed)) for v in votes]
    max_pairs = max(counts) if counts else 0
    with_hist = sum(1 for c in counts if c > 0)
    bad_notes = [n for v in votes for n in v.notes
                 if "unavailable" in n or "rate-limited" in n]
    if max_pairs == 0:
        if bad_notes:
            return ("no paired forecast history — fetches failed "
                    f"(e.g. \"{bad_notes[0][:60]}\"); transient, retry")
        return "no paired forecast history (no member returned usable archive days)"
    if max_pairs < floor:
        return (f"only {max_pairs} paired archive day(s) across {with_hist} member(s) "
                f"(< {floor} floor) — station truth and the forecast archive barely "
                f"overlap; backtest can't run on station truth in this window")
    if len(observed) <= WARMUP:
        return f"{len(observed)} observed truth day(s) (≤ warmup {WARMUP}); window too short"
    return (f"{max_pairs} paired day(s) present but the blend produced no held-out "
            f"prediction — investigate (possible defect)")


def _persist(report, today, usable_cities, cur_mae, cur, baseline_absent,
             reports_dir=REPORTS, baseline_path=BASELINE):
    """Write the audit report + 'latest' pointer + first-run baseline.

    A *degraded* run — zero cities produced a usable held-out MAE because every
    member was throttled / DNS-failed — is a network outage, not a health
    verdict. It must never clobber the last good ``latest.txt`` or move the
    baseline; otherwise a transient outage erases the operator's last known-good
    signal. The outage is still recorded in the dated audit file (only if today
    has no report yet, so a good earlier run on the same day is preserved).

    Returns True if this run was persisted as the latest signal, else False.
    """
    reports_dir.mkdir(exist_ok=True)
    dated = reports_dir / f"healthcheck_{today.isoformat()}.txt"
    if usable_cities == 0:                      # degraded — preserve last good
        if not dated.exists():
            dated.write_text(report + "\n")
        return False
    dated.write_text(report + "\n")
    (reports_dir / "latest.txt").write_text(report + "\n")
    if cur_mae is not None and baseline_absent:
        baseline_path.write_text(json.dumps(
            {"basket_mae_current": cur_mae, "date": today.isoformat(),
             "variant": list(cur)}, indent=2))
    return True


def _city_votes(city, target):
    """Resolve truth + collect each member's votes for one city. Uses a FRESH
    Council (and thus a fresh sandbox request budget) per city, since one client
    across the whole basket would exceed MAX_REQUESTS_PER_RUN. Returns
    (council, fp, observed, votes, freshness_dict, requests_made) or raises.

    `council` and `fp` are returned so the convergence layer can call the
    council's OWN blend/naive/validate/records/_convergence methods on the same
    fetched data — guaranteeing the health-check tally matches a live verdict."""
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
    return council, fp, observed, votes, fresh, council.sources.http.requests_made


VARIANTS = [("mean", 1), ("median", 1), ("mean", 2), ("median", 2)]


def main() -> int:
    today = dt.date.today()
    target = today

    per_city = {}
    no_holdout_reason = {}                 # city -> why n=0, when it happens
    basket_acc = {v: [] for v in VARIANTS}
    basket_hit = {v: [] for v in VARIANTS}
    basket_skill = {v: [] for v in VARIANTS}
    basket_cover = {v: [] for v in VARIANTS}
    basket_floor = {f: [] for f in OUTLIER_FLOORS}   # outlier-floor -> [per-city MAE]
    disp_pairs_all: list[tuple[float, float]] = []   # (|error|, dispersion), current config
    freshness = {}
    convergence_by_city = {}                          # city -> {"high":Conv,"low":Conv}
    total_requests = 0

    # Whether C7 realized-outcome calibration has earned a validated edge yet
    # (DB read only, no network). Gates whether the convergence layer's nudge is
    # ever ALLOWED to move a headline; until then it is annotation only.
    try:
        c7_validated = score_snapshots(fetch_settled_snapshots()).is_edge_validated
    except Exception:
        c7_validated = False

    for city in BASKET:
        try:
            council, fp, observed, votes, fresh, reqs = _city_votes(city, target)
            total_requests += reqs
        except Exception as exc:                       # one city must not kill the run
            per_city[city] = {"error": str(exc)}
            continue
        freshness[city] = fresh
        res = {}
        for variant in VARIANTS:
            mae, hit, n, skill, cover = _walk_forward(votes, observed, *variant)
            res[variant] = (mae, hit, n, skill, cover)
            if mae is not None:
                basket_acc[variant].append(mae)
            if hit is not None:
                basket_hit[variant].append(hit)
            if skill is not None:
                basket_skill[variant].append(skill)
            if cover is not None:
                basket_cover[variant].append(cover)
        per_city[city] = res
        if res[(CURRENT_BIAS, CURRENT_POWER)][2] == 0:   # n==0 on the live variant
            no_holdout_reason[city] = _diagnose_no_holdout(votes, observed)

        # OUTLIER_FLOOR_C sweep (current bias/power held fixed) + dispersion
        # collection at the live config, both on the outlier-screened blend.
        for fl in OUTLIER_FLOORS:
            fmae, _fhit, _fn, dpairs = _walk_forward_screened(
                votes, observed, CURRENT_BIAS, CURRENT_POWER, fl)
            if fmae is not None:
                basket_floor[fl].append(fmae)
            if fl == CURRENT_FLOOR:
                disp_pairs_all.extend(dpairs)

        # Recommend-only mechanism convergence: do the independent mechanisms
        # corroborate this city's headline today? (Never moves anything.)
        try:
            conv = _city_convergence(council, fp, target, votes, observed,
                                     c7_validated)
            if conv:
                convergence_by_city[city] = conv
        except Exception:
            pass                                        # never let it abort the run

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
        mae, hit, n = r.get(cur, (None, None, 0, None, None))[:3]
        f = freshness.get(city, {})
        mae_s = f"{mae:.3f}" if mae is not None else "  -  "
        hit_s = f"{hit*100:.0f}%" if hit is not None else " - "
        lines.append(f"  {city:12} MAE {mae_s}  hit {hit_s:>4}  n={n:3} | "
                     f"truth={f.get('kind','?')} end={f.get('window_end','?')} "
                     f"season_gap={f.get('season_gap_days','?')}d")
        if n == 0 and city in no_holdout_reason:
            lines.append(f"               └─ n=0 because: {no_holdout_reason[city]}")
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

    # Probabilistic calibration of the CURRENT variant (the distribution the
    # verdict actually sells as bucket probabilities), scored with a proper rule.
    skills = basket_skill[cur]
    covers = basket_cover[cur]
    lines.append("PROBABILISTIC CALIBRATION (current variant — proper scoring rule)")
    if skills:
        mskill = statistics.mean(skills)
        lines.append(f"  CRPS skill vs dressed climatology: {mskill*100:+.1f}% "
                     f"(mean over {len(skills)} cities; >0 = the distribution beats climatology)")
        if mskill <= 0:
            lines.append("  ⚠ probabilistic skill collapsed — the council's distribution no "
                         "longer beats climatology. Investigate before trusting bucket probs.")
    else:
        lines.append("  insufficient held-out residuals for CRPS today.")
    if covers:
        mcov = statistics.mean(covers) * 100
        cal = ("well-calibrated" if 75 <= mcov <= 85
               else "OVER-CONFIDENT (under-dispersed)" if mcov < 75
               else "under-confident (over-dispersed)")
        flag = "" if 70 <= mcov <= 90 else "  ⚠"
        lines.append(f"  80% interval empirical coverage: {mcov:.1f}% — {cal}{flag}")
        if mcov < 70:
            lines.append("    RECOMMENDATION: intervals are too narrow on fresh data; a human "
                         "should consider widening the predictive spread (do NOT auto-apply).")
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

    # OUTLIER_FLOOR_C sweep — re-justify the member-rejection floor on fresh data.
    floor_mae = {f: (statistics.mean(v) if v else None) for f, v in basket_floor.items()}
    lines.append("OUTLIER FLOOR SWEEP (basket-averaged held-out MAE, screened blend)")
    ranked_floors = sorted((f for f in OUTLIER_FLOORS if floor_mae[f] is not None),
                           key=lambda f: floor_mae[f])
    for f in OUTLIER_FLOORS:
        m = floor_mae[f]
        tag = "  <- CURRENT" if f == CURRENT_FLOOR else ""
        m_s = f"{m:.4f}" if m is not None else "  -   "
        ncity = len(basket_floor[f])
        lines.append(f"  floor {f:>4.1f} °C   MAE {m_s}  cities={ncity}{tag}")
    cur_floor_mae = floor_mae.get(CURRENT_FLOOR)
    best_floor = ranked_floors[0] if ranked_floors else None
    if best_floor is None or cur_floor_mae is None:
        lines.append("  insufficient data to evaluate the outlier floor today.")
    elif best_floor == CURRENT_FLOOR:
        lines.append(f"  HOLD. Current floor {CURRENT_FLOOR:.1f} °C is still best on the "
                     f"basket (MAE {cur_floor_mae:.4f}). No change recommended.")
    else:
        fdelta = cur_floor_mae - floor_mae[best_floor]
        if fdelta >= MIN_IMPROVEMENT:
            lines.append(f"  CONSIDER: floor {best_floor:.1f} °C beats current by "
                         f"{fdelta:.4f} °C basket MAE ({floor_mae[best_floor]:.4f} vs "
                         f"{cur_floor_mae:.4f}) — exceeds the {MIN_IMPROVEMENT} °C floor. "
                         f"Worth a human re-evaluation of OUTLIER_FLOOR_C; do NOT auto-apply.")
        else:
            lines.append(f"  HOLD. Best floor {best_floor:.1f} °C leads by only "
                         f"{fdelta:.4f} °C (< {MIN_IMPROVEMENT} floor) — noise, not signal. "
                         f"Keep OUTLIER_FLOOR_C at {CURRENT_FLOOR:.1f} °C.")
    lines.append("")

    # DISP-threshold validation — do the confidence tiers still discriminate?
    # A tier earns its place only if held-out error rises as dispersion rises.
    # NOTE: this uses the WITHIN-SYSTEM dispersion axis (the weighted σ of the
    # blended members) — the dominant, leak-free-reconstructible component of the
    # live `effective` uncertainty, which also folds in cross-system and spatial
    # terms in quadrature. So this validates the tier *ordering*, not the exact
    # numeric cutoffs against the full quadrature.
    lines.append("DISP THRESHOLD CHECK (does dispersion tier predict held-out error?)")
    tiers = [("normal   (≤%.1f)" % DISP_NORMAL,
              [e for e, d in disp_pairs_all if d <= DISP_NORMAL]),
             ("elevated (≤%.1f)" % DISP_ELEVATED,
              [e for e, d in disp_pairs_all if DISP_NORMAL < d <= DISP_ELEVATED]),
             ("high     (>%.1f)" % DISP_ELEVATED,
              [e for e, d in disp_pairs_all if d > DISP_ELEVATED])]
    tier_means = []
    for name, es in tiers:
        if es:
            mae_t = statistics.mean(es)
            tier_means.append(mae_t)
            lines.append(f"  {name}: MAE {mae_t:.3f}  n={len(es)}")
        else:
            tier_means.append(None)
            lines.append(f"  {name}: (no held-out days in this tier today)")
    present = [m for m in tier_means if m is not None]
    if len(present) >= 2:
        monotone = all(a <= b for a, b in zip(present, present[1:]))
        if monotone:
            lines.append("  ✓ tiers ordered: held-out error rises with dispersion — "
                         f"DISP_NORMAL={DISP_NORMAL}, DISP_ELEVATED={DISP_ELEVATED} still "
                         "discriminate on this axis. HOLD.")
        else:
            lines.append("  ⚠ tiers NOT ordered: a higher-dispersion tier is not less "
                         "accurate — the current cutoffs may no longer discriminate. "
                         "RECOMMENDATION: a human should re-examine DISP_NORMAL/"
                         "DISP_ELEVATED (do NOT auto-apply).")
    else:
        lines.append("  insufficient tier coverage to validate the thresholds today.")
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

    # Mechanism convergence (recommend-only): across the basket, do the
    # independent mechanisms corroborate each city's headline, and where do they
    # diverge? CONTESTED is not a defect — it flags a genuine forecast signal the
    # seasonal/persistence baselines do not back, i.e. an unusual day. Any nudge is
    # annotation only and is NOT applied (and not even allowed to move a headline
    # until C7 validates a realized-outcome edge).
    lines.append("MECHANISM CONVERGENCE (independent corroboration of the headline — recommend-only)")
    gate = ("C7 edge validated — a significant nudge would be ALLOWED to move a headline"
            if c7_validated else
            "C7 edge NOT yet validated — every nudge below is annotation only")
    lines.append(f"  gate: {gate}.")
    if not convergence_by_city:
        lines.append("  no city produced enough held-out support to run convergence today.")
    else:
        tally = {"AFFIRMED": 0, "AFFIRMED_NUDGE": 0, "CONTESTED": 0, "ABSTAIN": 0}
        contested, nudges = [], []
        for city, conv in sorted(convergence_by_city.items()):
            for attr in ("high", "low"):
                c = conv.get(attr)
                if c is None:
                    continue
                tally[c.status] = tally.get(c.status, 0) + 1
                if c.status == "CONTESTED":
                    contested.append(f"{city} {attr} (affirmation {c.affirmation:.0f}/100)")
                elif c.status == "AFFIRMED_NUDGE":
                    nudges.append(f"{city} {attr}: {c.nudge_c:+.2f} °C toward "
                                  f"{c.affirmed_c:.1f} (affirmation {c.affirmation:.0f}/100)")
        lines.append(f"  tally: {tally['AFFIRMED']} affirmed, "
                     f"{tally['AFFIRMED_NUDGE']} affirmed-with-nudge, "
                     f"{tally['CONTESTED']} contested, {tally['ABSTAIN']} abstained "
                     f"(per city-quantity).")
        if contested:
            lines.append("  CONTESTED (headline not corroborated by independent baselines — "
                         "likely an unusual/event-driven day, treat as lower-confidence):")
            for c in contested:
                lines.append(f"    - {c}")
        if nudges:
            lines.append("  RECOMMENDATION (convergence nudges — NOT applied, human review):")
            for c in nudges:
                lines.append(f"    - {c}")
        if not contested and not nudges:
            lines.append("  all evaluated headlines are affirmed within the convergence band; "
                         "no nudges or contests today.")
    lines.append("")

    # C7 — realized-outcome edge (council vs market, settled days). Read-only and
    # recommend-only: settle any logged market snapshots whose day has passed
    # against the verdict's OWN anchor station, then grade both forecasters with
    # strictly-proper scores. This is the only block that scores against realized
    # market outcomes; until ≥20 settled days accumulate it honestly reads
    # UNVALIDATED. It never sizes a position, prices an order, or moves funds.
    lines.append("C7 — REALIZED-OUTCOME EDGE (council vs market — settled days, read-only)")
    try:
        settled = settle_market_snapshots()
        report = score_snapshots(fetch_settled_snapshots())
        if settled:
            lines.append(f"  settled {len(settled)} snapshot(s) this run against the anchor station.")
        # edge_report_lines[0] is the report's own title — redundant with the
        # section header above, so skip it and keep just the body (already
        # indented; nest it one level deeper under this section).
        for el in edge_report_lines(report)[1:]:
            lines.append("  " + el.strip() if el.strip() else el)
    except Exception as exc:                       # never let C7 abort the health check
        lines.append(f"  C7 unavailable this run ({exc}); calibration unchanged.")
    lines.append("")

    lines.append(f"requests made this run: {total_requests} "
                 f"(across {len(BASKET)} per-city clients)")

    report = "\n".join(lines)
    print(report)

    # Persist. A degraded run (no usable city) must not clobber the last good
    # 'latest' pointer or move the baseline — see _persist. Baseline is written
    # only on the first good run; never silently moved, so drift stays measurable.
    usable_cities = len(basket_acc[cur])
    if not _persist(report, today, usable_cities, cur_mae, cur, baseline is None):
        print(f"[degraded run: {usable_cities}/{len(BASKET)} cities usable — "
              f"latest.txt and baseline preserved from last good run]")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
