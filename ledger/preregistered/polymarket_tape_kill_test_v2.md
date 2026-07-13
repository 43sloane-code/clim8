# Pre-registration — Polymarket trade-tape KILL test v2 (frozen before scoring)

*2026-07-14. Successor to polymarket_tape_kill_test.md, whose frozen ABORT gate fired on
resolution infrastructure (69/89 slug misses; wrong-event °F Londons) with ZERO gap
numbers computed — no evidence carries over. Changes are RESOLUTION and UNIVERSE only;
the kill criteria are identical. One attempt; probe reports/backtest_polymarket_tape_v2.py;
fresh cache (v1 rows are wrong-event-suspect and are not reused).*

## Changes from v1 (all infrastructure)
- **Universe:** the five basket/focus cities ONLY (Singapore, London, Karachi, Jeddah,
  San Francisco) — the distinct settled market-days in our market_snapshots ledger
  (~54 days). Tokyo/Chicago/Hong Kong/Manila experiment days are excluded by name.
- **Verified resolution:** slugs tried as [base, base-2026]; an event is ACCEPTED only if
  (a) its endDate/closedTime falls within [target, target+3 days], AND (b) its bucket
  labels carry the city's settlement unit (°C for SG/London/Karachi/Jeddah; °F for SF).
  Anything else = resolution failure (counted; never scored).
- **n floor: 40** (recalibrated to the ~54-day corrected universe; v1's 60 assumed 89).

## Unchanged (verbatim from v1)
Winner = gamma outcomePrices Yes==1, cross-checked against our recorded settle (mismatch
→ flagged/excluded/counted — now meaningful, post-verification). Afternoon window per
city (15:00 local; London 16:00) → end of local day. VW yes-equivalent price; gap = 1−vw;
killable := gap ≤ 0.01. ABORT if resolution failures >20% or empty tapes >30%. KILL at
≥80% killable → D25 + hostile prior inherited by the ask-fill preregs. Survival =
hindsight-winner UPPER BOUND, permission to keep testing only.
