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

## REMAINING (blocks basket promotion)
3. **NATIVE-°F BUCKET PMF.** Headline BUCKET pmf/band + intraday lock still compute in whole-°C;
   only the settlement section converts to °F. Fine for °C cities; for SF's finer °F market the
   °C headline (~20°C) is ~2 °F-buckets coarse. The °F read IS served (settlement: 66-68°F). Needs
   the core bucket pipeline to run in the detected grain end-to-end → must clear the frozen gate.
   NOT attempted. Until then SF is on-demand + archive-pattern; NOT snapshotted into the basket.

## STATUS
SF on-demand verdict now anchors on the live WU settlement oracle (current, °F) exactly like
RPLL/WSSS — truth + grain SOLVED. Only native-°F headline bucketing remains before CITIES/TWC
promotion. On-demand + archive-pattern layer fully usable now.
