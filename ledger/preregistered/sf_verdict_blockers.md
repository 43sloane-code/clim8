# San Francisco (KSFO) — added as DATA/PATTERN layer; live VERDICT path is BLOCKED

*2026-07-06. Added SF on the same scaffold as London (SETTLEMENT_REFERENCE, PINNED_ANCHOR_ICAO,
STRICT_ANCHOR_ICAO, backfill _TZ, 10y KSFO IEM archive). But unlike London, the live COUNCIL
verdict is NOT reliable for SF — three architectural blockers, each verified live:*

1. **TRUTH FEED.** `fetch_station_daily` has a modern IEM/HKO overlay only for EGLC + the HK
   Observatory. KSFO has none, so the SF verdict fell back to the Meteostat bulk archive, which
   lags ~101 days — it served MARCH truth for a July target ("recent observed 2026-03-25...").
   FIX: add a KSFO live-IEM overlay to fetch_station_daily (the EGLC pattern).
2. **SETTLEMENT GRAIN.** SF markets settle WHOLE-°F (KSFO obs 100% integral-in-°F, 19% in-°C).
   The grain detector defaulted to °C ("12% integral in C") and the market cross-check compared
   council 20°C vs market 66°F — garbled. FIX: detect + carry °F grain end-to-end (the market
   is already whole-°F; the bucket/quantizer must follow).
3. **BACKTEST SEASON.** The Meteostat lag makes the bias/skill backtest out-of-season
   (bias +2.47°C, "regime ~103d off") — untrustworthy until (1) is fixed.

WHAT WORKS NOW (clean): the 10y KSFO IEM archive (`data/ksfo_hourly_iem.jsonl`) → pattern
recognition + intraday lock schedule in native °F. SF is the EASIEST of the four to lock
(false-decline trap 14% vs London 30% / Singapore 19%; cool tight marine-layer single peaks;
July high median 70°F, P10-P90 66-77°F; declining@15:00 settles 96%).

STATUS: SF is NOT promoted to the tracked basket (CITIES/TWC) — that waits on blockers 1+2.
The scaffold + archive are committed so the pattern layer works and the fix has a home.
Do NOT claim a live SF verdict number off the council path until the IEM overlay + °F grain land.
