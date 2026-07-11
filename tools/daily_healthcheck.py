#!/usr/bin/env python3
"""Daily, data-driven health check for the weather council.

What this is — and is NOT:

  * It RE-EARNS the project's tuned choices on fresh data every day and writes a
    report a human can read. It is a *monitor and recommender*, never an editor.
  * It NEVER edits source, never trades, never moves funds, never touches the
    market/order side of anything. It only reads weather/observation archives
    through the project's own sandboxed client and writes a text report.

Each run:
  1. Walk-forward backtests the council across the basket (BASKET = Manila +
     Singapore, each pinned to its exact settlement station; rolling-origin, no
     future leakage), exactly the evaluation the live verdict uses.
  2. Compares the four weighting/bias variants (bias mean|median × 1/MAE^1|^2)
     so the committed choice (mean bias, 1/MAE^2) is re-justified — or challenged
     — on today's data. A challenger is surfaced only if it beats current on the
     basket by ≥ MIN_IMPROVEMENT °C AND a seeded paired bootstrap over the
     per-city deltas puts the 90% CI above 0 — so noise is not mistaken for signal.
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
  8. Logs a TRACKED, non-council forecaster (Weatherbit) alongside the council
     for each city and settles past entries against the same anchored truth, so a
     forecaster with no backtestable archive earns a measured head-to-head record
     PROSPECTIVELY. It is never voted into the blend; a sustained win only ever
     surfaces as a promotion *candidate* for a human. No-ops without an API key.

Recommendations are printed for human review. Applying them is a human decision.
"""

from __future__ import annotations

import datetime as dt
import json
import random
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from weather_council.agents import MIN_SAMPLES          # noqa: E402
from weather_council.council import (Council, WEIGHT_POWER,  # noqa: E402
                                     OUTLIER_FLOOR_C, DISP_NORMAL, DISP_ELEVATED,
                                     _weighted_std)
from weather_council.edge import report_lines as edge_report_lines  # noqa: E402
from weather_council.edge import score_snapshots  # noqa: E402
from weather_council.failures import soft_failure_counts, recent_soft_failures  # noqa: E402
from weather_council.scoring import crps_sample, interval_coverage, pit  # noqa: E402
from weather_council.spread_skill import spread_skill_eval  # noqa: E402
from weather_council.ensemble_verification import (rank_histogram_eval,  # noqa: E402
                                                   pit_calibration_eval)
from weather_council.storage import (fetch_settled_snapshots,  # noqa: E402
                                     log_market_snapshot, settle_market_snapshots,
                                     log_tracked_forecast, settle_tracked_forecasts,
                                     tracked_forecast_scores, book_snapshot_coverage)
from tools.book_logger import capture_for_place           # noqa: E402
# The REAL live model-vs-market comparison path. Reused verbatim (not
# reimplemented) so the bucket probabilities the health check logs are byte-for-byte
# what a live verdict would log — the only way C7's settled grading stays honest.
from run import _build_comparison                       # noqa: E402

CRPS_MIN = 10               # residuals needed before a predictive CRPS is trusted

# The basket — narrowed to the two markets we actually settle against, each pinned
# to its exact settlement station: Manila -> RPLL and Singapore -> WSSS (the WU
# oracle per city). Accuracy here means matching THESE two stations' published
# daily highs, so the basket is the settlement set, not a diversity sweep.
#
# Trade-off, stated honestly: with only two cities the cross-city paired bootstrap
# (BOOT_*) has just two per-city deltas to resample, so its 90% CI is effectively
# "did BOTH cities agree" — it can no longer distinguish a real constant-change
# from a two-city coincidence with any power. The challenger gate therefore
# becomes deliberately near-impossible to clear; read a surfaced CONSIDER on a
# 2-city basket as "look harder", never as statistical proof. Re-widen the basket
# if you want the constants re-justified with real significance.
BASKET = ["Manila", "Singapore"]

WINDOW = 120                 # days of history per city (bounded by archive lag)
WARMUP = MIN_SAMPLES         # walk-forward warmup = the live validation warmup
MIN_IMPROVEMENT = 0.03       # °C a challenger must beat current by on the basket
REGRESSION_TOL = 0.05        # °C of basket-MAE drift vs baseline worth flagging

# A challenger must clear TWO bars before it is ever surfaced as CONSIDER:
#  (1) practical: beat current by ≥ MIN_IMPROVEMENT °C basket MAE, AND
#  (2) statistical: a seeded paired bootstrap over the per-city MAE deltas must
#      put the 90% CI entirely above 0 — i.e. the win is not an artefact of which
#      cities happened to be in season. This replaces the old bare fixed-threshold
#      rule, where "beats by 0.03 °C" was asserted, never tested.
BOOT_ITERS = 2000            # bootstrap resamples (seeded → reproducible verdict)
BOOT_CI = 0.90               # central CI width on the mean per-city MAE delta
BOOT_SEED = 20260607         # fixed seed: same data → same recommendation

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
# Machine-readable summary of the latest good run, so the LIVE verdict (run.py)
# can surface the monitor's status as a read-only banner. Recommend-only: these
# are findings the verdict DISPLAYS, never values that move a forecast.
STATUS = REPORTS / "healthcheck_status.json"


def _blend_on_date(votes, attr, day, train, bias_method, power):
    """One held-out day's blend under a chosen (bias_method, power), using only
    `train` dates to learn each member's bias and weight. Returns
    (prediction, corrected_members) where `corrected_members` is the raw
    bias-corrected member panel for this day — surfaced (recommend-only) so the
    walk-forward can build the rank histogram, exactly as council._blend_on_date
    does for the live verdict. Returns (None, []) if too sparse."""
    num = den = 0.0
    corrected = []          # raw bias-corrected member forecasts for this day
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
        corrected.append(series[day][0] - bias)
    return ((num / den) if den > 0 else None), corrected


