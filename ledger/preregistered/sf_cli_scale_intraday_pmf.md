# Pre-registration — SF CLI-scale shift of the intraday sharpened pmf (ONE probe)

*2026-07-27, frozen BEFORE any scoring. Template: twc_member_gate.md (driver-first).
Context: the 2026-07-27 KSFO miss — obs-scale modal 69°F served at 78% while the
settling CLISFO printed 70 via the 18-00Z 6-hourly catch (`10211` = 21.1°C = 69.98°F;
the hourly T-groups topped at 20.6°C = 69.1°F). The labeling half of the fix
(seam context + top-of-bucket UNRESOLVED warning in the ceiling block, KAT
tests/test_sf_cli_seam_guard.py) shipped gate-free. THIS file registers the only
served-number candidate: shifting the SF intraday sharpened final-max pmf by the
measured CLI−obs seam before bucketing. One attempt; fail → dead ledger.*

## DRIVER (why an edge should exist at all)

The Kalshi KXHIGHTSFO oracle is the NWS CLI daily maximum, computed from the
sensor's CONTINUOUS record. The sharpened pmf is conditioned on the hourly-obs
record (WU/IEM), which is blind to between-obs minutes. The 6-hourly METAR max
groups (`1xxxx`) capture those minutes, so the CLI prints AT OR ABOVE the
hourly-obs max — an asymmetric, mechanical, one-directional divergence.
Specimens: 07-15 obs 73 → CLI 74; 07-23 obs 75 → 1-group 76 ("the bucket that
pays"); 07-27 obs 69.1 → 6h-group 69.98 → CLI 70. Logged series:
ledger/ksfo_cli_wu.jsonl (CLI − WU mean +1.27°F, n=15, growing daily via the
kalshi_logger duty). Hierarchy check: identity (same station KSFO) > driver
(6-hourly catch mechanism, NWS-documented) > pattern (the +1.27°F mean).

## KILL CONDITION (on the driver itself, not on losses)

The driver dies when the catch stops happening: kill if, on the forward
ledger/ksfo_cli_wu.jsonl series, the CLI−WU mean divergence reaches ≤ 0 over
any rolling 30-day window, OR the 10y IEM CLI-vs-obs archive probe shows the
catch rate (CLI max > hourly-obs max) is not sign-stable across BOTH
chronological halves. A dead driver kills the candidate regardless of score.

## REGIME

KSFO (CLI-primary, Kalshi-settled), INTRADAY lead-0 reads only, post-10:00-local
states where the sharpened pmf is served. NOT day-ahead (D19: the °C headline
stands at day-ahead σ — do not relitigate; this candidate is the intraday lever,
which D19 explicitly does not touch).

## PROBE (cheapest decisive test — historical, leak-free, no forward clock)

Data: 10y IEM KSFO archive — hourly obs (data/ksfo_hourly_iem.jsonl, already
local) for the obs-scale record; IEM parsed-CLI (sources.nws_cli_daily,
allowlisted host, probe-verified per kalshi_sf_seam.md S2 rule ≥30 days before
adoption) for the CLI-scale truth. Walk-forward strictly chronological: for each
day D and each served hour H ∈ {10..16}, rebuild the remaining-rise sharpened pmf
using ONLY days < D (the shipped intraday_ceiling machinery), then:

- arm A (served): bucket the pmf at obs scale (status quo);
- arm B (candidate): shift the running max / pmf by the seam estimator learned
  ONLY from days < D (expanding-window mean CLI − obs-max divergence), then bucket.

Score both arms against the ACTUAL CLI settle on the 2°F Kalshi market buckets
(floor/cap inclusive, T-tails per kalshi_sf_seam.md).

## GATE (all required; fail any → dead ledger, one attempt)

- C1: market-bucket hit rate, sign-stable improvement on BOTH chronological halves;
- C2: log score on the 2°F market-bucket distribution, BOTH halves;
- C3: no degradation of the banked-floor semantics (the ratchet stays obs-grade;
  the shift applies to the pmf, never to the floor);
- C4: driver alive at probe time (kill condition above not triggered).

Ship only with a KAT (shift applied at serve time, frozen artifact rules
respected) and this file stamped CERTIFIED with the probe numbers. If the gate
fails: dead-ledger entry citing this file, and the labeling guard shipped
2026-07-27 remains the standing mitigation.

## ADDENDUM (2026-07-27, still PRE-SCORING — no probe number has been computed)

Scope extension, same driver, same gate: the probe's CLI-scale truth assembly
(10y IEM parsed-CLI vs the hourly-obs archive) must ALSO emit the CLI-scale
catch-rate series for tools/finegrain_read.py `pattern_rate`. That instrument's
archive is hourly-obs rows only, so its "catch rate" is the obs-climb rate —
a floor for the paying CLI-catch rate, mislabeled until the 2026-07-27 honesty
relabel (tests/test_finegrain_read.py TestPatternLabeling pins the honest
label). 07-27: it served 0% from 17:00 against the 70-71 bucket the CLI then
paid via `10211`. The probe replaces the obs-scale archive truth with the
catch-inclusive CLI-scale truth behind the SAME leak-free conditioning; the
relabel stays until that series lands and clears the same sign-stability bar.

## OUTCOME — SCORED 2026-07-28, **FAILED THE GATE (DEAD, D29)**

S2 precondition ADOPT (44/44 exact, IEM parsed-CLI vs raw CLISFO text). Probe:
tools/probe_sf_cli_scale.py, 25,081 leak-free day×hour cells (2016-07-08..
2026-07-05, 16 truth-artifact days quarantined), report
reports/probe_sf_cli_scale_2026-07-27.json.
- C1 bucket-hit: PASS both halves (+0.0767 / +0.0788).
- C2 log score: **FAIL both halves (−0.5047 / −0.5113)** — the full shift moves
  mass OFF the obs-scale bucket that still settles ~35% of days; the proper
  score punishes the misses harder than the hits pay.
- C3 floor semantics PASS; C4 driver ALIVE (+0.858°F, halves +0.896/+0.820).
One attempt spent → dead ledger D29. A partial-shift/mixture variant is a NEW
candidate needing its own prereg. The 2026-07-27 labeling guard remains the
standing mitigation. The CLI-scale catch-rate series for pattern_rate (addendum
above) survives as a measurement deliverable of the MC tool, not a served number.
