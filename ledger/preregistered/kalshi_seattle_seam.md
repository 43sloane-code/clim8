# Pre-registration — Kalshi Seattle settlement seam (S1 expansion)

*2026-07-20. User-authorized expansion of `kalshi_expansion.md` to Seattle. Pins the
NWS Climatological Report station for the Kalshi Seattle high-temperature market,
with IEM ASOS as the settlement oracle and WU as a cross-reference. S2 data-layer
build and S3 probes are gated on ≥20 accrued market-days after this seam is wired.*

## Pinned facts

- **Active series:** `KXHIGHSEA` (ticker to be verified; Kalshi Seattle high-temperature
  daily series) — pending confirmation from the live series API or contract terms.
- **Settlement station: SEATTLE-TACOMA INTL AIRPORT (KSEA).** Pinned from the NWS
  Climatological Report convention for Seattle: the NWS Seattle/Tacoma WFO (SEW)
  issues the daily CLI for KSEA, and the IEM CLI archive returns data for KSEA.
  This is the record the Kalshi contract names as the oracle — **not WU**.
- **Bucket structure (expected, to verify live):** 2°F, INCLUSIVE both ends, plus two
  open tails. Grain whole °F. Binary markets, linear-cent price levels.
- **Clock rules:** same Kalshi weather convention — last trading 11:59 PM ET; during
  DST the market day is **01:00 → 00:59 local next day** (not the civil day).

## Settlement-truth rule

Kalshi Seattle resolves on the **NWS Climatological Report for KSEA**, derived from
ASOS/METAR. The project already consumes that feed via `mesonet.agron.iastate.edu`
(allowlisted), following the Austin precedent:

1. **Settlement truth:** IEM ASOS METAR daily extremes at KSEA, with the 6-hourly/T-group
   fine-grain read (`tools/finegrain_read.py`) used as a same-day settlement cross-check
   where boundary risk justifies it.
2. **Backtest truth:** IEM ASOS METAR daily extremes at KSEA. Market and backtest share
   the same ASOS source.
3. **WU cross-reference:** the WU KSEA record is captured as a *cross-reference* only,
   never as the settlement anchor. Any systematic WU↔NWS-CLI divergence is logged in
   `ledger/ksea_cli_wu.jsonl`.

## Seam rules S2 code must encode

1. **Station pinning:** Seattle verdicts anchor on KSEA coordinates (47.4502, -122.3088),
   not the city centroid.
2. **Truth source:** `council._resolve_truth` routes "Seattle" to KSEA IEM ASOS.
3. **Grain:** whole °F at KSEA. The served bucket call for Seattle is rendered in °F
   and maps to the Kalshi 2°F inclusive buckets.
4. **DST window:** the 01:00–00:59 local day for max attribution — KAT with synthetic
   midnight-straddling peak.
5. **API field discipline:** `*_dollars` / `*_fp` strings; absent fields parse to None.
6. **Intraday:** KSEA added to the hourly-station table (`intraday_ceiling._HOURLY_STATION`)
   and the dead-bucket eliminator (`intraday._CITY_CONFIG`) with whole-°F settlement
   grain. The dead-bucket eliminator uses the NWS API (`api.weather.gov`, allowlisted)
   blended with the IEM ASOS archive, so the running max closes the ~30 min IEM lag.
   The ceiling-sharpening lever continues to learn its remaining-rise distribution from
   the IEM hourly backbone.
7. **Cross-venue logging:** `tools/kalshi_logger.py` must snapshot the Seattle series
   alongside SF/Austin once the exact series ticker is confirmed.

## Known limitations frozen at S1

- **Kalshi series ticker unconfirmed.** The `tools/kalshi_logger.py` entry is pending
  the verified series ticker (currently not enumerable through the API within the
  response-size cap). No number is asserted until the ticker is pinned.
- **No 10-year KSEA fine-grain archive yet.** `tools/finegrain_read.py` is KSFO-only;
  KSEA will get live fine-grain reads but no historical `pattern_rate` until a 10-year
  archive is built.
- **Certified lock clocks not yet earned.** Intraday runs use the generic state/season
  risk tables until KSEA-specific bins reach n≥20 per cell.

## What S2 is

KSEA IEM ASOS overlay for `fetch_station_daily`; council truth routing for "Seattle" →
KSEA; whole-°F bucket rendering; hourly intraday station entry; ≥20 market-days accrual
before any S3 probe. NOTHING is asserted, scored, or traded under this file.
