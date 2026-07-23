#!/usr/bin/env python3
"""Dispersion-aware bucket-CONFIDENCE gate — when is a single-bucket call sub-skill?

CRUX (loop iter-1, ledger entry 16): settlement-bucket misses are DISPERSION, not
bias (signed bias ~0 on every stream) and not boundary fragility (HK miss edge-dist
== hit edge-dist; abstaining near boundaries moves HK 44%->44%). Where the leak-free
forecast sd exceeds the +-0.5degC bucket half-width, naming ONE whole-degree bucket
is a coin flip, and the +EV action is to WIDEN (multi-bucket) / ABSTAIN / size down
-- NOT to "predict the bucket better" (you cannot: the NWP error is larger than the
bucket, and behind HK's 38-day truth lag no temporal corrector certifies).

WHAT THIS ADDS over the siblings
--------------------------------
  * bucket_verdict.py        scores boundary EDGE-fragility -> shown null for HK.
  * conditional_bucket_*.py  compares heteroscedastic vs pooled cloud WIDTH on Brier.
  * meteogram (tail node)    gates only TAIL buckets.
This gate is the missing ACTION layer for the MODAL bucket: a leak-free per-day score
that says "is today a day where a single-bucket call is skillful, or a coin flip?" and
turns that into an abstain/size-down recommendation. Recommend-only; never trades.

THE SCORE (leak-free, lag-aware, ADAPTIVE sigma)
------------------------------------------------
For each scored day t, with point p_t and residual sd s_t estimated from a TRAILING
WINDOW of residuals ending STRICTLY earlier than (t - lag):
    b       = round_half_up(p_t)                      # the bucket the point names
    p_conf  = Phi((b+.5 - p_t)/s_t) - Phi((b-.5 - p_t)/s_t)
            = the model's OWN probability the realized whole-degree bucket == b.
p_conf is high when s_t is small AND p_t sits near a bucket centre; low when s_t is
large OR p_t sits near a boundary.

WHY A TRAILING WINDOW, NOT A POOLED SD (the iter-2 lesson)
---------------------------------------------------------
A POOLED full-history residual sd is a single constant; with it, p_conf varies ONLY
via the point's position inside its bucket -- i.e. it COLLAPSES to the boundary
edge-distance signal already shown NULL for HK. The per-day dispersion that actually
separates skill from coin flip is the REGIME (HK summer wide / winter tight), which a
pooled sd washes out but a trailing window RECOVERS -- and recovers from the
(point, realized) stream alone, leak-free. The falsifiable flip side: dispersion that
is NOT regime-blocked (high-frequency, day-to-day) is NOT recoverable by any trailing
window, and the gate correctly refuses to fire on it (selftest Box B control). Behind
HK's 38-day truth lag the window is stale, so whether the regime signal survives is an
empirical question -- which the live run answers, not asserts.

CERTIFICATION (each both-walk-forward-halves gated -- the overfit guard)
-----------------------------------------------------------------------
  (1) CALIBRATION  mean(p_conf) ~= empirical single-bucket hit rate, both halves
                   (the score is HONEST about how often it will be right).
  (2) SEPARATION   top-half-p_conf days hit materially more than bottom-half-p_conf
                   days, on BOTH time-halves (the score actually separates skill from
                   coin flip; only then is the gate worth acting on).

DECISION (recommend-only)
-------------------------
tau defaults to the uniform-guess rate 1/B (B = achievable buckets in the stream):
a single-bucket call whose self-assessed hit prob is below 1/B is worse than just
spreading the stake across the ladder. On CONFIDENT days (p_conf >= tau) a
single-bucket call is sanctioned; on FLAGGED days recommend WIDEN / ABSTAIN / size
down.

Stdlib only (math, statistics). Deterministic; caller-seeded RNG in the selftest.
Exits nonzero on any selftest failure.
"""
from __future__ import annotations

import csv
import math
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from weather_council.sources import _round_half_up as round_half_up  # noqa: E402