def _walk_forward(votes, observed, bias_method, power):
    """Rolling-origin held-out MAE, hit-rate(±2°C), and PROBABILISTIC skill for
    one variant. The probabilistic part dresses each held-out day with only the
    residuals of strictly-earlier held-out days (leak-free) and scores it with
    CRPS — a strictly proper rule — against a dressed-climatology baseline, plus
    the empirical 80% interval coverage. This re-checks daily that the council's
    *distribution* (the bucket probabilities it sells), not just its point, still
    beats the naive baseline and stays calibrated.

    Also returns the ACCURACY/PRECISION decomposition of the point error: the
    mean SIGNED error (bias — are the darts centred on the bullseye?) and the
    spread σ of those signed errors (precision — are the darts tightly grouped?).
    These are the two axes MAE conflates; RMSE² = bias² + σ². A city can be
    off-centre, scattered, or both, and you fix each differently.

    Finally returns the leak-free ENSEMBLE-CALIBRATION inputs (recommend-only),
    the same two the live council._validate now emits: `rank_inputs`, the
    per-day (bias-corrected member panel, observation) pairs the rank histogram
    consumes to ask whether the raw panel's dispersion is the right SIZE; and
    `pits`, each the PIT of the held-out outcome through ONLY the
    strictly-earlier residual cloud — the same distribution scored by CRPS — so
    the PIT histogram can ask whether the SERVED distribution is calibrated.

    Returns (mae, hit_rate, n, crps_skill_vs_climatology, coverage_80, bias, σ,
             rank_inputs, pits)."""
    dates = sorted(observed)
    test = dates[WARMUP:]
    if len(test) < 5:
        return None, None, 0, None, None, None, None, [], []
    errs, hits, n = [], 0, 0
    signed: list[float] = []                 # signed errors (obs − pred): accuracy + precision
    resid = {"high": [], "low": []}          # signed council residuals, in order
    clim_resid = {"high": [], "low": []}     # signed climatology residuals, in order
    rank_inputs: list[tuple[list[float], float]] = []   # (corrected panel, obs) per day
    pits: list[float] = []                   # leak-free PIT of obs through earlier cloud
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
            pred, members = _blend_on_date(votes, attr, d, train, bias_method, power)
            if pred is None:
                continue
            e = abs(pred - obs[idx])
            errs.append(e)
            hits += 1 if e <= 2.0 else 0
            n += 1
            r = obs[idx] - pred
            signed.append(r)                 # accuracy=mean(signed); precision=pstdev(signed)
            if len(members) >= 2:            # raw panel for the rank histogram
                rank_inputs.append((members, obs[idx]))
            rc = obs[idx] - clim[attr]
            pr, pc = resid[attr], clim_resid[attr]
            if len(pr) >= CRPS_MIN and len(pc) >= CRPS_MIN:
                crps_c_sum += crps_sample(pr, r)
                crps_clim_sum += crps_sample(pc, rc)
                crps_count += 1
                covered, _w = interval_coverage(pr, r)
                cover_hits += 1 if covered else 0
                cover_count += 1
                # PIT of today's residual through ONLY strictly-earlier residuals
                # — the same leak-free cloud CRPS scores and compare.py resamples.
                pits.append(pit(pr, r))
            pr.append(r)
            pc.append(rc)
    skill = (1.0 - crps_c_sum / crps_clim_sum
             if crps_count and crps_clim_sum > 0 else None)
    cover = (cover_hits / cover_count) if cover_count else None
    bias = statistics.mean(signed) if signed else None
    spread = statistics.pstdev(signed) if len(signed) >= 2 else None
    return (statistics.mean(errs) if errs else None,
            (hits / n) if n else None, n, skill, cover, bias, spread,
            rank_inputs, pits)


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


def _city_market_snapshot(council, place, fp, target, votes, observed, truth):
    """Recommend-only model-vs-market: place the council's bucket distribution
    beside the live Polymarket market for this city/day via the SAME
    run._build_comparison path a live verdict uses, then persist the snapshot so
    the C7 realized-outcome scorer can grade it once the day settles. This is the
    only thing that GROWS C7's settled set day over day — without it the edge
    verdict reads "accumulating forward" forever.

    Budget-safe by construction: it reuses the votes/observed already fetched and
    derives high/included/weights/validation from the council's OWN pure
    _blend/_validate (no extra council fetch). The only new requests are the
    read-only Polymarket fetch (+ a Meteostat offset probe for a sub-degree
    market). Measured cost on the tightest city (HK) is ~2 requests, far under the
    per-city budget.

    The minimal Verdict-shim carries exactly the fields _build_comparison,
    applied_bias_correction and log_market_snapshot read — and crucially the
    LOGGED bucket probabilities are computed from residuals+market alone, so they
    are identical to a live verdict's (settlement is left None only because it
    feeds a provenance *annotation*, never the probabilities). Returns
    (comparison, note): a VerdictMarketComparison when a market matched and the
    grain is supported (and the snapshot was logged), else `note` explains why it
    was withheld — e.g. HK's sub-degree Observatory settlement — or None when no
    market matched. NEVER sizes a position, prices an order, or moves funds."""
    high, inc_h, _sh, wts_h = council._blend(votes, "high")
    low, inc_l, _sl, wts_l = council._blend(votes, "low")
    validation = council._validate(votes, observed)
    shim = SimpleNamespace(
        high=round(high, 1), low=round(low, 1),
        included_high=inc_h, included_low=inc_l,
        weights_high=wts_h, weights_low=wts_l,
        votes=votes, validation=validation,
        settlement=None, truth_source=truth,
        place=fp, target=target.isoformat(),
    )
    comparison, note = _build_comparison(council.sources, shim, place, target)
    if comparison is not None:
        issued_at = log_market_snapshot(shim, comparison)   # recommend-only ledger
        # Read-only: archive the executable order book at the SAME instant (focus
        # cities only, e.g. Singapore in this basket) so depth-walk P&L can later be
        # measured against the mid. Never lets a book fetch abort the health check.
        try:
            capture_for_place(council.sources, place, target, issued_at)  # target: dt.date
        except Exception:
            pass
    return comparison, note


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


