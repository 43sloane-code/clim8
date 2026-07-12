# Pre-registration — TWC 9th-member blend gate (DRIVER-FIRST; frozen before n=40 completes)

*2026-07-12, at n=25/40 settled pairs — registered BEFORE the accrual clock fills so the
evaluation cannot be shaped by the data. Encodes the driver-first hypothesis law (operator
directive, 2026-07-12): every candidate must name its DRIVER, the testable chain, the
kill condition ON THE DRIVER, and the regime it lives in — a pattern with no driver is a
coincidence not yet disproven, and dies only after losses; a driver-based edge announces
its own death in the driver series before the pmf shows it.*

## The driver (why a TWC forecast should add information AT ALL)

*CORRECTED 2026-07-12 (operator-caught, before the clock fills; frozen criteria G1–G4
unchanged): the first draft called TWC "the settlement oracle forecasting its own
station" — WRONG. The oracle is the Wunderground OBSERVATION record: the airport's own
METAR/ASOS sensors, which TWC merely REDISTRIBUTES (whole-°F storage, between-obs
aggregation, the daily-max endpoint). TWC does not produce the observations and its
forecast has NO privileged relationship to settlement.*

The honest driver, at its true (weaker) strength: TWC's forecast is plausibly VERIFIED
AND CALIBRATED AGAINST THE SAME REDISTRIBUTED RECORD the market settles on — same
station, same whole-°F measurement convention, same aggregation quirks — so its errors
are measured in the settlement's own metric, whereas the 8 council members are grid
interpolations whose station bias our correction approximates. Shared verification
target, not insider knowledge. If TWC in fact just re-bases public NWP with no
station-level calibration loop, this driver DOES NOT EXIST — which is exactly what G3
(offset sign-stability) and G4 (independence) test, and why they carry more weight than
G1/G2 under the corrected driver. The 0/6 dead forecast members (D02 AIFS, UKMO-2km,
ICON-D2, ICON-EU, AROME) had no mechanism beyond "another model"; TWC's claim to a
mechanism is real but modest, and the gate treats it accordingly.

## The testable chain (what the driver predicts, each link falsifiable)

1. **Signed, stable, city-specific offset** vs the WU record (already instrumented:
   twc_offset.py three-gate). A driverless member's offset wanders around zero.
2. **Gain concentrated where station effects dominate**: TWC-added-value should be
   LARGEST on days the council diverges most from the settled WU record (station/
   microclimate days) and smallest on well-mixed synoptic days. A uniform, unconditional
   gain is more consistent with noise than with this driver.
3. **Independence**: the Phase-5 correlation audit must show TWC is not just the council
   re-warmed (same-company caveat recorded in twc_offset.md). If TWC's errors are ~fully
   spanned by the existing members, there is no new information, whatever the backtest says.

## Frozen pass criteria at n>=40 settled pairs (ALL required)

- **G1 (economic object):** council+TWC (Plan-3 blend machinery, no re-tuning) beats
  council-alone exact-bucket hit on BOTH disjoint chronological folds, per city pooled.
- **G2 (proper score):** same, on CRPS/log score — both folds.
- **G3 (driver evidence):** the per-city signed offset is sign-stable across both folds
  (chain link 1). An offset that flips sign between folds = no stable station effect =
  driver absent; REFUSE even if G1/G2 pass (a gain without its driver is a coincidence).
- **G4 (independence):** Phase-5 correlation audit passes (TWC error not spanned by the
  member panel, r-threshold per twc_offset.md conventions).

One attempt. Any failure → dead-ledger entry; TWC stays a display cross-reference only.

## Driver-health monitor (how this edge announces its death AFTER shipping, if it ships)

The driver is WATCHABLE independent of outcomes: the rolling per-city TWC−WU signed
offset (already logged daily by twc_forecast_logger). Kill-in-life conditions, mechanical:
- offset |median| decays toward 0 over a rolling 40-pair window (station alignment gone —
  e.g., TWC re-bases its forecast off the same public NWP), or
- offset sign flips and holds for 20+ pairs (regime change in their pipeline), or
- Phase-5 correlation to the council rises above the audit threshold (independence lost).
Any of these RETIRES the member BEFORE bucket-hit degradation is statistically visible —
the entire point of driver-first: watch the driver, not the losses.

## Regime statement (where this edge lives, if real)

Station-effect-dominant days: calm/locally-forced conditions, °F-boundary days, coastal/
microclimate cities (SF-class; Singapore sea-breeze). Expect LITTLE in strongly synoptic
regimes where all models converge. Post-ship, gain should be reported stratified by
council-vs-WU divergence tercile; a gain living ONLY in the wrong stratum (synoptic) is
evidence the measured edge is not this driver, and triggers re-adjudication.

## Scope guards

- Promotion routes through Plan-3 candidate/shadow machinery; L2 promotion stays
  human-gated (project_learning_loop). This file adds the DRIVER clauses; it does not
  bypass any existing gate.
- No backfill: TWC has no historical-forecast archive; only forward pairs count.
