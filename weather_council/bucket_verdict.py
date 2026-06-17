"""Bucket-verdict simulation — score the council on the object the market PAYS on.

The market does not pay on continuous accuracy. It pays on the **whole-degree
settlement bucket**: the station's high (or low) is read off, rounded half-up to
an integer, and the contract for that integer pays. A council with an excellent
continuous MAE can still name the WRONG bucket often — if its verdicts cluster
near the half-degree settlement boundaries, where a sub-degree error flips the
payout. MAE/CRPS/coverage never see that fragility; this module does.

What it measures (leak-free, over the SAME walk-forward Council._validate uses)
------------------------------------------------------------------------------
For each held-out day past a warmup, the council's MODAL bucket is its point
verdict dressed with the STRICTLY-earlier residual cloud — each draw rounded to
the settlement integer, the most frequent integer taken. That modal bucket is
compared to the realized settlement bucket (round-half-up of the actual). We
accumulate:

  * hit_rate          — frac of days the modal bucket == the realized bucket
                        (the economically-relevant score the market settles on).
  * point_hit_rate    — same, but for the bare point verdict's own bucket
                        (no cloud); isolates whether the cloud's mode helps.
  * signed_bias       — mean(modal − realized); a non-zero value is a DIRECTIONAL
                        off-by-one tendency: the cloud/point centre is biased, so
                        misses lean systematically high or low. Lever: de-bias /
                        recency-weight the residual centre.
  * edge fragility    — mean edge-distance (verdict's distance to the nearest
                        settlement boundary, 0 = on a boundary, 0.5 = bucket
                        centre) on HITS vs MISSES. When misses sit at much smaller
                        edge-distance than hits, the misses are boundary-driven:
                        the verdict is right to within rounding but lands on the
                        wrong side of a coin-flip edge. Lever: sharpen the cloud,
                        or abstain when the verdict sits on a boundary.

Which lever the data points to is the whole purpose: a directional bias and a
fragility problem call for DIFFERENT fixes, and this separates them.

Discipline
----------
  * Leak-free: day i's modal bucket uses ONLY residuals from days < i — identical
    walk-forward to Council._validate and calibration.coverage_calibration_eval.
  * Deterministic: no sampling; the modal bucket is the exact mode of the finite
    residual-dressed draw set, with a deterministic tie-break (nearest to the
    point verdict's own bucket, then the lower integer).
  * MEASURE-ONLY: this never moves the served verdict, never trades. It reports a
    finding for human review, like the other recommend-only diagnostics.
  * Single source of truth for settlement rounding: reuses sources._round_half_up,
    so a simulated bucket can never disagree with what the council would serve.

Stdlib only (math, statistics). No numpy/scipy.
"""

from __future__ import annotations

__all__ = [
    'BucketVerdictEval', 'bucket_verdict_eval', 'bucket_verdict_eval_grouped',
]

from dataclasses import dataclass

from .sources import _round_half_up

# A day's modal bucket needs a minimum prior residual cloud before it means
# anything — the same floor the incumbent CRPS cloud uses in Council._validate.
WARMUP = 10
# Below this many scored days the bucket hit-rate is too noisy to report.
MIN_SCORED = 20


@dataclass(frozen=True)
class BucketVerdictEval:
    """The leak-free whole-degree bucket-verdict scorecard for one stream (or a
    pool of streams). All rates are in [0, 1]; signed_bias and edges are in
    whole-degree / fraction-of-a-degree units."""
    n_scored: int
    hit_rate: float            # P(modal bucket == realized settlement bucket)
    point_hit_rate: float      # P(point verdict's own bucket == realized)
    signed_bias: float         # mean(modal − realized); != 0 => directional off-by-one
    frac_over: float           # frac of days modal > realized (named too warm)
    frac_under: float          # frac of days modal < realized (named too cool)
    mean_edge_hit: float       # mean boundary-distance on HIT days (0..0.5)
    mean_edge_miss: float      # mean boundary-distance on MISS days (0..0.5)

    @property
    def fragility(self) -> float:
        """How much smaller the verdict's boundary-distance is on misses than on
        hits. Large positive => misses are boundary-driven (a coin-flip edge),
        not gross errors — the verdict is right to within rounding but lands on
        the wrong side. Lever: sharpen the cloud / abstain on boundary days."""
        return self.mean_edge_hit - self.mean_edge_miss


def _edge_distance(point: float) -> float:
    """Distance from a verdict to the nearest whole-degree settlement boundary.

    With round-half-up, value v settles in bucket floor(v+0.5), so the bucket
    boundaries sit at the half-integers. 0.0 means v is exactly on a boundary (a
    coin-flip between two buckets); 0.5 means v is at a bucket centre (robust to
    a half-degree of error in either direction)."""
    f = (point + 0.5) % 1.0
    return min(f, 1.0 - f)


