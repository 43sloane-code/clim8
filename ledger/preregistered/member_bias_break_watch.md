# REGISTERED — per-member bias-BREAK watch (G1 of the 2026-07-12 driver audit)

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