# --------------------------------------------------------------------- primitives
def Phi(z: float) -> float:
    """Standard-normal CDF via the stdlib error function (no scipy)."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def bucket_prob(p: float, sigma: float, b: int) -> float:
    """P(realized whole-degree bucket == b) under N(p, sigma^2)."""
    sigma = max(sigma, 1e-9)
    return Phi((b + 0.5 - p) / sigma) - Phi((b - 0.5 - p) / sigma)


# ------------------------------------------------------------ leak-free confidence
def bucket_confidence(rows, lag: int = 0, warmup: int = 40, window: int = 45,
                      min_hist: int = 10):
    """Walk-forward, leak-free per-day bucket-confidence on a (point, realized) stream.

    rows: list of (point, realized) in date order (both whole-degC settlement units).
    sigma is the sd of a TRAILING WINDOW of `window` residuals ending strictly before
    (t - lag) -- adaptive so it tracks the dispersion REGIME (the iter-2 lesson). With
    window>=hi it reduces to the pooled sd. Returns list of dicts
    {t, p, obs, sigma, p_conf, hit} for each scored day."""
    pts = [r[0] for r in rows]
    obs = [r[1] for r in rows]
    out = []
    for t in range(warmup, len(rows)):
        hi = t - lag
        if hi < min_hist:
            continue
        lo = max(0, hi - window)
        res = [obs[s] - pts[s] for s in range(lo, hi)]
        sigma = (statistics.pstdev(res) if len(res) >= 2 else 1.0) or 1.0
        b = round_half_up(pts[t])
        out.append({
            "t": t,
            "p": pts[t],
            "obs": obs[t],
            "sigma": sigma,
            "p_conf": bucket_prob(pts[t], sigma, b),
            "hit": 1.0 if round_half_up(pts[t]) == round_half_up(obs[t]) else 0.0,
        })
    return out


def _half_separation(scored):
    """median-split p_conf within a window -> hit(top) - hit(bottom)."""
    if len(scored) < 4:
        return 0.0
    m = statistics.median(d["p_conf"] for d in scored)
    top = [d["hit"] for d in scored if d["p_conf"] >= m]
    bot = [d["hit"] for d in scored if d["p_conf"] < m]
    if not top or not bot:
        return 0.0
    return statistics.fmean(top) - statistics.fmean(bot)


def certify(scored, sep_margin: float = 0.05, cal_tol: float = 0.10):
    """Both-halves certification of the confidence score.

    Returns dict with calibration error, separation, and the two both-halves flags.
    A score is ACTIONABLE only if it separates skill from coin flip on BOTH halves."""
    n = len(scored)
    if n < 20:
        return {"n": n, "actionable": False, "reason": "insufficient scored days"}
    h = n // 2
    first, second = scored[:h], scored[h:]

    mean_conf = statistics.fmean(d["p_conf"] for d in scored)
    mean_hit = statistics.fmean(d["hit"] for d in scored)
    cal_err = abs(mean_conf - mean_hit)
    cal_ok = (abs(statistics.fmean(d["p_conf"] for d in first) -
                  statistics.fmean(d["hit"] for d in first)) <= cal_tol and
              abs(statistics.fmean(d["p_conf"] for d in second) -
                  statistics.fmean(d["hit"] for d in second)) <= cal_tol)

    s1, s2 = _half_separation(first), _half_separation(second)
    sep_overall = _half_separation(scored)
    sep_ok = (s1 > 0 and s2 > 0 and sep_overall >= sep_margin)

    return {
        "n": n,
        "mean_conf": mean_conf,
        "mean_hit": mean_hit,
        "cal_err": cal_err,
        "calibrated_both_halves": cal_ok,
        "sep_h1": s1,
        "sep_h2": s2,
        "sep_overall": sep_overall,
        "separates_both_halves": sep_ok,
        "actionable": sep_ok,
    }


def gate_decision(scored, n_buckets: int = 30, flag_quantile: float = 1.0 / 3.0):
    """Recommend-only split of scored days by confidence.

    DECISION split is DATA-RELATIVE: FLAG the least-confident `flag_quantile` of days
    (default bottom third) and recommend WIDEN / size-down on them; the rest are
    CONFIDENT (single-bucket sanctioned). A data-relative cut always bites and is what
    the both-halves SEPARATION certifies. The absolute uniform-guess floor (1/B) is
    reported only as context: `below_uniform_share` = days whose self-assessed hit
    prob is under 1/B, i.e. worse than spreading the stake across the whole ladder."""
    ps = sorted(d["p_conf"] for d in scored)
    if not ps:
        return {"n_conf": 0, "n_flag": 0, "hit_conf": float("nan"),
                "hit_flag": float("nan"), "flag_share": float("nan"),
                "below_uniform_share": float("nan"), "cut": float("nan")}
    cut = ps[min(int(len(ps) * flag_quantile), len(ps) - 1)]
    flag = [d for d in scored if d["p_conf"] <= cut]
    conf = [d for d in scored if d["p_conf"] > cut]
    hit = lambda xs: statistics.fmean(d["hit"] for d in xs) if xs else float("nan")
    tau = 1.0 / max(n_buckets, 1)
    return {
        "cut": cut,
        "n_conf": len(conf),
        "n_flag": len(flag),
        "hit_conf": hit(conf),
        "hit_flag": hit(flag),
        "flag_share": len(flag) / len(scored),
        "below_uniform_share": sum(1 for d in scored if d["p_conf"] < tau) / len(scored),
    }


# ----------------------------------------------------------------------- selftest
def _synth(seed, n, point, sd_fn):
    """(point, realized) stream; realized = point + N(0, sd_fn(i))."""
    rng = random.Random(seed)
    return [(point, point + rng.gauss(0, sd_fn(i))) for i in range(n)]


def _selftest():
    fails = []

    def check(box, cond, msg):
        print(f"   [{'PASS' if cond else 'FAIL'}] {msg}")
        if not cond:
            fails.append(f"{box}: {msg}")

    print("=" * 84)
    print("bucket_confidence_backtest — known-answer selftest (recovery + falsifiable control)")
    print("=" * 84)

    # -- Box A: calibration recovers on a stationary Gaussian stream ---------------
    print("\n[A] confidence is calibrated: mean(p_conf) ~= empirical hit (both halves)")
    rows = _synth(1, 600, 20.3, lambda i: 0.6)          # constant sd, off-centre point
    sc = bucket_confidence(rows, lag=0, warmup=40)
    cert = certify(sc)
    check("A", cert["cal_err"] < 0.05,
          f"|mean_conf - mean_hit| = {cert['cal_err']:.3f} < 0.05 "
          f"(conf {cert['mean_conf']:.3f} vs hit {cert['mean_hit']:.3f})")
    check("A", cert["calibrated_both_halves"], "calibrated on BOTH walk-forward halves")

    # -- Box B: SEPARATION on RECOVERABLE (regime) dispersion + falsifiable control -
    print("\n[B] score SEPARATES skill from coin flip on REGIME dispersion; "
          "NOT on un-recoverable high-frequency dispersion")
    # positive: dispersion is REGIME-BLOCKED (tight blocks vs wide blocks) -> a
    # trailing window adapts -> wide-block days are low-confidence AND low-hit.
    def _block_sd(i, blk=60, tight=0.35, wide=1.5):
        return tight if (i // blk) % 2 == 0 else wide
    rows_reg = _synth(2, 720, 20.0, _block_sd)
    sc_reg = bucket_confidence(rows_reg, lag=0, warmup=40, window=30)
    cert_reg = certify(sc_reg)
    check("B", cert_reg["separates_both_halves"],
          f"regime-block dispersion -> separation certified "
          f"(h1 {cert_reg['sep_h1']:+.3f}, h2 {cert_reg['sep_h2']:+.3f}, "
          f"overall {cert_reg['sep_overall']:+.3f})")
    # negative control: SAME marginal dispersion mix, but ALTERNATING day-to-day --
    # a trailing window averages it to a constant sd, so the regime signal is NOT
    # recoverable and the gate must NOT certify (the lag-wall lesson, in miniature).
    rows_hf = _synth(2, 720, 20.0, lambda i: 0.35 if i % 2 == 0 else 1.5)
    sc_hf = bucket_confidence(rows_hf, lag=0, warmup=40, window=30)
    cert_hf = certify(sc_hf)
    check("B", not cert_hf["separates_both_halves"],
          f"high-frequency (un-recoverable) dispersion -> separation NOT certified "
          f"(h1 {cert_hf['sep_h1']:+.3f}, h2 {cert_hf['sep_h2']:+.3f}, "
          f"overall {cert_hf['sep_overall']:+.3f})")

    # -- Box C: leak-free -- sigma[t] uses ONLY the trailing window before t-lag ----
    print("\n[C] leak-free: the day-t sigma reuses only the trailing window before t - lag")
    rows_ramp = _synth(4, 300, 20.0, lambda i: 0.4 + 1.6 * (i / 300))   # spread ramps up
    lag, win = 7, 45
    sc_ramp = bucket_confidence(rows_ramp, lag=lag, warmup=40, window=win)
    ok = True
    pts = [r[0] for r in rows_ramp]; obs = [r[1] for r in rows_ramp]
    for d in sc_ramp[:50]:
        hi = d["t"] - lag
        lo = max(0, hi - win)
        seg = [obs[s] - pts[s] for s in range(lo, hi)]
        expect = (statistics.pstdev(seg) if len(seg) >= 2 else 1.0) or 1.0
        if abs(expect - d["sigma"]) > 1e-12:
            ok = False
            break
    check("C", ok, "engine sigma == independently recomputed window-only sigma (no future leak)")
    # on a ramping-spread stream the lagged window UNDER-states the present spread,
    # so realized hits the named bucket LESS than the (stale, too-tight) p_conf prices
    cert_ramp = certify(sc_ramp)
    check("C", cert_ramp["mean_conf"] > cert_ramp["mean_hit"],
          f"ramped spread + lag -> over-confident (conf {cert_ramp['mean_conf']:.3f} "
          f"> hit {cert_ramp['mean_hit']:.3f}); the lag wall makes the gate honest about itself")

    # -- Box D: gate splits high/low confidence into high/low realized hit ----------
    print("\n[D] gate_decision: CONFIDENT days out-hit FLAGGED days")
    gd = gate_decision(sc_reg, n_buckets=8)
    check("D", gd["n_flag"] > 0 and gd["hit_conf"] > gd["hit_flag"],
          f"confident (top 2/3) hit {gd['hit_conf']:.1%} > flagged (bottom 1/3) hit "
          f"{gd['hit_flag']:.1%}")

    print("\n" + "=" * 84)
    if fails:
        print(f"FAILED {len(fails)} check(s):")
        for f in fails:
            print("   -", f)
        return 1
    print("ALL BOXES GREEN — confidence is calibrated, separates skill from coin flip "
          "(both halves),\n               leak-free under the lag wall, and gates "
          "single-bucket calls correctly.")
    print("=" * 84)
    return 0


# --------------------------------------------------------------------- live report
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports")
STREAMS = [
    ("hong_kong_high", "HKO high", 38),
    ("hong_kong_low",  "HKO low",  38),
    ("london_high",    "EGLC high", 0),
    ("london_low",     "EGLC low",  0),
]


def _load(name):
    rows = []
    with open(os.path.join(DATA, name + ".csv")) as fh:
        for r in csv.DictReader(fh):
            rows.append((float(r["point"]), float(r["realized"])))
    return rows


def _live():
    print("\n" + "=" * 92)
    print("LIVE — dispersion-aware bucket-confidence gate on the council CSVs (lag-aware, leak-free)")
    print("=" * 92)
    print("Split: FLAGGED = least-confident third of days; CONFIDENT = top two-thirds.\n")
    print(f"{'stream':10} {'n':>4} {'hit':>6} {'conf':>6} {'cal':>4} "
          f"{'sep h1/h2':>13} {'cert?':>5} | {'CONF hit':>8} {'FLAG hit':>8} {'gap':>6} "
          f"{'<1/B':>5} -> action")
    for name, label, lag in STREAMS:
        rows = _load(name)
        sc = bucket_confidence(rows, lag=lag, warmup=60)
        cert = certify(sc)
        n_buckets = len(set(round_half_up(o) for _, o in rows))
        gd = gate_decision(sc, n_buckets)
        gap = gd["hit_conf"] - gd["hit_flag"]
        action = ("single-bucket gate OK -> WIDEN bottom third"
                  if cert["actionable"] else "no certified gate -> size by regime")
        print(f"{label:10} {cert['n']:4d} {cert['mean_hit']*100:5.1f}% "
              f"{cert['mean_conf']*100:5.1f}% {cert['cal_err']*100:3.1f} "
              f"{cert['sep_h1']*100:+5.1f}/{cert['sep_h2']*100:+5.1f} "
              f"{('YES' if cert['actionable'] else 'no'):>5} | "
              f"{gd['hit_conf']*100:7.1f}% {gd['hit_flag']*100:7.1f}% {gap*100:+5.1f} "
              f"{gd['below_uniform_share']*100:4.0f}% -> {action}")
    print("\nRead: where SEPARATION certifies on BOTH halves, the confidence score is a real")
    print("skill-vs-coin-flip gate -> name a single bucket only on CONFIDENT days; WIDEN /")
    print("size-down on the FLAGGED (least-confident) third. The CONF-vs-FLAG hit gap is the")
    print("realized value of the gate. Where it does NOT certify, the per-day residual window")
    print("(esp. behind HK's 38-day lag) cannot recover the dispersion regime -> fall back to")
    print("the regime Kelly cut. Recommend-only: this never moves a served verdict.")


if __name__ == "__main__":
    rc = _selftest()
    if rc == 0:
        _live()
    sys.exit(rc)