def _modal_bucket(point: float, prior_resid: list[float]) -> int:
    """The most-frequent settlement integer when the point verdict is dressed
    with the prior residual cloud (each draw rounded half-up). Deterministic
    tie-break: among equally-frequent buckets pick the one nearest the point
    verdict's OWN bucket, then the lower integer — so the result never depends on
    cloud ordering."""
    point_bucket = _round_half_up(point)
    counts: dict[int, int] = {}
    for r in prior_resid:
        k = _round_half_up(point + r)
        counts[k] = counts.get(k, 0) + 1
    # max count, then nearest the point verdict's own bucket, then the lower
    # integer — a total order, so the mode never depends on cloud ordering.
    return max(counts, key=lambda k: (counts[k], -abs(k - point_bucket), -k))


class _BVAcc:
    """Pooled bucket-verdict tallies. Each stream contributes against its OWN
    expanding residual cloud (reset per stream), but the counts accumulate here so
    one finalize covers single- and multi-stream scoring identically."""
    __slots__ = ("n", "hits", "point_hits", "signed_sum", "over", "under",
                 "edge_hit_sum", "edge_miss_sum")

    def __init__(self) -> None:
        self.n = self.hits = self.point_hits = 0
        self.signed_sum = self.over = self.under = 0
        self.edge_hit_sum = self.edge_miss_sum = 0.0


def _score_stream(pairs: list[tuple[float, float]], acc: _BVAcc, *, warmup: int) -> None:
    """Walk ONE ordered (point_verdict, observed) stream, scoring each day past
    the warmup against the strictly-earlier residual cloud — leak-free by
    construction (today's residual is appended only AFTER it is used). Folds the
    per-day outcomes into the shared accumulator."""
    prior_resid: list[float] = []
    for pred, obs in pairs:
        if len(prior_resid) >= warmup:
            realized = _round_half_up(obs)
            modal = _modal_bucket(pred, prior_resid)
            edge = _edge_distance(pred)
            acc.n += 1
            acc.point_hits += 1 if _round_half_up(pred) == realized else 0
            d = modal - realized
            acc.signed_sum += d
            acc.over += 1 if d > 0 else 0
            acc.under += 1 if d < 0 else 0
            if modal == realized:
                acc.hits += 1
                acc.edge_hit_sum += edge
            else:
                acc.edge_miss_sum += edge
        prior_resid.append(obs - pred)


def _finalize(acc: _BVAcc, *, min_scored: int) -> BucketVerdictEval | None:
    if acc.n < min_scored:
        return None
    misses = acc.n - acc.hits
    return BucketVerdictEval(
        n_scored=acc.n,
        hit_rate=acc.hits / acc.n,
        point_hit_rate=acc.point_hits / acc.n,
        signed_bias=acc.signed_sum / acc.n,
        frac_over=acc.over / acc.n,
        frac_under=acc.under / acc.n,
        mean_edge_hit=(acc.edge_hit_sum / acc.hits) if acc.hits else 0.0,
        mean_edge_miss=(acc.edge_miss_sum / misses) if misses else 0.0,
    )


def bucket_verdict_eval(
    pairs: list[tuple[float, float]],
    *,
    warmup: int = WARMUP,
    min_scored: int = MIN_SCORED,
) -> BucketVerdictEval | None:
    """Score one ordered (point_verdict, observed) stream on whole-degree buckets.

    `pairs` are in walk-forward order. The residual cloud is built EXPANDING and
    leak-free: day i's modal bucket is taken over residuals from days < i only,
    exactly mirroring how the council's served cloud grows. Returns None when too
    few days clear the warmup to give a stable rate."""
    acc = _BVAcc()
    _score_stream(pairs, acc, warmup=warmup)
    return _finalize(acc, min_scored=min_scored)


def bucket_verdict_eval_grouped(
    streams: list[list[tuple[float, float]]],
    *,
    warmup: int = WARMUP,
    min_scored: int = MIN_SCORED,
) -> BucketVerdictEval | None:
    """Score several streams (e.g. high and low) each against its OWN expanding
    residual cloud, then POOL the per-day outcomes. Each attribute is a distinct
    market the council serves separately, so it must be scored against its own
    cloud — pooling the raw (pred, obs) pairs would mix two different settlement
    objects. Mirrors calibration.coverage_calibration_eval_grouped."""
    acc = _BVAcc()
    for pairs in streams:
        _score_stream(pairs, acc, warmup=warmup)
    return _finalize(acc, min_scored=min_scored)


