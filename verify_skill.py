#!/usr/bin/env python3
"""
verify_skill.py -- weather-verdict skill verification, corrected methodology.

Fixes the three defects flagged in review of FINDINGS.md:
  DEFECT 1: LOO climatology is degenerate at small n (Manila n=2 -> Brier 2.0).
            FIX: reference forecast = season base rate (stable, sample-independent),
            per Murphy (1973) skill-score framework.
  DEFECT 2: Skill sign claimed "earned" from point estimates only.
            FIX: paired block bootstrap, blocked BY DATE (city-days on the same
            date share synoptic forcing -> not independent). Sign is EARNED iff
            the 95% CI on (climo_score - model_score) excludes zero.
  DEFECT 3: Multi-category Brier scores a 2-bucket miss == a 5-bucket miss.
            FIX: RPS (ranked probability score) as primary for ordinal buckets;
            Brier retained for continuity with the existing log.

Also computes: empirical band coverage vs stated conviction (the missing number
that adjudicates Mistake 2 / defines the Manila probe's gate).

Charter-compliant: stdlib only, deterministic (fixed seed), reproducible.

USAGE
  python3 verify_skill.py --records records.jsonl --climo season_base_rates.json \
      [--lead day_ahead] [--boot 10000] [--seed 43]

INPUT SCHEMAS
  records.jsonl -- one JSON object per settled city-day:
    {"date": "2026-07-02", "city": "manila", "lead": "day_ahead"|"post_peak"|...,
     "probs": {"31": 0.10, "32": 0.45, "33": 0.30, "34": 0.10, "35": 0.05},
     "settled": 35,
     "band_lo": 31, "band_hi": 33, "conviction": 0.80}   # band fields optional
  season_base_rates.json -- proper climatology reference, per city:
    {"manila": {"31": 0.05, "32": 0.25, "33": 0.20, "34": 0.15, "35": 0.17,
                "36": 0.16, "37": 0.02}, "singapore": {...}, ...}

OUTPUT
  Markdown report to stdout: per-city + pooled Brier, RPS, BSS, RPSS
  (vs season base rate), bootstrap 95% CI on the paired score difference,
  sign verdict (EARNED / NOT EARNED), and coverage-vs-conviction table.

Exit codes: 0 ok, 2 input error. Never fabricates a number; missing data
per city is reported as missing, not imputed.
"""

import argparse
import json
import math
import random
import sys
from collections import defaultdict


# ----------------------------------------------------------------------------
# Scoring primitives
# ----------------------------------------------------------------------------

def _normalize_probs(probs):
    """Keys -> int buckets, values renormalized to sum 1 (guards drift)."""
    p = {int(k): float(v) for k, v in probs.items()}
    s = sum(p.values())
    if s <= 0:
        raise ValueError("probability vector sums to <= 0")
    return {k: v / s for k, v in p.items()}


def _category_axis(probs, settled, climo):
    """Ordered integer bucket axis spanning forecast, obs, and climatology."""
    keys = set(probs) | set(climo) | {settled}
    lo, hi = min(keys), max(keys)
    return list(range(lo, hi + 1))


def brier_mc(probs, settled, axis):
    """Multi-category Brier, range [0, 2]. sum_k (p_k - o_k)^2."""
    return sum((probs.get(k, 0.0) - (1.0 if k == settled else 0.0)) ** 2
               for k in axis)


def rps(probs, settled, axis):
    """Ranked probability score over ordinal buckets. Distance-aware:
    RPS = sum over thresholds of (CDF_forecast - CDF_obs)^2.
    Range [0, K-1]. Lower is better."""
    cdf_f, score = 0.0, 0.0
    for k in axis[:-1]:                      # last threshold contributes 0
        cdf_f += probs.get(k, 0.0)
        score += (cdf_f - (1.0 if settled <= k else 0.0)) ** 2
    return score


def score_record(rec, climo_city):
    """Return (brier_model, brier_climo, rps_model, rps_climo) for one record."""
    probs = _normalize_probs(rec["probs"])
    climo = _normalize_probs(climo_city)
    settled = int(rec["settled"])
    axis = _category_axis(probs, settled, climo)
    return (brier_mc(probs, settled, axis),
            brier_mc(climo, settled, axis),
            rps(probs, settled, axis),
            rps(climo, settled, axis))


# ----------------------------------------------------------------------------
# Skill + inference
# ----------------------------------------------------------------------------

def skill_score(model_mean, climo_mean):
    """Murphy skill score: 1 - S_model/S_ref. Perfect score is 0 for both metrics."""
    if climo_mean == 0:
        return float("nan")
    return 1.0 - (model_mean / climo_mean)


def block_bootstrap_ci(scored, metric_idx_model, metric_idx_climo,
                       n_boot, seed, alpha=0.05):
    """Paired bootstrap on mean(climo - model), resampling DATES (blocks).

    scored: list of (date, city, tuple_of_4_scores)
    Returns (point_diff, ci_lo, ci_hi, n_dates). Positive diff = model better.
    """
    by_date = defaultdict(list)
    for date, _city, s in scored:
        by_date[date].append(s)
    dates = sorted(by_date)
    if len(dates) < 2:
        return (float("nan"), float("nan"), float("nan"), len(dates))

    def mean_diff(sample_dates):
        num, den = 0.0, 0
        for d in sample_dates:
            for s in by_date[d]:
                num += s[metric_idx_climo] - s[metric_idx_model]
                den += 1
        return num / den if den else float("nan")

    point = mean_diff(dates)
    rng = random.Random(seed)
    diffs = []
    for _ in range(n_boot):
        resample = [dates[rng.randrange(len(dates))] for _ in dates]
        diffs.append(mean_diff(resample))
    diffs.sort()
    lo = diffs[int((alpha / 2) * n_boot)]
    hi = diffs[min(int((1 - alpha / 2) * n_boot), n_boot - 1)]
    return (point, lo, hi, len(dates))


