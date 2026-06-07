"""C7 — realized-outcome calibration: is the council actually better than the
market, on settled days?

`compare.py` places the council's per-bucket probability beside the market's
de-vigged probability, but deliberately claims no edge (`is_edge_validated`
stays False) because nothing there is scored against what actually happened.
This module closes that loop. Given snapshots that have *settled* — each one a
council probability vector, a market probability vector, and the realized bucket
the day's high landed in **at the anchor settlement station** (the record the
market pays out on, not a face-value grid reading) — it grades both forecasters
with strictly-proper scores and only certifies an edge when the council beats
the market out-of-sample by a margin a seeded paired bootstrap says is real.

Design boundaries (deliberate):
  * Read-only / recommend-only. Nothing here sizes a position, prices an order,
    or moves funds. It scores forecasts and prints a verdict for human review.
  * Leak-free by construction: every snapshot is scored against its OWN future
    realized outcome. No parameter is fitted, so there is nothing to overfit.
  * No backfill. Market prices were never archived, so the settled set can only
    grow forward in time — `n` is small until days accumulate, and the edge
    verdict honestly reads "unvalidated" until then.

Scoring (both lower-is-better):
  * Multiclass Brier  BS = Σ_b (p_b − 1{b=r})²  over the complete bucket ladder.
  * Log loss          LL = −ln p_r  (the realized bucket's probability, clipped).
A council "edge" requires it to win on BOTH, with a bootstrap CI on the log-loss
difference that excludes zero, over at least MIN_SETTLED settled days.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

# Settled days required before an edge can be certified at all. Small enough to
# be reachable on a daily cadence, large enough that a bootstrap CI is meaningful.
MIN_SETTLED = 20
# Probability floor so a zero-probability realized bucket gives a large but
# finite log-loss penalty rather than +inf (a deserved, bounded penalty).
EPS = 1e-6
BOOTSTRAP_SAMPLES = 5000
BOOTSTRAP_SEED = 20260607        # fixed so the edge verdict is reproducible
RELIABILITY_BINS = 5


@dataclass(frozen=True)
class SnapshotScore:
    """One settled day scored for both forecasters."""
    place: str
    target_date: str
    realized_label: str
    council_brier: float
    market_brier: float
    council_logloss: float
    market_logloss: float
    council_p_realized: float    # probability each put on the bucket that occurred
    market_p_realized: float


@dataclass(frozen=True)
class ReliabilityBin:
    lo: float
    hi: float
    mean_pred: float | None      # mean predicted probability in this bin
    emp_freq: float | None       # empirical frequency the predicted bucket occurred
    n: int


@dataclass(frozen=True)
class EdgeReport:
    n: int
    council_brier: float | None
    market_brier: float | None
    council_logloss: float | None
    market_logloss: float | None
    logloss_diff: float | None       # market − council; >0 means council is better
    logloss_diff_ci: tuple[float, float] | None   # paired bootstrap 95% CI
    brier_diff: float | None         # market − council; >0 means council is better
    council_reliability: tuple[ReliabilityBin, ...]
    market_reliability: tuple[ReliabilityBin, ...]
    is_edge_validated: bool
    note: str


def _brier(probs: dict[str, float], buckets: list[str], realized: str) -> float:
    """Multiclass Brier over the full bucket ladder. A missing/None bucket
    probability counts as 0 — a forecaster that left a bucket unpriced is scored
    as having assigned it no mass, which is exactly what it did."""
    total = 0.0
    for b in buckets:
        p = probs.get(b)
        p = p if isinstance(p, (int, float)) else 0.0
        total += (p - (1.0 if b == realized else 0.0)) ** 2
    return total


def _logloss(probs: dict[str, float], realized: str) -> float:
    p = probs.get(realized)
    p = p if isinstance(p, (int, float)) else 0.0
    return -math.log(max(p, EPS))


def _coerce_prob(p) -> float:
    return float(p) if isinstance(p, (int, float)) else 0.0


def score_snapshot(snap: dict) -> SnapshotScore | None:
    """Score one settled snapshot. `snap` carries the bucket ladder with both
    probability columns and the realized bucket label. Returns None if the
    snapshot has not settled (no realized bucket) or carries no buckets."""
    realized = snap.get("realized_label")
    buckets = snap.get("buckets") or []
    if not realized or not buckets:
        return None
    labels = [b["label"] for b in buckets]
    if realized not in labels:
        return None                      # realized outside the ladder — can't score
    council = {b["label"]: _coerce_prob(b.get("model_prob")) for b in buckets}
    market = {b["label"]: _coerce_prob(b.get("market_prob")) for b in buckets}
    return SnapshotScore(
        place=snap.get("place", ""),
        target_date=snap.get("target_date", ""),
        realized_label=realized,
        council_brier=_brier(council, labels, realized),
        market_brier=_brier(market, labels, realized),
        council_logloss=_logloss(council, realized),
        market_logloss=_logloss(market, realized),
        council_p_realized=council.get(realized, 0.0),
        market_p_realized=market.get(realized, 0.0),
    )


def _bootstrap_ci(diffs: list[float], samples: int, seed: int,
                  alpha: float = 0.05) -> tuple[float, float] | None:
    """Seeded paired-bootstrap CI for the mean of per-day (market − council)
    log-loss differences. Resamples days with replacement; the CI excluding 0 is
    the bar a real edge must clear."""
    n = len(diffs)
    if n < 2:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(samples):
        s = sum(diffs[rng.randrange(n)] for _ in range(n)) / n
        means.append(s)
    means.sort()
    lo = means[int((alpha / 2) * samples)]
    hi = means[min(samples - 1, int((1 - alpha / 2) * samples))]
    return (round(lo, 4), round(hi, 4))


def _reliability(scores: list[SnapshotScore], which: str,
                 snapshots: list[dict]) -> tuple[ReliabilityBin, ...]:
    """Pooled reliability diagram for one forecaster: across every (bucket,
    predicted-prob) pair on every settled day, bin by predicted probability and
    compare the mean prediction to how often that bucket actually occurred. A
    well-calibrated forecaster sits on the diagonal (mean_pred ≈ emp_freq)."""
    key = "model_prob" if which == "council" else "market_prob"
    pairs: list[tuple[float, int]] = []
    for snap in snapshots:
        realized = snap.get("realized_label")
        for b in (snap.get("buckets") or []):
            p = b.get(key)
            if isinstance(p, (int, float)):
                pairs.append((float(p), 1 if b["label"] == realized else 0))
    bins = []
    for i in range(RELIABILITY_BINS):
        lo = i / RELIABILITY_BINS
        hi = (i + 1) / RELIABILITY_BINS
        inb = [(p, y) for (p, y) in pairs
               if (p >= lo and (p < hi or (i == RELIABILITY_BINS - 1 and p <= hi)))]
        if inb:
            mp = sum(p for p, _ in inb) / len(inb)
            ef = sum(y for _, y in inb) / len(inb)
            bins.append(ReliabilityBin(round(lo, 2), round(hi, 2),
                                       round(mp, 3), round(ef, 3), len(inb)))
        else:
            bins.append(ReliabilityBin(round(lo, 2), round(hi, 2), None, None, 0))
    return tuple(bins)


def score_snapshots(snapshots: list[dict]) -> EdgeReport:
    """Aggregate settled snapshots into the council-vs-market edge verdict."""
    scored = [s for s in (score_snapshot(s) for s in snapshots) if s is not None]
    n = len(scored)
    if n == 0:
        return EdgeReport(
            n=0, council_brier=None, market_brier=None, council_logloss=None,
            market_logloss=None, logloss_diff=None, logloss_diff_ci=None,
            brier_diff=None, council_reliability=(), market_reliability=(),
            is_edge_validated=False,
            note="no settled council-vs-market snapshots yet — accumulating forward.")

    cb = sum(s.council_brier for s in scored) / n
    mb = sum(s.market_brier for s in scored) / n
    cll = sum(s.council_logloss for s in scored) / n
    mll = sum(s.market_logloss for s in scored) / n
    ll_diffs = [s.market_logloss - s.council_logloss for s in scored]   # >0 ⇒ council better
    ll_diff = sum(ll_diffs) / n
    ci = _bootstrap_ci(ll_diffs, BOOTSTRAP_SAMPLES, BOOTSTRAP_SEED)
    brier_diff = mb - cb

    council_better_both = (cll < mll) and (cb < mb)
    ci_clears_zero = ci is not None and ci[0] > 0
    enough = n >= MIN_SETTLED
    validated = bool(enough and council_better_both and ci_clears_zero)

    if not enough:
        note = (f"{n}/{MIN_SETTLED} settled days — not enough to certify an edge. "
                f"Scores shown are provisional; verdict stays UNVALIDATED.")
    elif validated:
        note = (f"council beats the market on both proper scores over {n} settled "
                f"days, and the paired-bootstrap 95% CI on the log-loss gain "
                f"excludes zero ({ci[0]:+.3f}, {ci[1]:+.3f}) — edge VALIDATED. "
                f"Recommend-only: this certifies calibration, not a trade.")
    else:
        why = []
        if not council_better_both:
            why.append("council does not beat the market on both Brier and log loss")
        if not ci_clears_zero:
            why.append("the bootstrap CI on the log-loss gain includes zero")
        note = (f"{n} settled days, but " + " and ".join(why)
                + " — no edge. Verdict UNVALIDATED.")

    return EdgeReport(
        n=n,
        council_brier=round(cb, 4), market_brier=round(mb, 4),
        council_logloss=round(cll, 4), market_logloss=round(mll, 4),
        logloss_diff=round(ll_diff, 4), logloss_diff_ci=ci,
        brier_diff=round(brier_diff, 4),
        council_reliability=_reliability(scored, "council", snapshots),
        market_reliability=_reliability(scored, "market", snapshots),
        is_edge_validated=validated, note=note,
    )


def report_lines(r: EdgeReport) -> list[str]:
    """Human-readable C7 report for the CLI (recommend-only)."""
    L = ["  C7 — REALIZED-OUTCOME CALIBRATION (council vs market, settled days)"]
    if r.n == 0:
        L.append(f"    {r.note}")
        return L
    edge = "VALIDATED" if r.is_edge_validated else "UNVALIDATED"
    L.append(f"    settled days scored : {r.n}     edge: {edge}")
    L.append(f"    Brier   council {r.council_brier:.4f}  vs  market {r.market_brier:.4f}"
             f"   (council−market {-r.brier_diff:+.4f}; lower is better)")
    L.append(f"    LogLoss council {r.council_logloss:.4f}  vs  market {r.market_logloss:.4f}"
             f"   (council−market {-(r.logloss_diff):+.4f}; lower is better)")
    if r.logloss_diff_ci is not None:
        L.append(f"    log-loss gain (market−council) {r.logloss_diff:+.4f}, "
                 f"95% bootstrap CI [{r.logloss_diff_ci[0]:+.4f}, {r.logloss_diff_ci[1]:+.4f}]")
    L.append(f"    -> {r.note}")
    return L


def report_to_dict(r: EdgeReport) -> dict:
    def _bins(bs):
        return [{"lo": b.lo, "hi": b.hi, "mean_pred": b.mean_pred,
                 "emp_freq": b.emp_freq, "n": b.n} for b in bs]
    return {
        "n": r.n,
        "council_brier": r.council_brier, "market_brier": r.market_brier,
        "council_logloss": r.council_logloss, "market_logloss": r.market_logloss,
        "logloss_diff_market_minus_council": r.logloss_diff,
        "logloss_diff_ci95": list(r.logloss_diff_ci) if r.logloss_diff_ci else None,
        "brier_diff_market_minus_council": r.brier_diff,
        "council_reliability": _bins(r.council_reliability),
        "market_reliability": _bins(r.market_reliability),
        "is_edge_validated": r.is_edge_validated,
        "note": r.note,
    }
