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

## INTERIM — first scoring 2026-07-13: ACCRUING (n=2; no verdict permitted)

07-08 18:16 PDT: NO-ASK (mirage). 07-09 15:15 PDT: bought the 66–67°F bucket @ 0.83,
settled 67°F — **+20.5%/unit** on $160 recorded liquidity. (Cosmetic: the probe's print
labels the °F bucket value with "°C"; values are °F per the frozen grain rules — display
only, numbers correct, script not edited post-scoring.)

**THE STUDY'S CENTRAL FINDING, now three-books strong:** the three fattest fills across
all five markets — Jeddah 0.78, Karachi 0.87, SF 0.83 — are ONE afternoon (2026-07-09,
the phantom-register day, the single day manual afternoon runs were made across cities).
Two readings, honestly held open: (a) that afternoon's register chaos left stale books
everywhere at once; (b) SAMPLING ARTIFACT — on-demand cities only have afternoon
snapshots when someone ran them, and 07-09 is the only multi-city afternoon session, so
"fat fills on 07-09" may just be "we only looked on 07-09." Singapore's automated
4×/day sampling — the only clean sampler — shows ~2¢ gaps. Reading (b) is load-bearing
for the pooled 6/6 win record: it is dominated by one session's condition. Resolution is
mechanical: the tape/automation now samples afternoons daily; the accrued ledgers will
separate (a) from (b) without any design change.
