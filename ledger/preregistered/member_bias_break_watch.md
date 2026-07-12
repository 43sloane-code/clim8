# REGISTERED — per-member bias-BREAK watch (G1 of the 2026-07-12 driver audit)

## EXECUTED 2026-07-12 (same day; gate green; alert-only, no served number)

- `weather_council/member_break.py` — pure core: raw_high−actual_high per (city, member)
  from settled provenance votes (the RAW bias is the driver series a pipeline upgrade
  breaks; the correction consumes it downstream); FIRST 20 settled errors pinned as the
  frozen reference (`reports/member_bias_ref.json`; assess_all NEVER moves a written pin —
  re-pin is a human, documented breakpoint); BREAK = rolling-10 mean outside the
  reference's seeded bootstrap 99% CI of 10-means — the same break test as the TWC
  driver-health monitor, one convention both places.
- KATs (tests/test_member_break.py) pin the three registered behaviors: +2σ step BREAK
  detected; same-regime silent; recency-class seasonal drift (0.03σ/day, a month) does
  NOT false-alarm (test vs the CI, not vs zero) — plus pin immutability, ACCRUING floors,
  and the empty-join case.
- Wired into accumulate (`member-bias break watch` ledger step), runs daily.
- **Honest live status at shipping:** 0 cells — the settled∧provenance join is EMPTY
  (provenance logging began 2026-07-11; none settled yet). The watch arms itself as the
  join fills: first pins ≈3 weeks out for daily cities, first meaningful OK/BREAK reads
  ≈4+ weeks. Per the consistency law, this ships as instrumentation, not as a claim.

*Registered for proper execution with KATs, not hot-patched. Recommend-only
instrumentation: an ALERT, never a correction (a break watch that silently re-biased
members would be a served-number change wearing a monitor's name).*

## The gap

The council's imported edge (NWP skill) dies locally through MEMBER PIPELINE CHANGES:
a provider ships a new model cycle and that member's station bias regime resets. The
existing watch — watchdog Duty 3b — pins ECMWF@Changi bias against a fixed reference
and REDs on drift, which is exactly the right shape, but covers 1 member × 1 city.
The other members × basket cities break silently until the daily PIT/coverage check
catches the DOWNSTREAM distributional effect — after served days, not before.

## The fix (when executed)

Per (member, city): rolling k-day mean of (member forecast − settled truth) compared
against that member's pinned reference window; ALERT when the rolling mean exits the
reference bootstrap CI (the same break test as the TWC driver-health monitor —
`twc_member_gate.md` — one convention, both places). Output: a line in the watchdog
report + eval_harness, naming member/city/magnitude. Explicitly NOT: any change to
the served blend, bias correction, or pmf — the alert's only action is to route a
human to the fold-gated recalibration path that already exists.

## Requirements before shipping

- Pinned reference windows per (member, city) chosen from settled history and FROZEN
  (a re-pin is a documented breakpoint, Duty-2 style).
- KATs: synthetic break detected; no-break stays silent; a seasonal drift that
  recency_bias already models must NOT false-alarm (the break test is vs the CI, not
  vs zero).
- Wire into the existing watchdog/accumulate chain; no new hosts, no new feeds —
  member forecasts and settled truths are already logged.
