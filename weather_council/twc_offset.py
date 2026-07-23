"""Signed-offset estimator (Plan 4 Phase 3) — which way a tracked forecaster runs vs the oracle.

`storage.tracked_forecast_scores` answers "how wrong" (unsigned MAE). This module answers the
question the human actually asked — "which WAY, how MUCH, how SURE" — for a tracked forecaster
(TWC is the first customer; written generically for `source`), per (city, attr ∈ {high, low}),
against the identical anchored WU settlement oracle the council verdict grades on.

The measured quantity is the SIGNED offset  TWC forecast − actual  in the settlement grain
(positive ⇒ TWC runs ABOVE the oracle). It is a CROSS-REFERENCE measurement only: it adjusts the
displayed TWC line (Phase 4), never the council's numbers, never a vote, never settlement. Any
promotion of TWC into the blend routes through Plan 3's candidate/shadow/promotion machinery.

CERTIFICATION (three mandatory gates — the same discipline edge.py applies to an edge claim):
a `direction` of ABOVE / BELOW is asserted for a (city, attr) cell ONLY when
    n ≥ MIN_N   AND   two-sided binomial sign-test p < ALPHA   AND   the bootstrap CI on the
    median offset excludes zero.
Otherwise:
    * n <  MIN_N               → UNMEASURED (not enough settled days to state a direction);
    * n ≥ MIN_N, gates unmet   → NEUTRAL   (enough data, no detectable directional bias).
Only ABOVE/BELOW carries an offset-adjustment downstream; NEUTRAL/UNMEASURED do not (adjusting by a
non-significant median would be manufacturing a correction). Directions are LIVE measurements, not
permanent facts: because every call recomputes from the settled rows, a growing n that pulls the CI
back over zero REVOKES an ABOVE/BELOW down to NEUTRAL automatically — nothing is cached.

Deterministic (seeded bootstrap, reusing edge.py's SEED/SAMPLES + percentile convention verbatim),
stdlib-only. Read-only. Self-test:  python3 -m weather_council.twc_offset
"""
from __future__ import annotations

__all__ = ["MIN_N", "ALPHA", "OffsetEstimate", "estimate_offsets", "report_lines"]

import math
import random
from dataclasses import dataclass
from math import comb

from . import edge                     # reuse MIN_SETTLED, BOOTSTRAP_SAMPLES/SEED + CI convention

MIN_N = edge.MIN_SETTLED               # 20 — same evidence bar as an edge certification
ALPHA = 0.05                           # sign-test significance AND the CI level (a 95% CI)
EPS = 1e-9                             # |offset| ≤ EPS counts as a tie (excluded from the sign test)


@dataclass(frozen=True)
class OffsetEstimate:
    """One (place, attr) signed-offset cell. `direction` ∈ {ABOVE, BELOW, NEUTRAL, UNMEASURED}."""
    place: str
    attr: str                          # 'high' | 'low'
    grain: str                         # settlement grain the offset is expressed in ('C' | 'F')
    n: int
    median_offset: float | None        # signed TWC − actual; median is PRIMARY (busted-day robust)
    mean_offset: float | None
    ci_95: tuple[float, float] | None  # seeded bootstrap CI on the MEDIAN
    sign_test_p: float | None          # exact two-sided binomial on above/below (ties excluded)
    n_above: int
    n_below: int
    n_ties: int
    direction: str
    mae_twc: float | None              # over rows where both TWC & actual exist
    mae_council: float | None          # over rows where council forecast also exists
    paired_mae_delta: float | None     # council_mae − twc_mae (>0 ⇒ TWC is the better forecaster)

    def label(self) -> str:
        """The compact display label carrying its own uncertainty."""
        if self.direction in ("UNMEASURED", "NEUTRAL"):
            return f"{self.direction} (n={self.n})"
        lo, hi = self.ci_95
        return (f"{self.direction}, median {self.median_offset:+.1f}°{self.grain} "
                f"[CI {lo:+.1f},{hi:+.1f}], n={self.n}, p={self.sign_test_p:.3f}")

    @property
    def is_certified(self) -> bool:
        return self.direction in ("ABOVE", "BELOW")


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    k = len(s)
    mid = k // 2
    return s[mid] if k % 2 else 0.5 * (s[mid - 1] + s[mid])


