# Pre-registration — POST-PEAK SETTLEMENT-LAG TRADE, Karachi (frozen before scoring)

*2026-07-13. Fourth market under the shared design (see `postpeak_lag_trade.md` /
`postpeak_lag_trade_ldn_jed.md` — those registrations, scripts, and interim stamps stay
untouched). Implementation is an ADDITIVE city entry in `reports/backtest_postpeak_lag_v2.py`'s
config map — the London/Jeddah code paths are unmodified and their printed numbers remain
re-verifiable. One attempt on the recorded history; recommend-only.*

## Per-city parameters (frozen)
- **Karachi (OPKC):** entry hour ≥ 15:00 PKT — the 07-12 miss proved the peak tail runs
  mid/late afternoon past the ~13:00 climatological center, so the same on-demand-city
  anchoring as Jeddah applies. Archive `data/opkc_hourly_iem.jsonl` (ends 07-08), extended
  live over the fixed past gap per the shared design. NO certified crossover exists for
  OPKC → the driver-gap column is N/A (REGISTERED LIMITATION, same as Jeddah: if this
  city ever passes, clock certification precedes any promotion). Settle:
  pm_resolved_label, realized_high fallback (whole-°C round-half-up). Dead-id on a
  FAIL at n ≥ 20: **D23**.

## Priors (stated before scoring)
Karachi has been run manually only; the inventory shows AT MOST ONE qualifying decision
day (2026-07-09 — which is also the phantom-register day that produced Jeddah's fat
fill). Expected outcome: trivially ACCRUING (n ≤ 1). The value of registering anyway:
the design is frozen BEFORE Karachi's snapshot density grows (it is now tape-adjacent
and manually run daily), so future scorings inherit clean criteria instead of a
post-hoc design.

## INTERIM — first scoring 2026-07-13: ACCRUING (n=1; no verdict permitted)

One decision day: 2026-07-09 (the 15:14 PKT snapshot read HOLDING and was skipped by the
frozen rule; the 16:00 snapshot read declining) — bought 34°C @ 0.87, settled 34,
**+14.9%/unit** on $189 recorded liquidity. **Cross-city honesty flag:** this fill and
Jeddah's 0.78 fill are the SAME afternoon (07-09, the phantom-register day, when manual
afternoon runs happened to be made) — the study's two fattest gaps are one correlated
session, not two independent observations. Four-market pooled picture: 13 decision days,
8 untradeable (the mirage confound dominates), 5/5 fills won (asks .96/.95/.91/.87/.78),
capacity $189–533 — but effective independent fill-days = 4, not 5. Design frozen;
re-scores as Karachi's snapshot density grows (now run daily).
