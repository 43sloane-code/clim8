# San Francisco (KSFO) — 2 of 3 blockers FIXED; native-°F bucketing remains before basket promotion

*2026-07-06. Added SF on the London scaffold; a live verdict run exposed 3 blockers. Two are now
FIXED and tested (420/420); the third is identified and is the remaining gate for promotion.*

## FIXED
1. **TRUTH FEED (fixed, commit follows).** `fetch_station_daily`'s EGLC IEM overlay generalised
   to a `_IEM_OVERLAY_TZ` table (EGLC + KSFO) via `iem_overlay_truth_series`. SF now backtests on
   live KSFO METAR: "recent observed" is current (July, ~0-day lag, was March/~101d), regime is
   in-season, and the council shows real skill (CRPS 0.489 vs climatology 1.002, +51%, 82 days).
2. **GRAIN DETECTION (fixed).** `fetch_metar_daily` grain rule was `frac_f >= 0.9` — too strict
   for US ASOS (mixed whole-°F / 0.1-°C → frac_f ~0.5-0.8). Now `frac_f > frac_c and frac_f >= 0.4`:
   flips ONLY genuine °F stations (EGLC/WSSS/RPLL are 1.00 integral-in-C, unaffected — verified).
   SF settlement now reads whole-°F correctly (settles 67°F; "model 68-69°F vs market 66-67°F").

## REMAINING (blocks basket promotion)
3. **NATIVE-°F BUCKET PMF.** The headline BUCKET CALL + pmf + band + the intraday lock are computed
   in whole-°C throughout (residual cloud, bucket_contract, intraday_ceiling), and only the
   SETTLEMENT-ALIGNMENT section converts to °F. For the °C cities °C-bucket == settlement-bucket, so
   it's fine; for SF the °F market is finer (1°F ≈ 0.56°C), so the °C headline (20°C) is ~2 °F-buckets
   coarse. The °F info IS available (settlement section: 67-69°F) but the primary served pmf/lock are
   not native-°F. Serving SF headline + snapshotting it into the tracked basket needs the bucket
   pipeline to run in the detected grain end-to-end. NOT attempted here (touches the core pmf path
   for all cities — must clear the frozen gate). Until then SF is on-demand + archive-pattern only.

## STATUS
On-demand SF verdict now SOUND (current truth, in-season, +51% skill, correct °F settlement read);
pattern/intraday layer works in native °F from the archive. NOT promoted to CITIES/TWC — waits on
blocker 3. Do not snapshot SF into the basket until the °F bucket pipeline lands + gates.
