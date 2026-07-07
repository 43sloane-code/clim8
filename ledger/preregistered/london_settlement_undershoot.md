# REGISTERED DEFECT — London settlement UNDERSHOOTS the WU record (2026-07-07, user-caught)

*London 07-07 SETTLED 32°C (user, watching the market). Our system locked 31 and was WRONG by a
full bucket. This is a settlement-TRUTH defect — the most serious class.*

## What happened
- Our routine IEM METAR obs peaked at 31.0°C (whole-°C); `wunderground_daily_series('EGLC')`
  returned 31.1°C = 31. Both said 31.
- The WU v3 current-conditions/24h-register read 89-90°F (31.7-32.2°C) all afternoon → 32.
- The market / WU displayed high settled **32**. Our obs-derived feed undershot it by ~1-2°F,
  i.e. a whole bucket at the 31/32 edge.
- COMPOUNDING ERROR: I explicitly DISMISSED the WU current-conditions/register for London
  ("not London's settlement feed") — twice — and locked 31 off the whole-°C METAR. The register
  was tracking the true settlement; my dismissal was the miss.

## Diagnosis (two candidate root causes, both real)
1. WRONG SOURCE: London may settle on the WU DISPLAYED daily high (°F, register-inclusive =
   90°F→32), NOT the IEM-METAR-whole-°C record (31) we anchor on. The "London settles whole-°C
   via IEM, 1470/1470 integral" assumption describes the METAR grain, not necessarily the
   market's settlement source.
2. UNDERSHOOT: even within WU, our daily high = max(hourly obs) misses the true peak that a
   SPECI / the 24h-register catches (our 88°F hourly max vs the 90°F register). Reconstruct-from-
   hourly systematically undershoots by up to a bucket at boundaries.

## The honest cross-city lesson (NOT "always trust the register")
- London 07-07: register 32 was RIGHT, obs 31 WRONG.
- Singapore 07-07: register 33 was WRONG (noise), obs/daily 32 RIGHT.
So the register is neither blanket-trust nor blanket-dismiss. The ONLY reliable arbiter is the
AUTHORITATIVE WU displayed/settled daily high — which we must pull directly, not reconstruct
from hourly obs and not override with our own whole-°C METAR.

## Fix (gated, settlement-truth — highest priority)
Determine the EXACT source the London contract settles on (WU displayed high in °F vs IEM METAR
whole-°C), pull THAT as the truth, and validate the last ~30 settled days: does our served bucket
match the market's settled bucket? Any systematic undershoot at boundaries is a settlement-source
bug, not a lock-timing issue. Until resolved, London locks are provisional — flag the register
divergence loudly instead of dismissing it.

## Immediate correction
London 07-07 = 32°C (settled). Our 31 lock was wrong. Recorded as a served-miss, not argued away.

## FIXED — 2026-07-07 (same session, not deferred)
Root cause pinned: London was excluded from the live-register consult — `_WU_INTRADAY =
{"singapore"}` gated BOTH the hourly source AND the register consult, so EGLC never saw its own
WU v3 current (89°F=31.7°C) / register (90°F=32.2°C). It locked off the IEM whole-°C hourly (31)
alone. FIX: split the two concerns — new `_LIVE_REGISTER = {"singapore","london"}` gates the
register consult independently of the hourly source; London keeps its IEM hourly but now fuses
the WU live current/register (floor-raise-only). Re-run: London 07-07 now banks 32°C (runmax
32.2 via the fused 90°F) — the settled bucket. KAT: tests/test_live_floor.py::TestLondonRegisterConsult
pins both the config and the fusion recovering 32. Suite 420/420.
Residual (still open, honestly): the register can also OVER-read (Singapore 07-07 its 91°F was
noise, settled 32) — the register_overread_gate.md reconciliation vs the authoritative settled
high is the remaining gated piece. But the London UNDERSHOOT — the bucket miss that actually
happened — is fixed and tested now.
