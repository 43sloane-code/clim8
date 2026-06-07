"""Bridge: set the council's verdict beside a market's bucket ladder.

C5 — turn a point verdict into per-bucket probabilities using ONLY the method's
earned, held-out backtest errors. No assumed distribution, no invented σ. For
each signed historical error e_i = observed − predicted, the predicted actual is
(verdict + e_i); quantizing that to the market's native grain and dropping it
into the C4 bucket ladder gives an empirical distribution over the buckets. Skew
and fat tails in the real errors (e.g. the documented hot-day clipping) survive
because the errors are resampled, not summarized into a mean and a width.

Scope / honesty boundary:
  * These probabilities are on the **backtest-truth scale** (the station/grid the
    method is actually scored against). The market settles on a *different*
    sensor; the measured source bias (settlement #4) is surfaced alongside as a
    known offset, NOT silently folded in.
  * Both the raw "Yes" price and the de-vigged market probability (C6,
    proportional normalisation over the complete bucket partition) are reported,
    so model vs market is a like-for-like probability comparison.
  * Nothing here is calibrated against realized market outcomes yet (C7). No
    "edge" is claimed — this only places two probability columns side by side.
"""

from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass

from .market import WeatherMarket
from .scoring import interval_coverage, quantile
from .station_offset import StationOffset

# Below this many held-out errors we will not assert an empirical distribution —
# the same "don't claim what you haven't measured" floor the council uses
# elsewhere. A handful of points cannot shape a credible tail.
MIN_RESIDUALS = 10


@dataclass(frozen=True)
class Calibration:
    """Honest accounting of how trustworthy the model's bucket probabilities are.

    The probabilities come from resampling N held-out backtest errors, so their
    quality is bounded by that distribution. We report its shape and a weak
    out-of-sample coverage self-check — and crucially do NOT claim the
    comparison is a calibrated edge. A real reliability curve needs realized
    market outcomes accumulated over time (the storage/--verify path), which
    free-archive data cannot backfill; until that exists, `is_edge_validated`
    stays False by design."""
    n: int
    bias: float                  # mean held-out error left after correction
    spread: float                # std of held-out errors
    skew: float                  # signed asymmetry (warm tail = positive)
    p10: float
    p50: float
    p90: float
    coverage_80: float | None    # out-of-sample fraction inside the [p10,p90] band
    coverage_n: int              # held-out points the coverage check used
    is_edge_validated: bool      # always False here — no realized-outcome curve yet
    note: str


def residual_calibration(residuals_c: list[float]) -> Calibration | None:
    if len(residuals_c) < MIN_RESIDUALS:
        return None
    xs = list(residuals_c)
    n = len(xs)
    mean = statistics.mean(xs)
    spread = statistics.pstdev(xs)
    skew = 0.0
    if spread > 1e-9:
        skew = sum(((x - mean) / spread) ** 3 for x in xs) / n
    p10, p50, p90 = quantile(xs, 0.10), quantile(xs, 0.50), quantile(xs, 0.90)

    # Leak-free out-of-sample dispersion check, sharing ONE engine with the
    # council's walk-forward calibration (scoring.interval_coverage): for every
    # error past the same warmup floor the council uses, ask whether it falls
    # inside the 80% band of the STRICTLY-EARLIER errors. This scores every later
    # point — not one arbitrary chronological split-half — so this number and
    # Validation.coverage_80 are the same measurement on the same residuals,
    # never two conventions that could disagree.
    coverage = None
    cov_n = 0
    if n > MIN_RESIDUALS:
        hits = 0
        for i in range(MIN_RESIDUALS, n):
            covered, _ = interval_coverage(xs[:i], xs[i])
            hits += 1 if covered else 0
            cov_n += 1
        coverage = hits / cov_n if cov_n else None

    note = (
        f"probabilities resampled from {n} held-out errors "
        f"(bias {mean:+.2f}°C, spread {spread:.2f}°C"
        + (f", warm-skewed {skew:+.2f}" if abs(skew) >= 0.3 else "")
        + "). NOT validated against realized market outcomes — treat as a "
        "comparison, not an edge."
    )
    return Calibration(
        n=n, bias=round(mean, 2), spread=round(spread, 2), skew=round(skew, 2),
        p10=round(p10, 2), p50=round(p50, 2), p90=round(p90, 2),
        coverage_80=(round(coverage, 2) if coverage is not None else None),
        coverage_n=cov_n, is_edge_validated=False, note=note,
    )


@dataclass(frozen=True)
class BucketComparison:
    label: str
    lo: int | None
    hi: int | None
    model_prob: float            # empirical P(high settles in this bucket)
    market_yes: float | None     # raw "Yes" price (vig still in it)
    market_prob: float | None    # de-vigged market probability (C6)


