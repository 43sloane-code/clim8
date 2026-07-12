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
4. **NATIVE-°F HEADLINE BUCKET PMF — ATTEMPTED 2026-07-12, FAILED THE GATE (dead ledger D19).**
   The naive fix (quantize the same residual cloud at whole-°F) was pre-registered
   (`sf_native_f_headline.md`) and probed once on 10y KSFO (3,628 walk-forward days): it LOSES
   to the served °C pmf read as a °F answer — log score fails BOTH halves (−3.372 vs −3.159 /
   −3.314 vs −3.165), modal hit not sign-stable (H1 +1.9pt, H2 −2.4pt). At day-ahead σ (~4°F)
   with n≈160 residuals, a whole-°F empirical pmf over-fits bin noise across ~15 buckets; the
   °C bucketing is an accidental regularizer (2°F bins). So the °C headline STANDS, and SF
   stays on-demand / out of the basket. A future °F headline is a NEW mechanism (smoothed /
   shrunk density estimate), needs its own pre-registration, and must beat the °C-split
   baseline — read D19 before re-proposing.

## STATUS
SF on-demand verdict anchors on the live WU settlement oracle (current, °F) exactly like
RPLL/WSSS — truth + grain SOLVED, settlement-reference block renders °F end-to-end (#3), and
the headline pmf stays °C ON MEASURED EVIDENCE (#4 → D19: the °C pmf is the better °F answer
at day-ahead σ; quote the SETTLEMENT section's °F figures for SF, per CLAUDE.md). Basket/TWC
promotion remains blocked unless a smoothed-°F-density candidate clears its own gate.