def _binom_two_sided_p(k: int, n: int) -> float:
    """Exact two-sided binomial sign-test p (H0: p=0.5): total mass of outcomes at least as extreme
    as k successes in n trials. Stdlib `comb`, deterministic. (Same convention as tools.lessons,
    reimplemented locally to keep weather_council a leaf of tools, not a dependant.)"""
    if n == 0:
        return 1.0
    pmf = [comb(n, i) * (0.5 ** n) for i in range(n + 1)]
    obs = pmf[k]
    return min(1.0, sum(p for p in pmf if p <= obs + 1e-12))


def _bootstrap_median_ci(values: list[float], alpha: float = ALPHA) -> tuple[float, float] | None:
    """Seeded bootstrap CI on the MEDIAN — mirrors edge._bootstrap_ci exactly (same SEED, same
    SAMPLES, same percentile picking) but resamples the median rather than the mean, so the CI is
    reproducible and consistent with the rest of the system."""
    n = len(values)
    if n < 2:
        return None
    rng = random.Random(edge.BOOTSTRAP_SEED)
    meds = []
    for _ in range(edge.BOOTSTRAP_SAMPLES):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        meds.append(_median(sample))
    meds.sort()
    lo = meds[int((alpha / 2) * edge.BOOTSTRAP_SAMPLES)]
    hi = meds[min(edge.BOOTSTRAP_SAMPLES - 1, int((1 - alpha / 2) * edge.BOOTSTRAP_SAMPLES))]
    return (round(lo, 4), round(hi, 4))


def build_estimate(place: str, attr: str, offsets: list[float],
                   council_abs_errs: list[float] | None = None,
                   grain: str = "C") -> OffsetEstimate:
    """Build one (place, attr) estimate from the signed offsets (TWC − actual) and, optionally, the
    paired council absolute errors. Pure — no I/O. Applies the three-gate certification."""
    n = len(offsets)
    if n == 0:
        return OffsetEstimate(place, attr, grain, 0, None, None, None, None, 0, 0, 0,
                              "UNMEASURED", None, None, None)

    n_above = sum(1 for o in offsets if o > EPS)
    n_below = sum(1 for o in offsets if o < -EPS)
    n_ties = n - n_above - n_below
    n_eff = n_above + n_below
    sign_p = _binom_two_sided_p(n_above, n_eff) if n_eff else 1.0

    med = round(_median(offsets), 4)
    mean = round(sum(offsets) / n, 4)
    ci = _bootstrap_median_ci(offsets)

    mae_twc = round(sum(abs(o) for o in offsets) / n, 4)
    mae_council = paired_delta = None
    if council_abs_errs:
        mae_council = round(sum(council_abs_errs) / len(council_abs_errs), 4)
        paired_delta = round(mae_council - mae_twc, 4)

    # The three gates. A direction is asserted only when ALL hold; else NEUTRAL (enough data, no
    # detectable bias) or UNMEASURED (too few days). Recomputed every call ⇒ revocable.
    ci_excludes_zero = ci is not None and (ci[0] > 0 or ci[1] < 0)
    certified = n >= MIN_N and sign_p < ALPHA and ci_excludes_zero
    if certified:
        direction = "ABOVE" if med > 0 else "BELOW"
    elif n >= MIN_N:
        direction = "NEUTRAL"
    else:
        direction = "UNMEASURED"

    return OffsetEstimate(place, attr, grain, n, med, mean, ci, round(sign_p, 4),
                          n_above, n_below, n_ties, direction, mae_twc, mae_council, paired_delta)


def _grain_for(place: str) -> str:
    """Settlement grain of the stored forecast values. Basket cities settle whole-°C (logged in °C);
    a °F-settling city (KSFO) would be logged in °F. Default °C — the current tracked basket."""
    return "F" if "san francisco" in (place or "").lower() else "C"