@dataclass(frozen=True)
class VerdictMarketComparison:
    market_title: str
    grain: str
    n_residuals: int
    verdict_high_c: float
    verdict_reading: int             # verdict snapped to native whole-degree
    verdict_bucket: str | None       # bucket the point verdict alone lands in
    edge_distance_c: float | None    # verdict's distance to nearest bucket edge
    bias_correction_c: float | None  # signed backtested correction baked into the verdict
    settles_sub_degree: bool         # market settles finer than its whole-degree labels
    settlement_offset_c: float | None  # measured settlement-vs-backtest station offset (°C)
    settlement_high_c: float | None    # verdict transferred onto the settlement station scale
    settlement_offset_note: str | None # provenance of the station-offset transfer
    settlement_offset_modern: bool | None  # offset measured on recent (not decades-stale) overlap
    buckets: tuple[BucketComparison, ...]
    model_modal: str | None          # most-likely bucket by the model
    market_modal: str | None         # most-likely bucket by the market
    market_overround: float | None   # Σ(yes) − 1; the vig still present
    unmatched_fraction: float        # resampled draws that hit no bucket
    settlement_bias_note: str | None # truth-vs-settlement caveat, if measurable
    calibration: Calibration | None  # how trustworthy the model probs are (C7)
    largest_gap: float | None        # max |model_prob − market_prob| over buckets


def compare_high(
    market: WeatherMarket,
    verdict_high_c: float,
    residuals_c: list[float],
    source_check: dict | None = None,
    bias_correction_c: float | None = None,
    station_offset: StationOffset | None = None,
) -> VerdictMarketComparison | None:
    """Place the council's high verdict beside one city/day market.

    residuals_c: signed held-out errors (observed − predicted) in °C, from the
    council's own backtest (Validation.residuals_high).
    bias_correction_c: the signed, backtested bias correction already baked into
    verdict_high_c (see council.applied_bias_correction). Surfaced so a divergence
    from the market can be reported as earned signal rather than hedged.
    station_offset: a measured settlement-vs-backtest station offset (see
    station_offset.measure_settlement_offset). REQUIRED to compare a market that
    settles finer than its whole-degree labels (e.g. HK on the Observatory): it
    transfers the verdict onto the settlement station's scale so the bucket
    mapping is earned, not fabricated. Without it such a market is declined."""
    if not market.buckets or len(residuals_c) < MIN_RESIDUALS:
        return None
    sub_degree = market.settles_sub_degree()
    if sub_degree and station_offset is None:
        # Settlement grain is finer than the whole-degree labels (HK Observatory,
        # 0.1°C) AND settles on a different station than the backtest. Without a
        # measured station offset we cannot map the model onto the settlement
        # scale without fabricating it, so we decline. Callers can surface
        # grain_support_note() to explain; supply station_offset to compare.
        return None

    # Transfer the verdict onto the settlement station's scale when an offset was
    # measured (the systematic settlement−backtest mean). 0 for ordinary markets.
    offset_c = station_offset.high_mean if station_offset is not None else 0.0
    settle_c = verdict_high_c + offset_c

    n = len(residuals_c)
    counts: dict[str, int] = {b.label: 0 for b in market.buckets}
    unmatched = 0
    for e in residuals_c:
        b = market.bucket_for_high(settle_c + e)
        if b is None:
            unmatched += 1
        else:
            counts[b.label] += 1

    devigged = market.implied_probabilities() or {}
    rows = tuple(
        BucketComparison(
            label=b.label, lo=b.lo, hi=b.hi,
            model_prob=counts[b.label] / n,
            market_yes=b.yes_price,
            market_prob=devigged.get(b.label),
        )
        for b in market.buckets
    )

    model_modal = max(rows, key=lambda r: r.model_prob).label if rows else None
    priced = [r for r in rows if r.market_yes is not None]
    market_modal = max(priced, key=lambda r: r.market_yes).label if priced else None
    overround = market.overround()
    largest_gap = max(
        (abs(r.model_prob - r.market_prob) for r in rows if r.market_prob is not None),
        default=None,
    )
    calibration = residual_calibration(residuals_c)

    note = None
    if source_check and source_check.get("high_mean") is not None:
        bias = source_check["high_mean"]
        tail = source_check.get("tail_days_ge3") or 0
        note = (
            f"model probs are on the backtest-truth scale; the settlement sensor "
            f"reads on average {bias:+.2f}°C vs that truth"
            + (f" (with {tail} day(s) ≥3°C apart — hot-day clipping)" if tail else "")
            + ". Shift not applied here."
        )

    return VerdictMarketComparison(
        market_title=market.title,
        grain=market.grain,
        n_residuals=n,
        verdict_high_c=verdict_high_c,
        verdict_reading=market.native_reading(settle_c),
        verdict_bucket=(market.bucket_for_high(settle_c).label
                        if market.bucket_for_high(settle_c) else None),
        edge_distance_c=market.edge_distance_c(settle_c),
        bias_correction_c=(round(bias_correction_c, 3)
                           if bias_correction_c is not None else None),
        settles_sub_degree=sub_degree,
        settlement_offset_c=(round(offset_c, 3) if station_offset is not None else None),
        settlement_high_c=(round(settle_c, 2) if station_offset is not None else None),
        settlement_offset_note=(station_offset.note() if station_offset is not None else None),
        settlement_offset_modern=(station_offset.is_modern if station_offset is not None else None),
        buckets=rows,
        model_modal=model_modal,
        market_modal=market_modal,
        market_overround=overround,
        unmatched_fraction=unmatched / n,
        settlement_bias_note=note,
        calibration=calibration,
        largest_gap=(round(largest_gap, 3) if largest_gap is not None else None),
    )


_MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"],
        start=1,
    )
}


def _label_month_day(date_label: str) -> tuple[int, int] | None:
    """Pull (month, day) out of a market's free-text date label ("June 4").
    Year is omitted in the label, so we match only month+day."""
    if not date_label:
        return None
    month = day = None
    for tok in date_label.replace(",", " ").split():
        tl = tok.lower()
        if tl in _MONTHS:
            month = _MONTHS[tl]
        elif tok.isdigit():
            day = int(tok)
    return (month, day) if month and day else None


def match_market(
    markets: list[WeatherMarket], city: str, target_date: dt.date
) -> WeatherMarket | None:
    """Find the one market for this city on this day, or None.

    City is matched by case-insensitive containment either direction (the
    market's "London" vs the geocoder's "London"); the day is matched on
    month+day from the market's date label so a stale or differently-phrased
    year can't cause a false miss. Returns the first exact city+day hit."""
    cl = city.strip().lower()
    md = (target_date.month, target_date.day)
    for m in markets:
        if not m.city:
            continue
        mc = m.city.strip().lower()
        if cl not in mc and mc not in cl:
            continue
        lab = _label_month_day(m.date_label or "")
        if lab is not None and lab != md:
            continue
        return m
    return None


def grain_support_note(
    market: WeatherMarket, verdict_high_c: float | None = None
) -> str | None:
    """None if a model-vs-market comparison is sound for this market (its
    settlement grain matches its whole-degree bucket labels). Otherwise an
    explanation of why the comparison is withheld — so the CLI/UI can say *why*
    instead of silently rounding or showing nothing."""
    if not market.settles_sub_degree():
        return None
    note = (
        f"matched \"{market.title}\", but it settles to {market.precision} on "
        f"{market.station or 'a sub-degree source'} — finer than its whole-degree "
        f"bucket labels, and on a different station than the council's backtest. "
        f"Snapping the model to a whole degree here would fabricate a bucket "
        f"mapping we haven't verified, so the model-vs-market comparison is "
        f"withheld (roadmap A3/A5)."
    )
    if verdict_high_c is not None:
        note += (
            f" At this resolution the high verdict {verdict_high_c:.1f}°C settles "
            f"as {verdict_high_c:.1f}°C — not a rounded whole degree."
        )
    return note


def comparison_to_dict(c: VerdictMarketComparison) -> dict:
    cal = c.calibration
    return {
        "market_title": c.market_title,
        "grain": c.grain,
        "n_residuals": c.n_residuals,
        "verdict_high_c": c.verdict_high_c,
        "verdict_reading": c.verdict_reading,
        "verdict_bucket": c.verdict_bucket,
        "edge_distance_c": c.edge_distance_c,
        "bias_correction_c": c.bias_correction_c,
        "settles_sub_degree": c.settles_sub_degree,
        "settlement_offset_c": c.settlement_offset_c,
        "settlement_high_c": c.settlement_high_c,
        "settlement_offset_note": c.settlement_offset_note,
        "settlement_offset_modern": c.settlement_offset_modern,
        "model_modal": c.model_modal,
        "market_modal": c.market_modal,
        "market_overround": c.market_overround,
        "unmatched_fraction": c.unmatched_fraction,
        "largest_gap": c.largest_gap,
        "settlement_bias_note": c.settlement_bias_note,
        "is_edge_validated": bool(cal and cal.is_edge_validated),
        "calibration": (
            {
                "n": cal.n, "bias": cal.bias, "spread": cal.spread, "skew": cal.skew,
                "p10": cal.p10, "p50": cal.p50, "p90": cal.p90,
                "coverage_80": cal.coverage_80, "coverage_n": cal.coverage_n,
                "note": cal.note,
            }
            if cal else None
        ),
        "buckets": [
            {
                "label": b.label, "lo": b.lo, "hi": b.hi,
                "model_prob": b.model_prob,
                "market_yes": b.market_yes,
                "market_prob": b.market_prob,
            }
            for b in c.buckets
        ],
    }
