# Pre-registration — P2b: 12:00-ONLY conditional lock, FORWARD data (frozen before any scoring)

*2026-07-04. The single legitimate descendant of D15, exactly as its dead-ledger entry carved
out: "a FUTURE pre-registration of a 12:00-ONLY hypothesis on NEW/unseen data would be a
legitimately different experiment... its own prereg with its own bar... never a post-hoc slice
of THIS probe's data." This is that prereg. Forward days only; nothing here may be evaluated
on the D15 probe window.*

## Hypothesis (narrowed from D15's evidence, tested on unseen data)
At 12:00 SGT — before the running max has absorbed the morning's information — conditioning
the remaining-rise resample on (state@12 × morning-cloud tercile) beats the unconditional
lever. D15 measured this hour at +5.9pt, fold-stable (+3.7/+8.1); it died only because 13:00
regressed and the pooled bar missed. The forward test asks: does the 12:00 effect survive on
days that did not exist when it was found, through the LIVE feed we would actually serve?

## Frozen design
- Instrument: `tools/p2b_1200_logger.py`, run daily by accumulate (point-in-time rows into
  `ledger/p2b_1200.jsonl`; idempotent per day).
- At capture: today's WSSS obs through 12:00 SGT (IEM feed, training grain) → runmax@12,
  state@12; predictor = mean cloudcover 08:00–12:00 SGT from the LIVE open-meteo forecast API
  (the deployable feed — deliberately NOT ERA5 reanalysis; D11's lesson is that reanalysis
  probes are optimistic upper bounds, so the forward test uses what serving would use).
- Tercile thresholds FROZEN as constants 84.3 / 97.7 (the D15 warmup block, 2016–17 data —
  fixed before any forward day exists).
- Both arms logged in full each day: unconditional bucket pmf (all prior IEM days' rises@12)
  and conditional pmf (same-cell prior days; cell min-n 30 else that day records `fallback`).
  Settlement: the day's final IEM °C bucket, stamped by the next run.
- NOTHING SERVED CHANGES. The served 12:00 output remains the unconditional lever. This
  ledger only accrues.

## Frozen gate (rules at n ≥ 60 settled non-fallback days; interim peeks are narration only)
1. Paired exact-hit: conditional beats unconditional with one-sided sign test on discordant
   days, p < 0.05 (paired McNemar — the honest small-n test);
2. Mean RPS delta < 0 (conditional sharper), and
3. Sign-stable: hit-delta ≥ 0 AND RPS-delta ≤ 0 on BOTH chronological halves of the forward
   window;
4. Fallback rate < 20% (else the cells are too thin to serve and the design fails on
   deployability regardless of skill);
5. Feed-climatology sanity: the frozen thresholds are ERA5-derived, the live predictor is the
   forecast API — if the live feed maps degenerately (any tercile taking < 10% of forward
   days), the ERA5→live mapping failed and the design FAILS on deployability (the D11
   reanalysis-vs-live lesson, made a criterion instead of a post-mortem).

## Outcomes (pre-committed)
- CLEARS → recommend-only: eligible for the frozen-A/B serving gate at 12:00 ONLY (13:00+
  stays unconditional per D15 — no scope creep).
- FAILS → **D16**, greps registered; the 12:00 lane closes permanently.
