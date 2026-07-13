# Pre-registration — POST-PEAK SETTLEMENT-LAG TRADE, San Francisco (frozen before scoring)

*2026-07-13. Fifth market under the shared design (see postpeak_lag_trade.md lineage; all
prior registrations, scripts, and stamps untouched). SF is the one market where the shared
°C design CANNOT be copied blind — it settles WHOLE °F in 2°F contract buckets — so the
grain rules below are frozen explicitly. Implementation: an ADDITIVE grain-aware branch +
config entry in `reports/backtest_postpeak_lag_v2.py`; the °C cities' code path is the
unmodified default and their printed numbers remain re-verifiable. One attempt on the
recorded history; recommend-only.*

## Per-city parameters (frozen)
- **San Francisco (KSFO), grain F:** running-max bucket = the 2°F market bucket containing
  `rhu(°F of the °C running max)`; bucket bounds are INCLUSIVE on hi (label "72-73°F" =
  [72, 73]; open-ended "71°F or below"/"82°F or higher" use the one defined bound).
  Win iff the settled whole-°F value (`rhu(realized_high °C × 9/5 + 32)`; pm label used
  only when it parses as a range containing it) lies inside the ENTRY bucket's bounds.
- Entry hour ≥ 15:00 PDT (peak ~14:00; CLAUDE.md's certified clock quotes declining@15:00
  ≈ 96% — cited as CONTEXT only: no PINNED crossover artifact exists for KSFO, so the
  driver-gap column is N/A, the same registered limitation as Jeddah/Karachi; a pass would
  require pinning the clock before any promotion).
- Archive `data/ksfo_hourly_iem.jsonl` (ends 07-05), extended live over the fixed past gap
  per the shared design. IEM KSFO obs are °C floats of whole-°F natives (D19 grain sanity:
  99.5% integral) — the °F conversion is exact to the settlement grain.
- Everything else identical to the shared design: first qualifying snapshot per day,
  2-consec declining rule, recorded best_ask only (0 < ask ≤ 0.97), untradeable counted,
  win (1−ask)/ask / lose −1, six criteria, n ≥ 20 floor. Dead-id on FAIL at n ≥ 20: **D24**.

## Priors (stated before scoring)
On-demand city; inventory shows AT MOST TWO qualifying decision days (07-08 18:16 PDT,
07-09 15:15 PDT — 07-09 again). Expected outcome: trivially ACCRUING. The marine-layer
regime makes SF the coin-flip-richest book (2°F buckets on a σ≈4°F day-ahead cloud), so
if the mirage confound spares any market it may be this one — but that is a hypothesis
for the accrued ledger, not tonight.