def estimate_offsets(source: str = "twc", db_path=None) -> list[OffsetEstimate]:
    """Read settled `source` rows from tracked_forecasts and build a signed-offset estimate per
    (place, attr). Only rows whose anchored actual is present are used (leak-free by construction —
    settlement already gated on truth availability). Read-only."""
    from . import storage
    conn = storage._connect() if db_path is None else storage._connect_at(db_path)
    try:
        rows = conn.execute(
            "SELECT place, fc_high, fc_low, council_high, council_low, actual_high, actual_low "
            "FROM tracked_forecasts WHERE source=? AND actual_high IS NOT NULL",
            (source,)).fetchall()
    finally:
        conn.close()

    # place -> attr -> (offsets, council_abs_errs)
    acc: dict[str, dict[str, tuple[list, list]]] = {}
    for place, fh, fl, ch, cl, ah, al in rows:
        cell = acc.setdefault(place, {"high": ([], []), "low": ([], [])})
        if fh is not None and ah is not None:
            cell["high"][0].append(float(fh) - float(ah))
            if ch is not None:
                cell["high"][1].append(abs(float(ch) - float(ah)))
        if fl is not None and al is not None:
            cell["low"][0].append(float(fl) - float(al))
            if cl is not None:
                cell["low"][1].append(abs(float(cl) - float(al)))

    out = []
    for place in sorted(acc):
        grain = _grain_for(place)
        for attr in ("high", "low"):
            offsets, council_errs = acc[place][attr]
            if offsets:
                out.append(build_estimate(place, attr, offsets, council_errs, grain))
    return out


def report_lines(estimates: list[OffsetEstimate]) -> list[str]:
    """Human table (recommend-only). Certified cells first; UNMEASURED honestly last."""
    L = [f"TWC SIGNED-OFFSET vs the WU oracle (recommend-only; TWC never votes or settles; "
         f"cert bar: n≥{MIN_N} & sign-p<{ALPHA} & CI excludes 0)"]
    if not estimates:
        L.append("  (no settled tracked rows yet — accruing)")
        return L
    order = {"ABOVE": 0, "BELOW": 0, "NEUTRAL": 1, "UNMEASURED": 2}
    for e in sorted(estimates, key=lambda e: (order.get(e.direction, 3), e.place, e.attr)):
        skill = ""
        if e.paired_mae_delta is not None:
            better = "TWC better" if e.paired_mae_delta > 0 else "council better"
            skill = f"  | MAE twc {e.mae_twc} vs council {e.mae_council} ({better})"
        L.append(f"  {e.place[:22]:22} {e.attr:4}  {e.label()}{skill}")
    return L


def _selftest() -> None:
    # 1) planted +1.3° bias, n=25 all positive -> ABOVE, CI excludes 0, median≈1.3, p tiny.
    planted = [1.3 + 0.1 * ((i % 5) - 2) for i in range(25)]      # 1.1..1.5, all > 0
    e = build_estimate("Test", "high", planted)
    assert e.direction == "ABOVE", e.direction
    assert e.n_above == 25 and e.n_below == 0
    assert e.ci_95[0] > 0 and e.sign_test_p < ALPHA
    assert abs(e.median_offset - 1.3) < 0.2, e.median_offset

    # 2) symmetric zero-bias noise, n=40 -> NEUTRAL (enough data, no detectable bias; CI spans 0).
    noise = [(-1) ** i * (0.4 + 0.05 * (i % 3)) for i in range(40)]   # ± around 0
    z = build_estimate("Test", "high", noise)
    assert z.n >= MIN_N and z.direction == "NEUTRAL", z.direction
    assert z.ci_95[0] <= 0 <= z.ci_95[1]

    # 3) a 19-day planted bias is UNMEASURED (n<MIN_N gate dominates however clean the signal).
    short = [1.3] * 19
    s = build_estimate("Test", "high", short)
    assert s.n == 19 and s.direction == "UNMEASURED", s.direction
    assert not s.is_certified

    # 4) BELOW: planted −0.9° bias.
    below = [-0.9 + 0.1 * ((i % 5) - 2) for i in range(22)]
    b = build_estimate("Test", "low", below)
    assert b.direction == "BELOW" and b.median_offset < 0 and b.ci_95[1] < 0

    # 5) paired MAE skill: TWC errors smaller than council's -> paired_mae_delta > 0.
    off = [0.2 * ((i % 3) - 1) for i in range(25)]                # small, centered
    council = [1.0] * 25                                          # council is worse
    p = build_estimate("Test", "high", off, council)
    assert p.paired_mae_delta is not None and p.paired_mae_delta > 0

    # 6) determinism: same inputs -> identical CI (seeded).
    assert build_estimate("Test", "high", planted).ci_95 == e.ci_95
    print("twc_offset selftest PASSED (ABOVE/BELOW/NEUTRAL/UNMEASURED three-gate certification; "
          "median-primary; seeded bootstrap CI; n<20 gate dominates; paired MAE skill; deterministic)")


if __name__ == "__main__":
    _selftest()
