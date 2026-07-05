# Pre-registration — served intraday-lock confidence must be STATE- and SEASON-conditional

*2026-07-05. Motivated by a live miss the USER caught twice ("92% on 32 proved obsolete";
"96% on 28" while holding). The served lock percentile is the BLENDED backtest rate; it does
not condition on the holding/declining state it already computes, nor on season.*

## Measured defect (10y EGLC, this is the evidence, not the fix)
Exact-bucket settle rate SPLIT BY STATE:
- July holding@16:00 settles the current bucket 62.9% (climbs 37.1%), n=194
- July declining@16:00 settles 95.6%, n=113
- July holding@17:00 84.4% vs declining@17:00 99.3%
- All-year holding@16:00 80.5% vs declining@16:00 97.0%
The lock served **96%** on a July **holding** day whose true rate is **~63%** — it quoted the
declining-grade / blended number. The lever's stated "raise-risk 8%" also understates the
measured holding climb (July 37%, all-year 19.5%).

## Hypothesis
Serving the STATE×SEASON-conditional empirical exact-bucket rate (holding/declining ×
month-bin) is better-calibrated than the blended marginal, out-of-sample, without
manufacturing confidence.

## Frozen gate (calibration, not edge — but it changes a SERVED number)
- Walk-forward, warmup 400d. For each (state, season-bin, hour) cell with n>=30 prior days,
  the served confidence = that cell's leak-free empirical settle rate; else fall back to the
  current blended rate (recorded).
- CERTIFY iff, on BOTH chronological halves: (a) reliability improves — |served% − realized%|
  drops vs the blended baseline, AND (b) no bucket-hit regression (the MODAL bucket is
  unchanged; this recalibrates the CONFIDENCE, never the pick). Fail → keep the blend, record
  as D18.
- Labeling-first interim (NO gate needed, ships now as HONESTY not edge): the served text must
  STOP quoting 90%+ while the day is HOLDING — cap the displayed confidence at the holding-state
  empirical rate and say "peak not formed; high confidence is earned only once DECLINING."

## Status
INSTRUMENTED-PENDING. The interim labeling cap is the immediate honest fix; the full
state×season table is the gated change. Do NOT hand-tune per-day. Related: the shipped state
split (holding/declining), verify_skill.py, the certification ledger.