def _self_test() -> None:
    """Deterministic oracles: a centred sharp cloud names buckets nearly always;
    a biased point misses directionally; boundary-pinned verdicts miss at the
    coin-flip edge (low edge-distance on misses)."""
    import random

    # 1) Verdicts at bucket CENTRES with a tight, centred cloud => near-perfect
    #    bucket hits, no directional bias.
    rng = random.Random(1)
    pairs = []
    for _ in range(400):
        centre = rng.randint(10, 30)            # integer => bucket centre
        err = rng.gauss(0.0, 0.12)              # << 0.5, rarely crosses a boundary
        pairs.append((float(centre), centre + err))   # pred=centre, obs=centre+err
    ev = bucket_verdict_eval(pairs)
    assert ev is not None and ev.hit_rate > 0.95, ev
    assert abs(ev.signed_bias) < 0.05, ev

    # 2a) A CONSTANT point bias is fully corrected by the residual cloud: the
    #     cloud's mean is the bias, so the modal bucket recovers the truth even
    #     though the bare POINT verdict is one bucket low. This is the desired
    #     behaviour — a stationary bias is not an inefficiency the market sees.
    rng = random.Random(2)
    pairs = [(realized - 0.7, float(realized))
             for realized in (rng.randint(10, 30) for _ in range(400))]
    ev = bucket_verdict_eval(pairs)
    assert ev is not None
    assert ev.hit_rate > 0.95, ev               # cloud de-biases the modal bucket
    assert ev.point_hit_rate < 0.05, ev         # the bare point is off-by-one low
    assert abs(ev.signed_bias) < 0.05, ev

    # 2b) A DRIFTING bias is NOT corrected: the trailing cloud's mean lags the
    #     growing bias, so the modal bucket is dragged low — a directional
    #     (negative) signed_bias with more under- than over-shoots. This is the
    #     genuine inefficiency the simulation must surface (the London failure
    #     mode), distinct from the harmless constant bias above.
    rng = random.Random(22)
    pairs = []
    for i in range(500):
        realized = rng.randint(10, 30)
        bias = 0.02 * i                          # grows over the walk-forward
        pairs.append((realized - bias, float(realized)))
    ev = bucket_verdict_eval(pairs)
    assert ev is not None
    assert ev.signed_bias < -0.3, ev            # trailing cloud under-corrects
    assert ev.frac_under > ev.frac_over, ev
    assert ev.hit_rate < 0.7, ev

    # 3) Verdicts spread ACROSS the bucket (edge-distance varies) with small
    #    centred noise: misses concentrate where the verdict sits near a
    #    settlement boundary, so miss-day edge-distance is materially smaller than
    #    hit-day edge-distance (fragility > 0) — boundary-driven, not gross error.
    rng = random.Random(3)
    pairs = []
    for _ in range(2000):
        base = rng.randint(10, 30)
        pred = base + rng.uniform(-0.5, 0.5)     # anywhere in the bucket
        obs = pred + rng.gauss(0.0, 0.25)
        pairs.append((pred, obs))
    ev = bucket_verdict_eval(pairs)
    assert ev is not None
    assert ev.fragility > 0.05, ev               # misses sit closer to the edge

    # 4) Leak-free warmup: with exactly warmup+min_scored pairs, only those past
    #    the warmup are scored (each day uses strictly-earlier residuals).
    rng = random.Random(4)
    n = WARMUP + MIN_SCORED
    pairs = [(float(rng.randint(10, 30)), float(rng.randint(10, 30))) for _ in range(n)]
    ev = bucket_verdict_eval(pairs, min_scored=1)
    assert ev.n_scored == n - WARMUP, ev

    # 5) Grouped pooling == n-weighted average of per-stream hit-rates (each
    #    stream scored against its own cloud).
    rng = random.Random(5)
    hi = [(float(rng.randint(20, 34)), float(rng.randint(20, 34))) for _ in range(300)]
    lo = [(float(rng.randint(5, 18)), float(rng.randint(5, 18))) for _ in range(300)]
    g = bucket_verdict_eval_grouped([hi, lo])
    e_hi = bucket_verdict_eval(hi)
    e_lo = bucket_verdict_eval(lo)
    assert g.n_scored == e_hi.n_scored + e_lo.n_scored, g
    expected = (e_hi.hit_rate * e_hi.n_scored
                + e_lo.hit_rate * e_lo.n_scored) / g.n_scored
    assert abs(g.hit_rate - expected) < 1e-9, (g.hit_rate, expected)

    # 6) Thin stream => None.
    assert bucket_verdict_eval([(20.0, 20.0)] * (MIN_SCORED + WARMUP - 1)) is None

    print("bucket_verdict self-test PASSED "
          "(centred=names buckets; biased=directional miss; boundary=fragile; "
          "leak-free warmup; grouped==n-weighted; thin=None)")


if __name__ == "__main__":
    _self_test()