# ----------------------------------------------------------------------------
# Coverage vs conviction (the Mistake-2 adjudicator)
# ----------------------------------------------------------------------------

def coverage_table(records):
    """Empirical band coverage grouped by stated conviction and by city."""
    by_conv = defaultdict(lambda: [0, 0])   # conviction -> [hits, total]
    by_city = defaultdict(lambda: [0, 0])
    for r in records:
        if r.get("band_lo") is None or r.get("band_hi") is None:
            continue
        hit = int(r["band_lo"]) <= int(r["settled"]) <= int(r["band_hi"])
        conv = round(float(r.get("conviction", float("nan"))), 2)
        by_conv[conv][0] += hit
        by_conv[conv][1] += 1
        by_city[r["city"]][0] += hit
        by_city[r["city"]][1] += 1
    return by_conv, by_city


# ----------------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------------

def run(records, climo, n_boot, seed, lead_filter):
    if lead_filter:
        records = [r for r in records if r.get("lead") == lead_filter]
    if not records:
        print("No records after filtering; nothing to verify.", file=sys.stderr)
        return 2

    scored = []          # (date, city, (bm, bc, rm, rc))
    skipped = []
    for r in records:
        city = r["city"]
        if city not in climo:
            skipped.append((r.get("date"), city, "no season base rate"))
            continue
        try:
            scored.append((r["date"], city, score_record(r, climo[city])))
        except (KeyError, ValueError) as e:
            skipped.append((r.get("date"), city, str(e)))

    if not scored:
        print("All records skipped -- check climatology file keys.", file=sys.stderr)
        return 2

    cut = lead_filter or "all leads"
    print(f"# Skill verification -- reference = SEASON BASE RATE (not LOO)")
    print(f"Cut: **{cut}** | records scored: **{len(scored)}** | "
          f"skipped: {len(skipped)} | bootstrap: {n_boot} resamples, seed {seed}\n")

    # per-city + pooled table
    groups = defaultdict(list)
    for date, city, s in scored:
        groups[city].append(s)
    groups["POOLED"] = [s for _, _, s in scored]

    print("| scope | n | Brier model | Brier climo | BSS | RPS model | RPS climo | RPSS |")
    print("|---|---|---|---|---|---|---|---|")
    for scope in sorted(groups, key=lambda k: (k == "POOLED", k)):
        ss = groups[scope]
        n = len(ss)
        bm = sum(s[0] for s in ss) / n
        bc = sum(s[1] for s in ss) / n
        rm = sum(s[2] for s in ss) / n
        rc = sum(s[3] for s in ss) / n
        print(f"| {scope} | {n} | {bm:.3f} | {bc:.3f} | {skill_score(bm, bc):+.3f} "
              f"| {rm:.3f} | {rc:.3f} | {skill_score(rm, rc):+.3f} |")

    # inference: is the sign earned?
    print("\n## Sign test -- paired block bootstrap (blocked by DATE)\n")
    print("| metric | mean(climo - model) | 95% CI | date-blocks | verdict |")
    print("|---|---|---|---|---|")
    for name, mi, ci_ in (("Brier", 0, 1), ("RPS", 2, 3)):
        pt, lo, hi, nd = block_bootstrap_ci(scored, mi, ci_, n_boot, seed)
        if math.isnan(pt):
            verdict = "INSUFFICIENT DATES"
        elif lo > 0:
            verdict = "SIGN EARNED (model beats climatology)"
        elif hi < 0:
            verdict = "SIGN EARNED, NEGATIVE (climatology beats model)"
        else:
            verdict = "NOT EARNED -- CI includes zero; label MEASURED-PENDING"
        print(f"| {name} | {pt:+.4f} | [{lo:+.4f}, {hi:+.4f}] | {nd} | {verdict} |")

    # coverage
    by_conv, by_city = coverage_table(records)
    print("\n## Band coverage vs stated conviction (Mistake-2 adjudicator)\n")
    if not by_conv:
        print("No band data in records -- log band_lo/band_hi/conviction going forward.")
    else:
        print("| stated conviction | n | empirical coverage | gap |")
        print("|---|---|---|---|")
        for conv in sorted(by_conv):
            h, t = by_conv[conv]
            emp = h / t
            gap = emp - conv if not math.isnan(conv) else float("nan")
            print(f"| {conv:.0%} | {t} | {emp:.0%} | {gap:+.0%} |")
        print("\n| city | n | empirical coverage |")
        print("|---|---|---|")
        for city in sorted(by_city):
            h, t = by_city[city]
            print(f"| {city} | {t} | {h/t:.0%} |")

    if skipped:
        print("\n## Skipped records (never imputed)\n")
        for d, c, why in skipped:
            print(f"- {d} {c}: {why}")

    print("\n*Interpretation rules: BSS/RPSS magnitudes are PROVISIONAL until the "
          "sign test reads EARNED and n_dates >= 40. A stated-conviction gap "
          "beyond +/-10pp at n >= 20 is systemic mis-calibration, not noise.*")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--records", required=True, help="JSONL of settled city-days")
    ap.add_argument("--climo", required=True, help="JSON of per-city season base rates")
    ap.add_argument("--lead", default=None, help="filter to one lead class, e.g. day_ahead")
    ap.add_argument("--boot", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=43)
    args = ap.parse_args()

    try:
        with open(args.records) as f:
            records = [json.loads(line) for line in f if line.strip()]
        with open(args.climo) as f:
            climo = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Input error: {e}", file=sys.stderr)
        return 2

    return run(records, climo, args.boot, args.seed, args.lead)


if __name__ == "__main__":
    sys.exit(main())
