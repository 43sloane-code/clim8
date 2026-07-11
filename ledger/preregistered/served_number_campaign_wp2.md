# WP-2 addendum — `sources.wunderground_daily_max` local-day regrouping (Class D + held impact replay)

*Served-number campaign, WP-2 (F2). The FIX (regroup onto the contract-defined local civil day) is
Class-D deterministic correctness; the IMPACT REPLAY (how many historical served buckets change) is a
retro-audit, HELD by operator directive 2026-07-11 and run later.*

## The defect (exhibit)
`wunderground_daily_max` fetches `startDate=target` (one day, no `endDate`) and takes `max()` over EVERY
returned observation, WITHOUT regrouping onto the station's LOCAL calendar day. The WU history
endpoint's day window is keyed to the record's own boundary (UTC-ish), so for an off-UTC station the
returned rows STRADDLE two local days — and `max()` can pick up a temperature from the ADJACENT local
day. All active WU stations are off-UTC (Singapore/Manila +8, London +1 BST, SF −7, Jeddah +3, Karachi
+5), and this value is the **phantom-cap ceiling** feeding `_fuse_live_floor` (INTRADAY_LOCK) and the
run.py settlement cross-ref — a too-high ceiling defeats the phantom cap; a too-low one suppresses a
real re-heat. The correct pattern already exists in `_wu_daily_raw` (group by `valid_time_gmt` →
station-local via `ZoneInfo`); `wunderground_daily_max` simply skipped it.

## Fix design (frozen — Class D; cert = KAT delta-exhibit; the bucket-impact COUNT is the held replay)
1. **Add a `timezone` param.** `wunderground_daily_max(icao, target, timezone=None)`. Both callers pass
   the station's local tz (`intraday_ceiling` already has `tz` in scope; `run.py` passes
   `place.timezone`). `None` → UTC grouping (a safe degrade, not the target case).
2. **Widen the fetch to ±1 UTC day** (`startDate=target−1`, `endDate=target+1`) so the station-local
   `target` day is FULLY covered whatever the endpoint's own day semantics/offset — then FILTER; never
   trust the endpoint's `startDate` day again.
3. **Regroup onto the local civil day.** Each obs belongs to the local day of its `valid_time_gmt`
   instant (`fromtimestamp(vt, UTC).astimezone(zone).date()`), mirroring `_wu_daily_raw` exactly. Max
   over ONLY the obs whose local date == `target`. DST fold/gap handled by `zoneinfo` semantics.

## KATs (this WP)
- **KAT-F2a** UTC+8 station (WSSS): a hot obs at `target+1 00:30` local must be EXCLUDED — corrected max
  = the target-local obs, ≠ the naive max-over-all (the straddle the old code included). (RED pre-fix.)
- **KAT-F2b** London BST (UTC+1): a `target+1 00:30` BST obs excluded; a `target 23:30` BST obs kept.
- **KAT-F2c** DST fall-back day (Europe/London 2026-10-25 fold): obs on the transition day group to it
  correctly (no crash, right date); an adjacent-day obs is excluded.
- **KAT-F2d** parity: with NO straddle (all obs on `target` local), corrected max == the naive
  max-over-all — the fix is a no-op exactly where it should be.

*Repo-gate note (same as WP-1): KATs confirmed RED on the pre-fix code (which has no `timezone` param
and no local-day filter → wrong max), then committed GREEN with the fix.*

## HELD — impact replay / retro-audit (operator directive)
Recompute the corrected daily max over available WU history for every active station; diff vs the
as-served values; every diff day is an exhibit (which obs crossed the boundary, both values). Then
replay `_fuse_live_floor` over the diff days → **the count of served buckets that would have differed**
is the artifact headline (measured, not estimated). `daily_final` in the xref store is built from
corrected values only; contaminated rows flagged per §1.3. This gates `cur_f_corroboration_guard`
Phase-3 sign-off (§2.1: the guard's `recorded_max_f` must be F2-corrected). Until it runs, the fix stops
NEW straddle errors; it does not quantify or clean the historical ones.
