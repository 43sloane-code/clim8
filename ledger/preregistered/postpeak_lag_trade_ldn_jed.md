# Pre-registration — POST-PEAK SETTLEMENT-LAG TRADE, London + Jeddah (frozen before scoring)

*2026-07-13. Extends the Singapore registration (`postpeak_lag_trade.md`, first scoring
ACCRUING n=9) to the other two active markets. The Singapore prereg's design and script
stay frozen and untouched; this file registers per-city parameters for a SEPARATE
city-parameterized probe (`reports/backtest_postpeak_lag_v2.py`). One attempt per city
on the recorded history; recommend-only; no served number, no live orders.*

## Shared design (identical to the Singapore registration)
Lead-0 settled `market_snapshots`, targets ≤ 2026-07-12. Leak-free state at issue from
IEM METAR hourly obs (obs hh ≤ local issue hour only; the static archive extended over
its end-gap by a live IEM fetch of the SAME fixed past window — a stable historical
feed, documented here before scoring). Entry = first snapshot per day with
day_state == declining (the shipped 2-consec rule) at/after the city's entry hour.
Buy the running-max whole-°C bucket at its RECORDED best_ask (0 < ask ≤ 0.97); no ask →
UNTRADEABLE, counted. Win (1−ask)/ask, lose −1. One trade/day. Same six frozen criteria:
n ≥ 20 else ACCRUING; pooled mean > 0; both halves > 0; hit > mean ask; untradeable
< 50%; median recorded liquidity ≥ $50. FAIL at n ≥ 20 → dead ledger (D21 London /
D22 Jeddah). PASS → forward paper ledger first, never capital from the backtest.

## Per-city parameters (frozen)
- **London (EGLC):** entry hour ≥ 16:00 local — its certified ≈.93 crossover hour
  (pinned baseline 16:00 = .9328), matching Singapore's anchoring (its ≥15:00 was its
  .934 hour). Driver-gap reference: .9328 for all entries ≥16:00 (conservative — the
  pinned table ends at 16:00). Settle: pm_resolved_label, realized_high fallback.
- **Jeddah (OEJN):** entry hour ≥ 15:00 local (peak tail 15–16h; NO certified crossover
  exists for OEJN — the driver-gap column is N/A and is a REGISTERED LIMITATION: without
  a certified reference the kill-watch series cannot be quoted; if this city ever passes,
  certification of its clock precedes any promotion). Settle: same rule.

## Priors (stated before scoring)
The Singapore interim points at seller-exit dominating (6/9 untradeable, ~2¢ gap when
tradeable, $345–533 capacity). London's book is deeper (its 07-12 market showed
$27,769 resting liquidity) so the untradeable rate may be lower — but a deeper book also
means faster repricing, so the gap may be thinner still. Jeddah is expected trivially
ACCRUING (3 qualifying snapshots exist). Expected outcomes: London ACCRUING-or-FAIL,
Jeddah ACCRUING.

## INTERIM — first scoring 2026-07-13: BOTH ACCRUING (no verdict permitted)

**London: 2 decision days** (< 20). Both untradeable (NO-ASK at 21:20/23:30 local; the
seller-exit mirage again). The dominant fact is upstream of the market: **14 of 16
post-16:00 snapshots read HOLDING** — EGLC's whole-°C evening plateau keeps the 2-consec
declining rule un-triggered within its 0.3°C tolerance, so the predicate itself rarely
fires at the 20:00 snapshot hour. A design consequence, recorded, NOT re-tuned (loosening
the state rule post-hoc would be exactly the forbidden move).
**Jeddah: 1 decision day — filled and won big:** 2026-07-09 15:01 local, bought 38°C
@ 0.78, settled 38, +28.2%/unit on $282 recorded liquidity. The fattest gap in the
three-city study (Singapore's fills were ~2¢; this was ~22¢) — and it occurred on the
phantom-register day (07-09), when the served figure confusion may have been exactly what
mispriced the book. n=1: suggestive, not evidence.
**Three-city picture so far (12 decision days):** 8/12 untradeable — the mirage confound
dominates everywhere; 4/4 fills won (asks .96/.95/.91/.78); capacity $282–533. Designs
stay frozen; all three re-score as snapshot days accrue.
