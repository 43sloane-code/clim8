# REGISTERED DEFECT — register floor-raise over-reads across a bucket boundary (2026-07-07)

*Caught live on Singapore 07-07 by the user's observation that the market sat at 93% on 32
while our register-fused lock banked 33.*

## The defect (verified live)
`sources._fuse_live_floor` raises the intraday banked floor from the WU v3 24h-register,
gated ONLY as floor-raise-only + attributable-vs-yesterday's-max. On 07-07 the register read
91°F (32.78°C → bucket 33) while the routine METAR plateau and the WU DAILY settlement record
both read 90°F (32°C). The register crossed the 90/91°F (32/33) boundary on a transient the
settled record never confirmed, so the lock BANKED 33 — over-reading by a full bucket. Four
independent signals said 32 (routine obs, WU daily, market 93.3%, 113 historical analogs);
only our own register-fused lock said 33. `settle_cross_check` warns on the divergence but the
served headline still leads with the over-read floor.

## Hypothesis / fix (gated — touches a served number)
The register may raise the floor only to a bucket the SETTLEMENT-GRADE feed (routine METAR /
WU daily) also supports; a register reading that alone crosses a bucket boundary ABOVE the
routine running-max is served as a PROVISIONAL hint ("register brushed N°C, unconfirmed"),
NOT a banked floor. Equivalent: bank floor = round(min(register, routine_runmax + epsilon)).

## Frozen gate
Backtest on the WU-grain 3y set (the ONLY set with both register-class and settled values):
count days where register-banked bucket > WU-settled bucket (false raises) vs where the
register correctly led the settlement (true early raises). CERTIFY the gating iff it removes
false raises WITHOUT dropping >1 true early-raise per false one removed, on both halves.
Fail → keep current fusion, D18.

## Companion note (not a defect, a labeling lag)
Intraday, the served day-ahead HIGH and the market-comparison model pmf remain the frozen
morning forecast — below the observed running max once the day has climbed past it. The
INTRADAY LOCK already overrides for the headline; but the HIGH line + market-comparison
should floor the model's central estimate by the observed running max intraday so
"model vs market" is not read off a stale sub-observation forecast. Labeling-first; separate.