def _write_status(status: dict, status_path=STATUS):
    """Persist the compact, machine-readable health-check summary the live verdict
    reads. Written only on a good (non-degraded) run, alongside latest.txt, so a
    transient outage never overwrites the operator's last known-good status."""
    status_path.write_text(json.dumps(status, indent=2) + "\n")


def _city_votes(city, target):
    """Resolve truth + collect each member's votes for one city. Uses a FRESH
    Council (and thus a fresh sandbox request budget) per city, since one client
    across the whole basket would exceed MAX_REQUESTS_PER_RUN. Returns
    (council, place, fp, observed, votes, freshness_dict, truth_source) or raises.

    `council`, `place`, `fp` and the full `truth_source` are returned so two
    recommend-only layers can run on this SAME fetched data with no extra council
    fetch: (1) the convergence layer calls the council's OWN
    blend/naive/validate/records/_convergence methods; (2) the model-vs-market
    snapshot builds a minimal Verdict-shim and drives run._build_comparison. Both
    therefore match exactly what a live verdict reports. `place` is the geocoded
    city (what match_market / measure_settlement_offset key on); `fp` is the
    forecast/anchor point (what the verdict's own `.place` is)."""
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
    return council, place, fp, observed, votes, fresh, truth


VARIANTS = [("mean", 1), ("median", 1), ("mean", 2), ("median", 2)]


def _accuracy_precision(bias, spread):
    """Decompose a city's point error into its two dartboard axes — accuracy
    (bias: are the darts centred?) and precision (σ: are they tightly grouped?).
    Returns (rmse, bias_fraction, diagnosis), where bias_fraction = bias²/RMSE²
    is the share of squared error that is systematic. The accuracy/precision call
    is made on which axis DOMINATES (bias_fraction ≷ 0.5), a relative split — no
    arbitrary °C threshold, so it doesn't reintroduce a magic number."""
    rmse = (bias * bias + spread * spread) ** 0.5
    bfrac = (bias * bias) / (rmse * rmse) if rmse > 0 else 0.0
    if bfrac >= 0.5:
        diag = (f"accuracy-limited — {bfrac*100:.0f}% of the error is systematic "
                f"bias (reach for the bias term, not the spread)")
    else:
        diag = (f"precision-limited — {(1-bfrac)*100:.0f}% of the error is scatter "
                f"(reach for the dispersion model, not the bias)")
    return rmse, bfrac, diag


def _paired_city_deltas(cur_by_city, chal_by_city):
    """Per-city MAE deltas (current − challenger; >0 ⇒ challenger better) over the
    cities present for BOTH configs, so the comparison is apples-to-apples on the
    same basket and never credits a challenger for an easier city set."""
    return [cur_by_city[c] - chal_by_city[c]
            for c in cur_by_city if c in chal_by_city]


def _paired_bootstrap_ci(deltas, iters=BOOT_ITERS, ci=BOOT_CI, seed=BOOT_SEED):
    """Seeded paired bootstrap CI for the MEAN of per-city MAE deltas. Resamples
    cities with replacement so the interval reflects how much a basket-mean
    'improvement' rides on which cities happened to be in season today. Returns
    (point, lo, hi, n_pairs); lo/hi are None when a single pair can't be bounded.
    Deterministic — identical deltas yield an identical CI (so the same data
    always produces the same recommendation)."""
    n = len(deltas)
    if n == 0:
        return None, None, None, 0
    point = statistics.mean(deltas)
    if n == 1:
        return point, None, None, 1
    rng = random.Random(seed)
    means = []
    for _ in range(iters):
        means.append(statistics.mean(deltas[rng.randrange(n)] for _ in range(n)))
    means.sort()
    lo_q = (1.0 - ci) / 2.0
    lo = means[int(lo_q * (iters - 1))]
    hi = means[int((1.0 - lo_q) * (iters - 1))]
    return point, lo, hi, n


