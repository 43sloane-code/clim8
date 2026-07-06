# Pre-registration — redefine the lock "declining" trigger as PERSISTENT (2 consecutive reads)

*2026-07-05. From the historical curve-pattern analysis both cities (10y IEM = the WU graph
feed). The shipped `declining` state fires on a SINGLE read below runmax−0.3; the 07-04 London
miss and the pattern scan show that single-read decline is a FALSE peak-passed signal 1-in-3
to 1-in-5 July days.*

## Measured pattern (evidence, not the fix)
False-decline trap = after the first post-noon decline, a later reading still lifts the bucket:
- London EGLC: 1st-decline 29.6% July / 15.7% all-year → 2-consecutive 11.1% / 6.8%
- Singapore WSSS: 1st-decline 19.4% July / 17.5% all-year → 2-consecutive 7.4% / 7.6%
Drivers: London late-spike (25% July set a new bucket after 16:00); Singapore convective
double-peak (9-10% of days — midday dip then afternoon re-peak).

## Hypothesis
Defining "peak passed / lock-grade declining" as TWO consecutive readings below the banked
floor (≈1h sustained) raises the exact-bucket reliability of the lock trigger without materially
delaying it, out-of-sample, both cities.

## Frozen gate (calibration/behavior change — needs the gate)
- Walk-forward, warmup 400d. Compare single-read vs 2-consecutive declining trigger.
- CERTIFY iff, on BOTH chronological halves AND both cities: (a) exact-bucket reliability at the
  trigger improves (fewer post-lock bucket rises), AND (b) median lock delay ≤ +45 min (it must
  not push the lock so late it loses the intraday advantage). Fail either → keep single-read,
  record as D18.
- The banked FLOOR is unchanged (mechanical ratchet). This changes only WHEN the confidence
  upgrades to lock-grade. Compose with the shipped holding-cap + the gated state×season table
  (`lock_state_season_calibration.md`) — same served surface, so they gate together, not piecemeal.

## Status
PRE-REGISTERED, pending the frozen-A/B run. Do NOT hand-patch the declining threshold live.
Related: the 07-04 curve decipher, [[feedback_lock_overconfident_while_holding]].

## VERDICT — CERTIFIED 2026-07-06 (shipped)
Probe per the frozen design (10y, both cities, chronological halves):
reliability single-read -> 2-consecutive: EGLC 80.7->91.6 (h1) / 78.2->89.8 (h2);
WSSS 75.7->88.3 / 75.9->89.5 — ALL FOUR cells PASS; median delay +30min (<=45 bound) PASS.
Shipped into `intraday_ceiling._day_state` (last TWO reads below floor = declining), KAT
pins the one-dip trap as HOLDING. First served-behavior candidate to clear a full gate.
