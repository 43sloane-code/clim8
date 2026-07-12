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
target, not insider knowledge.

*Second operator sharpening (2026-07-12): the market settles ONLY on the
Wunderground-branded record — nothing TWC-branded (forecast, v3 nowcast, internal obs)
ever settles, and not by accident: an oracle is chosen for PUBLIC VERIFIABILITY, not
freshness — the WU station-history page is the one surface any counterparty can inspect
(the same asymmetry behind banked-vs-leading: a fresher same-company cur_f still never
banks until the record prints). Consequence for this driver: "TWC calibrates to the same
record" is an ASSUMPTION — TWC's internal verification target is plausibly, not provably,
identical to the Wunderground-displayed record. The driver holds only as far as that
identity holds, and G3 is its test: calibration to anything else shows up as an unstable
offset against the record. Standing law reaffirmed: TWC never becomes a truth source,
never feeds settlement, never anchors a lock — display cross-reference and gated member
candidate only.* If TWC in fact just re-bases public NWP with no
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
- **G3′ (driver evidence — AMENDED pre-completion, 2026-07-12 collaborative revision;
  rationale below):** the per-city TWC−record offset must be CONSISTENT across both folds:
  EITHER (a) sign-stable (a stable, correctable station bias), OR (b) fold medians'
  bootstrap CIs overlap around ~0 AND TWC's error-sd vs the record is below the council
  blend's on both folds (tight calibration — the driver's STRONG form). An offset that
  materially flips sign between folds (CI-disjoint medians of opposite sign) = no
  mechanism; REFUSE even if G1/G2 pass. *Why amended: the original G3 ("sign-stable or
  refuse") would have refused a PERFECTLY record-calibrated TWC — near-zero offsets flip
  sign by pure noise, so the criterion read the driver's best case as driver-absent.
  Amended before the clock fills (n≈26/40), motivated by logic, not outcomes; G1/G2/G4
  pass thresholds untouched.*
- **G4 (independence):** Phase-5 correlation audit passes (TWC error not spanned by the
  member panel, r-threshold per twc_offset.md conventions).

One attempt. Any failure → dead-ledger entry; TWC stays a display cross-reference only.

## Driver decomposition (2026-07-12 collaborative revision — two drivers + one confound)

The "shared verification target" driver is really TWO mechanisms with DIFFERENT deaths,
plus a mundane confound that is neither:
- **Driver A — station-MOS heritage:** TWC runs decades of MOS-class correction
  (Glahn–Lowry lineage) on this exact METAR series. Predicts gains concentrated on
  microclimate/council-divergence days. Dies on station hardware/siting changes or a TWC
  model-mix change — visible as an offset BREAK.
- **Driver B — settlement-convention alignment:** whole-°F storage, specials aggregation,
  the displayed record's rounding. Predicts gains concentrated on °F-BOUNDARY days.
  Dies if TWC's internal target is not the Wunderground-displayed record.
- **Confound C — cycle freshness (NOT a driver):** TWC's endpoint updates continuously;
  council members carry Open-Meteo cycle latency. A newer forecast beating an older one
  is timing, not station knowledge — and shipping it labeled "station driver" aims the
  death-watch at the wrong series.

**Adjudication BEFORE Plan-3 promotion (if G1–G4 pass):** stratify TWC-added-value by
(i) °F-boundary proximity, (ii) council-vs-record divergence tercile, (iii) log-time /
best-available vintage proxy. Attribute the gain to A, B, or C. If the gain lives mainly
in (iii) — or cannot be separated from freshness with the data held — promote (if at all)
labeled "operational timing advantage, driver UNRESOLVED", never "station driver
confirmed". Promotion remains human-gated either way.

## Driver-health monitor (how this edge announces its death AFTER shipping, if it ships)

The driver is WATCHABLE independent of outcomes: the rolling per-city TWC−record offset
and error-sd (already logged daily by twc_forecast_logger). Kill-in-life conditions,
mechanical — REVISED with the decomposition (decay-toward-zero REMOVED: under the
corrected driver, offset→0 with shrinking spread is the driver IMPROVING, not dying):
- offset BREAK: rolling 20-pair median jumps outside the prior 40-pair bootstrap CI
  (station hardware / TWC pipeline change — kills Driver A), or
- TWC error-sd vs the record rises above the council blend's over a rolling 40-pair
  window (calibration advantage gone — kills the strong form), or
- °F-boundary-stratum gain disappears while other strata hold (identity with the
  displayed record broken — kills Driver B), or
- Phase-5 correlation to the council rises above the audit threshold (independence lost).
Any of these RETIRES the member BEFORE bucket-hit degradation is statistically visible —
the entire point of driver-first: watch the driver, not the losses.

## Regime statement (where this edge lives, if real)

Station-effect-dominant days: calm/locally-forced conditions, °F-boundary days, coastal/
microclimate cities (SF-class; Singapore sea-breeze). Expect LITTLE in strongly synoptic
regimes where all models converge. Post-ship, gain reported stratified by the THREE
adjudication strata above; a gain living ONLY in the wrong stratum (synoptic, or the
freshness proxy) is evidence the measured edge is not the named driver, and triggers
re-adjudication.

## Scope guards

- Promotion routes through Plan-3 candidate/shadow machinery; L2 promotion stays
  human-gated (project_learning_loop). This file adds the DRIVER clauses; it does not
  bypass any existing gate.
- No backfill: TWC has no historical-forecast archive; only forward pairs count.
