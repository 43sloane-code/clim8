# San Francisco (KSFO) — now WU-ORACLE anchored (like RPLL/WSSS); 1 blocker remains

*2026-07-06. SF settles on the live Wunderground KSFO feed (the user's correction — KSFO HAS a WU
feed). Re-anchored SF on the WU oracle, the exact record the market pays on, superseding the
earlier IEM-overlay detour. Config now mirrors Singapore/Manila exactly.*

## FIXED
1. **TRUTH FEED — via the WU oracle (best form).** Added KSFO to `WU_GEO`, `WU_LOCATION`
   ("KSFO:9:US"), `council._WU_TRUTH_STATIONS`, `storage._WU_SETTLE_TZ`. SF now anchors on the
   live Wunderground/Weather Company KSFO record + v3 current-conditions feed (cur_f + 24h
   register) — current, no archive lag, "the market's own oracle". The London-style IEM
   scaffolding (PINNED/STRICT_ANCHOR, _IEM_OVERLAY KSFO) was REVERTED — SF is a WU city, not IEM.
   Verified: daily 07-01..05 = 70/70/69/68/68°F; live current 62°F, 24h-register 69°F.
2. **GRAIN — whole °F.** `fetch_metar_daily` rule fixed (`frac_f>frac_c and frac_f>=0.4`); SF
   settlement reads whole-°F (66-68°F). °C cities unaffected (1.00 integral-in-C), KAT'd.
3. **SETTLEMENT-REFERENCE BLOCK — now renders °F (2026-07-07, commit pending).** The
   "SETTLEMENT RECORD — Wunderground KSFO" block used to hardcode °C (`_native_reading_int(_,
   "C", _)` + °C display), so SF's whole-°F record showed as a ~2-bucket-coarse °C reading under
   a "→ contract whole °C" label on a °F contract. `_settlement_reference` now buckets in the
   detected grain and `_settlement_reference_lines` renders in the native unit (°F for KSFO; °C
   unchanged for London/Manila/Singapore — identity conversion, byte-for-byte same). SF now
   headlines "high 57°F", "WU max 68.0°F → bucket 68°F", "→ contract whole °F". Display/labeling
   fix (the settlement section already SERVED °F, just mis-rendered); no gate needed. KAT
   `TestSettlementReferenceGrain`.

## REMAINING (blocks basket promotion)
4. **NATIVE-°F HEADLINE BUCKET PMF.** The HEADLINE bucket pmf/band + intraday lock still compute
   in whole-°C (SEPARATE from the settlement-reference block, now fixed in #3). For SF's finer °F
   market the °C headline (~20°C) is ~2 °F-buckets coarse. The settlement SECTION serves °F; the
   HEADLINE distribution does not. Needs the core bucket pipeline to run in the detected grain
   end-to-end → touches a SERVED probability → must clear the frozen gate. NOT attempted. Until
   then SF is on-demand + archive-pattern; NOT snapshotted into the basket.

## STATUS
SF on-demand verdict now anchors on the live WU settlement oracle (current, °F) exactly like
RPLL/WSSS — truth + grain SOLVED, and the settlement-reference block now renders °F end-to-end.
Only the native-°F HEADLINE bucket pmf (#4) remains before CITIES/TWC promotion — it is gated
(served probability). On-demand + archive-pattern layer fully usable now.