def main() -> int:
    run_t0 = time.monotonic()              # wall-clock of the whole run (metrics)
    today = dt.date.today()
    target = today

    per_city = {}
    no_holdout_reason = {}                 # city -> why n=0, when it happens
    basket_acc = {v: [] for v in VARIANTS}
    basket_hit = {v: [] for v in VARIANTS}
    basket_skill = {v: [] for v in VARIANTS}
    basket_cover = {v: [] for v in VARIANTS}
    basket_floor = {f: {} for f in OUTLIER_FLOORS}   # outlier-floor -> {city: per-city MAE}
    disp_pairs_all: list[tuple[float, float]] = []   # (|error|, dispersion), current config
    # Ensemble-calibration inputs pooled basket-wide at the LIVE variant only
    # (recommend-only): the raw member panels feed the rank histogram, the PIT
    # values feed the PIT histogram. Live variant only — these verify the served
    # configuration, not the challenger sweep.
    rank_inputs_all: list[tuple[list[float], float]] = []
    pit_values_all: list[float] = []
    freshness = {}
    convergence_by_city = {}                          # city -> {"high":Conv,"low":Conv}
    market_div = {}                                    # city -> VerdictMarketComparison
    market_withheld = {}                              # city -> reason a comparison was withheld
    snapshots_logged = 0                              # C7 ledger rows written this run
    tracked_logged = 0                                # weatherbit forecasts logged this run
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
            (council, place, fp, observed, votes,
             fresh, truth) = _city_votes(city, target)
        except Exception as exc:                       # one city must not kill the run
            per_city[city] = {"error": str(exc)}
            continue
        freshness[city] = fresh
        res = {}
        for variant in VARIANTS:
            (mae, hit, n, skill, cover, bias, spread,
             rank_inputs, pit_vals) = _walk_forward(votes, observed, *variant)
            res[variant] = (mae, hit, n, skill, cover, bias, spread)
            if mae is not None:
                basket_acc[variant].append(mae)
            if hit is not None:
                basket_hit[variant].append(hit)
            if skill is not None:
                basket_skill[variant].append(skill)
            if cover is not None:
                basket_cover[variant].append(cover)
            # Pool the ensemble-calibration inputs for the LIVE variant only.
            if variant == (CURRENT_BIAS, CURRENT_POWER):
                rank_inputs_all.extend(rank_inputs)
                pit_values_all.extend(pit_vals)
        per_city[city] = res
        if res[(CURRENT_BIAS, CURRENT_POWER)][2] == 0:   # n==0 on the live variant
            no_holdout_reason[city] = _diagnose_no_holdout(votes, observed)

        # OUTLIER_FLOOR_C sweep (current bias/power held fixed) + dispersion
        # collection at the live config, both on the outlier-screened blend.
        for fl in OUTLIER_FLOORS:
            fmae, _fhit, _fn, dpairs = _walk_forward_screened(
                votes, observed, CURRENT_BIAS, CURRENT_POWER, fl)
            if fmae is not None:
                basket_floor[fl][city] = fmae
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

        # Recommend-only model-vs-market: log today's council-vs-Polymarket
        # snapshot (so C7's settled set grows) and capture the live divergence.
        try:
            comparison, note = _city_market_snapshot(
                council, place, fp, target, votes, observed, truth)
            if comparison is not None:
                market_div[city] = comparison
                snapshots_logged += 1
            elif note:
                market_withheld[city] = note
        except Exception:
            pass                                        # never let it abort the run

        # Recommend-only TRACKED FORECASTER (Weatherbit): log its forecast for
        # today's target ALONGSIDE the council's own, so a forecaster with no
        # backtestable archive earns a measured, head-to-head record forward.
        # Silently no-ops when WEATHERBIT_API_KEY is unset; never votes.
        try:
            wb = council.sources.fetch_weatherbit_forecast(place, target)
            if wb is not None:
                c_high, *_ = council._blend(votes, "high")
                c_low, *_ = council._blend(votes, "low")
                ch = round(c_high, 1) if c_high is not None else None
                cl = round(c_low, 1) if c_low is not None else None
                log_tracked_forecast("weatherbit", place, target.isoformat(),
                                     wb[0], wb[1], ch, cl, truth)
                tracked_logged += 1
        except Exception:
            pass                                        # never let it abort the run

        # Per-city request total AFTER both recommend-only layers, so the budget
        # line reflects the real sandbox usage (must stay < MAX_REQUESTS_PER_RUN).
        total_requests += council.sources.http.requests_made

    # Per-city MAE keyed by city for each variant, so a challenger can be tested
    # against current on the SAME cities with a paired bootstrap — not a bare
    # difference of basket means. Cities absent for either config are dropped from
    # the pairing, never imputed.
    variant_mae_by_city = {v: {} for v in VARIANTS}
    for city in BASKET:
        r = per_city.get(city, {})
        if "error" in r:
            continue
        for v in VARIANTS:
            tup = r.get(v)
            if tup and tup[0] is not None:
                variant_mae_by_city[v][city] = tup[0]

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

    # Trackers distilled into the machine-readable status the live verdict reads.
    # Filled in the report blocks below so the banner can never disagree with the
    # printed report (same decision, captured once).
    status_reco: list[str] = []      # short CONSIDER/CANDIDATE tags for the banner
    status_regression = False
    status_cov = None                # 80% interval coverage (%) of the live variant
    status_cov_label = None
    status_convergence = None        # mechanism-convergence tally (recommend-only)
    status_c7 = None                 # realized-outcome edge state (read-only)

    lines = []
    lines.append(f"WEATHER COUNCIL — DAILY HEALTH CHECK  ({today.isoformat()})")
    lines.append("=" * 64)
    lines.append(f"basket: {len(BASKET)} cities | window {WINDOW}d | warmup {WARMUP} | "
                 f"current = bias {CURRENT_BIAS}, 1/MAE^{CURRENT_POWER}")
    lines.append("")

    lines.append("PER-CITY held-out MAE (current variant) + truth freshness")
    lines.append("  decomposition: accuracy(bias)=mean signed error (obs−pred; + ⇒ obs warmer, "
                 "model runs cold); precision(σ)=spread of those errors. RMSE²=bias²+σ².")
    for city in BASKET:
        r = per_city.get(city, {})
        if "error" in r:
            lines.append(f"  {city:12} ERROR: {r['error']}")
            continue
        tup = r.get(cur, (None, None, 0, None, None, None, None))
        mae, hit, n, bias, spread = tup[0], tup[1], tup[2], tup[5], tup[6]
        f = freshness.get(city, {})
        mae_s = f"{mae:.3f}" if mae is not None else "  -  "
        hit_s = f"{hit*100:.0f}%" if hit is not None else " - "
        lines.append(f"  {city:12} MAE {mae_s}  hit {hit_s:>4}  n={n:3} | "
                     f"truth={f.get('kind','?')} end={f.get('window_end','?')} "
                     f"season_gap={f.get('season_gap_days','?')}d")
        if bias is not None and spread is not None:
            rmse, _bfrac, diag = _accuracy_precision(bias, spread)
            lines.append(f"               └─ accuracy(bias) {bias:+.2f}°C · "
                         f"precision(σ) {spread:.2f}°C · RMSE {rmse:.2f}°C → {diag}")
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
        status_cov, status_cov_label = mcov, cal
        lines.append(f"  80% interval empirical coverage: {mcov:.1f}% — {cal}{flag}")
        if mcov < 70:
            status_reco.append("widen predictive spread (80% coverage below 70%)")
            lines.append("    RECOMMENDATION: intervals are too narrow on fresh data; a human "
                         "should consider widening the predictive spread (do NOT auto-apply).")
    lines.append("")

    # Recommendation on the variant. A challenger must clear BOTH a practical
    # floor (≥ MIN_IMPROVEMENT °C) AND a statistical bar (seeded paired bootstrap
    # over the per-city deltas, 90% CI above 0) — the bare basket-mean difference
    # alone is not a test and is biased downward by picking the best of four.
    lines.append("RECOMMENDATION (constants — human review required)")
    if best is None or cur_mae is None:
        lines.append("  insufficient data to evaluate variants today.")
    elif best == cur:
        lines.append(f"  HOLD. Current (bias {cur[0]}, 1/MAE^{cur[1]}) is still best on "
                     f"the basket (MAE {cur_mae:.4f}). No change recommended.")
    else:
        deltas = _paired_city_deltas(variant_mae_by_city[cur], variant_mae_by_city[best])
        point, lo, hi, npair = _paired_bootstrap_ci(deltas)
        if point is None:
            lines.append("  HOLD. No city is comparable on both current and the best "
                         "challenger today; cannot test. Keep current.")
        else:
            ci_s = (f"90% CI [{lo:+.4f}, {hi:+.4f}]" if lo is not None
                    else "CI n/a (single paired city)")
            significant = lo is not None and lo > 0
            if point >= MIN_IMPROVEMENT and significant:
                status_reco.append(f"variant→ bias {best[0]} 1/MAE^{best[1]} "
                                   f"({point:+.3f} °C, significant)")
                lines.append(f"  CONSIDER: bias {best[0]}, 1/MAE^{best[1]} beats current by "
                             f"{point:.4f} °C basket MAE over {npair} paired cities "
                             f"({ci_s}, excludes 0) — exceeds the {MIN_IMPROVEMENT} °C floor "
                             f"AND is significant. Worth a human re-evaluation; do NOT "
                             f"auto-apply.")
            elif point >= MIN_IMPROVEMENT:
                lines.append(f"  HOLD. Best challenger (bias {best[0]}, 1/MAE^{best[1]}) "
                             f"leads by {point:.4f} °C but {ci_s} includes 0 over {npair} "
                             f"paired cities — not distinguishable from noise. Keep current.")
            else:
                lines.append(f"  HOLD. Best challenger (bias {best[0]}, 1/MAE^{best[1]}) "
                             f"leads by only {point:.4f} °C (< {MIN_IMPROVEMENT} floor) — "
                             f"noise, not signal. Keep current.")
    lines.append("")

    # OUTLIER_FLOOR_C sweep — re-justify the member-rejection floor on fresh data.
    floor_mae = {f: (statistics.mean(v.values()) if v else None) for f, v in basket_floor.items()}
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
        fdeltas = _paired_city_deltas(basket_floor[CURRENT_FLOOR], basket_floor[best_floor])
        fpoint, flo, fhi, fnp = _paired_bootstrap_ci(fdeltas)
        if fpoint is None:
            lines.append(f"  HOLD. No city is comparable on both the current floor and "
                         f"floor {best_floor:.1f} °C today; cannot test. Keep "
                         f"OUTLIER_FLOOR_C at {CURRENT_FLOOR:.1f} °C.")
        else:
            fci_s = (f"90% CI [{flo:+.4f}, {fhi:+.4f}]" if flo is not None
                     else "CI n/a (single paired city)")
            fsig = flo is not None and flo > 0
            if fpoint >= MIN_IMPROVEMENT and fsig:
                status_reco.append(f"outlier_floor→ {best_floor:.1f} °C "
                                   f"({fpoint:+.3f} °C, significant)")
                lines.append(f"  CONSIDER: floor {best_floor:.1f} °C beats current by "
                             f"{fpoint:.4f} °C basket MAE over {fnp} paired cities "
                             f"({fci_s}, excludes 0) — exceeds the {MIN_IMPROVEMENT} °C floor "
                             f"AND is significant. Worth a human re-evaluation of "
                             f"OUTLIER_FLOOR_C; do NOT auto-apply.")
            elif fpoint >= MIN_IMPROVEMENT:
                lines.append(f"  HOLD. Floor {best_floor:.1f} °C leads by {fpoint:.4f} °C but "
                             f"{fci_s} includes 0 over {fnp} paired cities — not "
                             f"distinguishable from noise. Keep OUTLIER_FLOOR_C at "
                             f"{CURRENT_FLOOR:.1f} °C.")
            else:
                lines.append(f"  HOLD. Best floor {best_floor:.1f} °C leads by only "
                             f"{fpoint:.4f} °C (< {MIN_IMPROVEMENT} floor) — noise, not "
                             f"signal. Keep OUTLIER_FLOOR_C at {CURRENT_FLOOR:.1f} °C.")
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

    # Spread–skill reliability of the SAME (|error|, dispersion) axis: beyond the
    # 3-tier ordering test above, does dispersion track error with the right SHAPE
    # across regimes (a binned reliability diagram + significance-gated rank
    # correlation), or only on average? Recommend-only; never moves a cutoff.
    ss_eval = spread_skill_eval(disp_pairs_all)
    if ss_eval is not None:
        lines.append(f"  spread–skill: {ss_eval.label} "
                     f"(disp↔|err| r={ss_eval.consistency:+.2f}, relative-reliability "
                     f"gap {ss_eval.reliability_gap*100:.0f}%, averaging 1/α≈"
                     f"{ss_eval.avg_members_factor:.1f}×, n={ss_eval.n}).")
        if not ss_eval.tracks_error:
            lines.append("  ⚠ dispersion does not track held-out error past the noise "
                         "floor — the per-day confidence signal is weak on today's "
                         "data. RECOMMENDATION: a human should review whether the "
                         "dispersion-based tiers earn their place (do NOT auto-apply).")
            status_reco.append("spread–skill FLAT (dispersion ⊥ held-out error)")
    else:
        lines.append("  spread–skill: insufficient held-out days to bin a reliability "
                     "diagram today.")
    lines.append("")

    # Ensemble-calibration companions to spread–skill (recommend-only). Two
    # standard verification diagrams, pooled basket-wide at the LIVE variant:
    #   1. rank histogram (Talagrand): is the raw member panel's dispersion the
    #      right SIZE? A U-shape = under-dispersed (too tight), a dome = too wide,
    #      a tilt = a panel bias.
    #   2. PIT histogram: is the SERVED distribution — the held-out residual cloud
    #      compare.py resamples into bucket probabilities — calibrated? A U-shape
    #      = over-confident buckets, a dome = under-confident, a tilt = biased.
    # Together with spread–skill they verify the bucket-probability spread end to
    # end. Like every block here they emit a finding for human review; they never
    # move a verdict number or a cutoff.
    rh_eval = rank_histogram_eval(rank_inputs_all)
    pc_eval = pit_calibration_eval(pit_values_all)
    lines.append("ENSEMBLE CALIBRATION CHECK (is the spread the right size, and the "
                 "served cloud honest?)")
    if rh_eval is not None:
        rhd = rh_eval.diag
        lines.append(f"  rank histogram (raw panel dispersion): {rh_eval.verdict} — "
                     f"{rhd.shape}, edge ratio {rhd.edge_ratio:.2f}, reduced χ²="
                     f"{rhd.reduced_chi2} (z={rhd.z:+.1f}), n={rhd.n}.")
        if rh_eval.verdict != "CALIBRATED":
            lines.append(f"    → {rh_eval.meaning}.")
            status_reco.append(f"rank histogram {rh_eval.verdict} ({rhd.shape})")
    else:
        lines.append("  rank histogram: insufficient held-out days to fill the bins today.")
    if pc_eval is not None:
        pcd = pc_eval.diag
        lines.append(f"  PIT of served cloud (the bucket-probability distribution): "
                     f"{pc_eval.verdict} — {pcd.shape}, edge ratio {pcd.edge_ratio:.2f}, "
                     f"reduced χ²={pcd.reduced_chi2} (z={pcd.z:+.1f}), n={pcd.n}.")
        if pc_eval.verdict != "CALIBRATED":
            lines.append(f"    → {pc_eval.meaning}.")
            status_reco.append(f"PIT {pc_eval.verdict} ({pcd.shape})")
    else:
        lines.append("  PIT histogram: insufficient leak-free PIT values to bin today.")
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
            status_regression = drift > REGRESSION_TOL
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
        # Persist the tally for the live UI feed (recommend-only; MEASURED today).
        status_convergence = {
            "affirmed": tally["AFFIRMED"],
            "affirmed_nudge": tally["AFFIRMED_NUDGE"],
            "contested": tally["CONTESTED"],
            "abstained": tally["ABSTAIN"],
            "evaluated": sum(tally.values()),
        }
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

    # Model vs market (recommend-only): where does the council's bucket
    # distribution disagree with the LIVE Polymarket implied probabilities today?
    # This is NOT an edge claim — disagreement only becomes a validated edge once
    # C7 (below) grades enough SETTLED days and the council beats the market on
    # both proper scores with a CI excluding zero. Each comparison here is also
    # LOGGED so that settled set can grow. Withheld cities (e.g. HK's sub-degree
    # Observatory settlement) are named with the reason rather than fabricated.
    lines.append("MODEL vs MARKET (live Polymarket implied probabilities — recommend-only, NOT an edge)")
    lines.append(f"  logged {snapshots_logged} snapshot(s) to the C7 ledger this run "
                 f"(they settle & are graded below once their day passes).")
    if market_div:
        disagreements = []
        for city, cmp in sorted(market_div.items()):
            mm, km = cmp.model_modal, cmp.market_modal
            gap = cmp.largest_gap
            gap_s = f"{gap*100:.0f}pp" if gap is not None else " - "
            flag = "  ⚠ modal disagreement" if (mm and km and mm != km) else ""
            bc = cmp.bias_correction_c
            bc_s = f", bias-corr {bc:+.2f}°C" if bc is not None else ""
            lines.append(f"  {city:12} model modal {str(mm):>5} vs market modal "
                         f"{str(km):>5}  largest-gap {gap_s:>5}{bc_s}{flag}")
            if flag:
                disagreements.append(city)
        if disagreements:
            lines.append("  NOTE (human review): the council and the market disagree on the "
                         "MOST-LIKELY bucket for " + ", ".join(disagreements) + ". This is a "
                         "candidate signal, not a verified edge — see C7. Do NOT trade on it.")
        else:
            lines.append("  council and market agree on the modal bucket for every matched city "
                         "today; divergences are within a bucket.")
    else:
        lines.append("  no city produced a supported model-vs-market comparison today.")
    if market_withheld:
        lines.append("  withheld (comparison would require fabricating an unverified bucket mapping):")
        for city, reason in sorted(market_withheld.items()):
            short = reason.split(". ")[0]
            lines.append(f"    - {city}: {short}.")
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
        # Persist the realized-edge state for the live UI feed (read-only). This is
        # the SAME report whose is_edge_validated drives c7_validated above, so the
        # bar's C7 chip can never disagree with the loop gate.
        status_c7 = {
            "settled_days": report.n,
            "validated": bool(report.is_edge_validated),
            "settled_this_run": len(settled),
            "snapshots_logged": snapshots_logged,
            "note": report.note,
        }
    except Exception as exc:                       # never let C7 abort the health check
        lines.append(f"  C7 unavailable this run ({exc}); calibration unchanged.")
    lines.append("")

    # Tracked forecaster (recommend-only): settle any Weatherbit forecasts whose
    # day has passed against the SAME anchored truth the council uses, then report
    # the head-to-head MAE. Weatherbit has no backtestable forecast archive, so it
    # can ONLY earn a record forward — and it is NEVER voted into the live blend,
    # whatever it scores. Promotion (to a properly backtested member) is a human
    # decision, surfaced here only as a candidate once it has earned enough days.
    try:
        settle_tracked_forecasts()
        wb = tracked_forecast_scores("weatherbit")
    except Exception as exc:
        wb = {"n": 0, "source_mae": None, "council_mae": None}
        wb_err = str(exc)
    else:
        wb_err = None
    lines.append("TRACKED FORECASTER — WEATHERBIT (recommend-only, prospective; NEVER voted)")
    if wb_err:
        lines.append(f"  tracker unavailable this run ({wb_err}); ledger unchanged.")
    else:
        lines.append(f"  logged {tracked_logged} forecast(s) this run; "
                     f"{wb['n']} day(s) settled head-to-head against anchored truth.")
        if wb["n"] == 0:
            lines.append("  no settled days yet — Weatherbit exposes no backtestable forecast "
                         "archive, so it earns its record forward. Accumulating "
                         "(set WEATHERBIT_API_KEY to start logging).")
        elif wb["source_mae"] is None or wb["council_mae"] is None:
            lines.append("  settled days present but a side lacks a forecast; cannot compare yet.")
        else:
            wb_mae, co_mae = wb["source_mae"], wb["council_mae"]
            delta = co_mae - wb_mae          # > 0 ⇒ Weatherbit beats the council
            lines.append(f"  Weatherbit MAE {wb_mae:.3f} °C  vs council MAE {co_mae:.3f} °C "
                         f"(over {wb['n']} settled day(s)).")
            if wb["n"] < MIN_SAMPLES:
                lines.append(f"  TOO FEW settled days (< {MIN_SAMPLES}) to judge — keep "
                             "accumulating. Weatherbit remains tracked-only, NOT a council vote.")
            elif delta >= MIN_IMPROVEMENT:
                status_reco.append(f"weatherbit promotion candidate "
                                   f"({delta:+.3f} °C over {wb['n']} settled days)")
                lines.append(f"  CANDIDATE (human review): Weatherbit beats the council by "
                             f"{delta:.3f} °C over {wb['n']} settled days (exceeds the "
                             f"{MIN_IMPROVEMENT} °C floor). Worth evaluating it as a properly "
                             "backtested member — a human decision; still NOT voted, do NOT "
                             "auto-apply.")
            else:
                lines.append(f"  No promotion case: Weatherbit does not beat the council beyond "
                             f"the {MIN_IMPROVEMENT} °C floor (delta {delta:+.3f} °C). "
                             "Remains tracked-only.")
    lines.append("")

    # SOFT-FAILURE SURFACING (Phase 6b) — the deliberately-swallowed except-blocks
    # in the settlement path (storage.settle_market_snapshots WU/station fetch,
    # intraday_ceiling register consult) now RECORD instead of vanishing. A
    # settlement-source swallow is invisible data corruption — the day just does
    # not settle and absence reads as success — so any settlement-tagged failure in
    # the last 24h is an ALARM here. Read-only: this only reports the ledger the
    # swallow already wrote; it never changes control flow or a served number.
    lines.append("SOFT FAILURES (swallowed exceptions, last 24h — settlement-tagged = ALARM)")
    try:
        counts = soft_failure_counts(24)
    except Exception as exc:
        counts = {}
        lines.append(f"  soft-failure ledger unavailable this run ({exc}).")
    # A tag is settlement-critical when a swallow there can silently drop a
    # settlement/lock: the WU/station settle fetches and the register consult.
    _SETTLE_TAGS = ("settle_", "register")
    if not counts:
        lines.append("  none recorded in the last 24h.")
    else:
        alarms = {t: n for t, n in counts.items()
                  if any(t.startswith(p) or p in t for p in _SETTLE_TAGS)}
        for tag, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            flag = "  ⚠ ALARM (settlement path)" if tag in alarms else ""
            lines.append(f"  {tag:26} {n:4}{flag}")
        if alarms:
            # Show the most recent settlement-tagged detail so the operator can act
            # without opening the DB — which feed failed, and how.
            recent = [r for r in recent_soft_failures(24)
                      if r["tag"] in alarms]
            if recent:
                r0 = recent[0]
                lines.append(f"    → latest: {r0['at']}  {r0['tag']}  "
                             f"{r0['etype']}: {(r0['detail'] or '')[:80]}")
            lines.append("    RECOMMENDATION: a settlement source is silently failing — "
                         "days may be going unsettled. A human should investigate the feed "
                         "before trusting today's coverage.")
            status_reco.append("soft-failure ALARM: " + ", ".join(sorted(alarms)))
    lines.append("")

    # ORDER-BOOK CAPTURE (Phase 4) — is the executable-depth archive actually
    # growing? Read-only view of book_snapshots over the last 24h. A capture that
    # ran but returned mostly fetch_ok=0 rows means the CLOB books were unavailable
    # (the depth-walk P&L would be blind), so a high failure share is flagged.
    lines.append("ORDER-BOOK CAPTURE (executable depth archive, last 24h — read-only)")
    try:
        cov = book_snapshot_coverage(24)
    except Exception as exc:
        cov = None
        lines.append(f"  book-coverage unavailable this run ({exc}).")
    if cov is not None:
        if cov["rows"] == 0:
            lines.append("  no order-book rows captured in the last 24h "
                         "(focus cities: Karachi, Jeddah, Singapore, London, San Francisco).")
        else:
            fail_share = cov["failed"] / cov["rows"] if cov["rows"] else 0.0
            flag = "  ⚠ mostly-empty books" if fail_share >= 0.5 else ""
            lines.append(f"  {cov['rows']} token row(s) across {cov['batches']} capture(s) / "
                         f"{cov['places']} city(ies): {cov['ok']} OK, {cov['failed']} "
                         f"failed ({fail_share*100:.0f}% empty){flag}")
            for place, pc in sorted(cov["by_place"].items()):
                lines.append(f"    {place:24} {pc['ok']:3} OK / {pc['failed']:3} failed")
            if fail_share >= 0.5:
                lines.append("    NOTE (human review): most captured books were empty/"
                             "unavailable — executable P&L would be blind on these; check the "
                             "CLOB feed before trusting depth-walk numbers.")
    lines.append("")

    # ERROR ATTRIBUTION (Plan 3 Phase 3) — how settled errors decompose. SETTLEMENT attributions
    # are ALARM-tier: they mean the model was right and the anchor/contract diverged — the one
    # error class no forecasting improvement can fix. Read-only.
    lines.append("ERROR ATTRIBUTION (settled post-mortems — decomposed cause histogram)")
    try:
        from weather_council.postmortem import attribution_histogram
        hist = attribution_histogram()
    except Exception as exc:
        hist = None
        lines.append(f"  attribution unavailable this run ({exc}).")
    if hist is not None:
        if not hist:
            lines.append("  no attributed post-mortems yet (settled+provenance rows still "
                         "accruing — pre-provenance verdicts are UNATTRIBUTABLE by design).")
        else:
            for cause, n in sorted(hist.items(), key=lambda kv: (-kv[1], kv[0])):
                flag = "  ⚠ ALARM (anchor≠contract; no forecast fix)" if cause == "SETTLEMENT" else ""
                lines.append(f"  {cause:28} {n:4}{flag}")
            if hist.get("SETTLEMENT"):
                lines.append("    RECOMMENDATION: a settlement/anchor divergence occurred — the "
                             "model was right but the record it paid on differed. A human should "
                             "check the anchor↔contract alignment before trusting coverage.")
                status_reco.append("SETTLEMENT attribution (anchor≠contract)")
    lines.append("")

    lines.append(f"requests made this run: {total_requests} "
                 f"(across {len(BASKET)} per-city clients)")

    report = "\n".join(lines)
    print(report)

    # Persist. A degraded run (no usable city) must not clobber the last good
    # 'latest' pointer or move the baseline — see _persist. Baseline is written
    # only on the first good run; never silently moved, so drift stays measurable.
    usable_cities = len(basket_acc[cur])
    persisted = _persist(report, today, usable_cities, cur_mae, cur, baseline is None)
    if persisted:
        # Distil the run into the machine-readable status the live verdict reads.
        # Only on a good run, so a transient outage never overwrites last-known-good.
        baseline_mae = baseline.get("basket_mae_current") if baseline else None
        status = {
            "date": today.isoformat(),
            "variant": list(cur),
            "basket_mae": round(cur_mae, 4) if cur_mae is not None else None,
            "baseline_mae": baseline_mae,
            "baseline_date": (baseline.get("date") if baseline else None),
            "regression": status_regression,
            "calibration_coverage_pct": (round(status_cov, 1)
                                         if status_cov is not None else None),
            "calibration_label": status_cov_label,
            # Spread–skill reliability of the dispersion signal on today's basket
            # (recommend-only; MEASURED, never a target). None when too few days.
            "spread_skill": ({
                "label": ss_eval.label,
                "tracks_error": ss_eval.tracks_error,
                "reliable": ss_eval.reliable,
                "consistency": ss_eval.consistency,
                "reliability_gap": ss_eval.reliability_gap,
                "averaging_factor": ss_eval.avg_members_factor,
                "n": ss_eval.n,
            } if ss_eval is not None else None),
            # Ensemble-calibration companions (recommend-only; MEASURED, never a
            # target). "applied": False makes explicit they change no served value.
            "rank_histogram": ({
                "verdict": rh_eval.verdict,
                "shape": rh_eval.diag.shape,
                "edge_ratio": rh_eval.diag.edge_ratio,
                "reduced_chi2": rh_eval.diag.reduced_chi2,
                "z": rh_eval.diag.z,
                "uniform": rh_eval.diag.uniform,
                "bins": list(rh_eval.diag.bins),
                "n": rh_eval.n,
                "applied": False,
            } if rh_eval is not None else None),
            "pit_calibration": ({
                "verdict": pc_eval.verdict,
                "shape": pc_eval.diag.shape,
                "edge_ratio": pc_eval.diag.edge_ratio,
                "reduced_chi2": pc_eval.diag.reduced_chi2,
                "z": pc_eval.diag.z,
                "uniform": pc_eval.diag.uniform,
                "bins": list(pc_eval.diag.bins),
                "n": pc_eval.n,
                "applied": False,
            } if pc_eval is not None else None),
            "recommendations": status_reco,
            # Realized-outcome edge state (read-only). This is the SAME flag the
            # loop's deploy gate consumes: the council can only ever clear to LIVE
            # once C7 validates a settled council-vs-market edge AND a human signs
            # off. Persisted so the live UI can render the real gate, not a label.
            "c7_validated": bool(c7_validated),
            # Mechanism convergence — independent corroboration of each headline
            # (recommend-only; MEASURED today). None when no city had enough support.
            "convergence": status_convergence,
            # Realized-outcome edge state (read-only). Mirrors the C7 report that
            # drives c7_validated, so the UI can never show a divergent edge state.
            "c7": status_c7,
            "cities_usable": usable_cities,
            "cities_total": len(BASKET),
            "data_freshness_max_gap_days": (max(gaps) if gaps else None),
            "requests": total_requests,
            # Operational metrics — every value here is MEASURED this run (never a
            # target or a fabricated number). Surfaced for monitoring; like the
            # rest of the status, the live verdict only ever DISPLAYS these.
            "metrics": {
                "run_seconds": round(time.monotonic() - run_t0, 1),
                "requests": total_requests,
                "cities_usable": usable_cities,
                "cities_total": len(BASKET),
                "city_error_rate": (round((len(BASKET) - usable_cities) / len(BASKET), 3)
                                    if BASKET else None),
                "backtest_mae": round(cur_mae, 4) if cur_mae is not None else None,
                "coverage_pct_80": (round(status_cov, 1)
                                    if status_cov is not None else None),
                "data_freshness_max_gap_days": (max(gaps) if gaps else None),
            },
        }
        _write_status(status)
    else:
        print(f"[degraded run: {usable_cities}/{len(BASKET)} cities usable — "
              f"latest.txt and baseline preserved from last good run]")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
