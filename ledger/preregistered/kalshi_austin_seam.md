# Pre-registration — Kalshi Austin settlement seam (S1 expansion to second city)

*2026-07-18. User-authorized expansion of `kalshi_expansion.md` / `kalshi_sf_seam.md`
to Austin. Executes S1 for `KXHIGHAUS`: active-series verification, station pinning,
and the settlement-seam rules S2 code must encode. No S3 hypothesis probes may begin
until ≥20 market-days accrue under this seam.*

## Pinned facts (all fetched 2026-07-18 via SafeHTTPClient / Kalshi public API)

- **Active series: `KXHIGHAUS`** ("Highest temperature in Austin on ...?", daily) —
  selected by OPEN EVENT with live two-sided quotes. Open event verified:
  **KXHIGHAUS-26JUL18**, 6 buckets, 5/6 two-sided (≤91°F tail one-sided at 0.01 ask).
- **Settlement station: AUSTIN BERGSTROM INTL AIRPORT (KAUS).** Pinned from the contract's
  own `rules_primary` text (Kalshi public market data): "If the highest temperature
  recorded in **Austin Bergstrom** for [date] as reported by the **National Weather
  Service's Climatological Report (Daily)** ...". The contract names the NWS CLI as the
  oracle — **not WU**.
- **Bucket structure:** 2°F, INCLUSIVE both ends (e.g. 94–95°F), plus two open tails
  (T-tickers: "89° or below" / "98° or above"). Grain whole °F. Binary markets,
  linear-cent price levels.
- **Clock rules:** same Kalshi weather convention — last trading 11:59 PM ET; market day
  during DST is **01:00 → 00:59 local next day** (NOT the civil day).

## Settlement-truth rule (the seam this registration exists to protect)

Kalshi Austin resolves on the **NWS Climatological Report for KAUS**, which is derived
from the same ASOS/METAR feed the IEM archive ingests. The project already consumes that
feed via `mesonet.agron.iastate.edu` (allowlisted). Therefore:

1. **Settlement truth:** IEM ASOS METAR daily extremes at KAUS, with the 6-hourly/T-group
   fine-grain read (`tools/finegrain_read.py`) used as a same-day settlement cross-check
   where boundary risk justifies it.
2. **Backtest truth:** IEM ASOS METAR daily extremes at KAUS. Unlike London, there is no
   "WU pays out" split here — the market and the backtest can share the same ASOS source.
3. **No WU substitution:** the WU KAUS record may be used as a *cross-reference* only,
   never as the settlement anchor. Any systematic WU↔NWS-CLI divergence must be logged,
   not papered over.

## Seam rules S2 code must encode (each with a KAT before any number is trusted)

1. **Station pinning:** Austin verdicts anchor on KAUS coordinates (30.194, -97.67),
   not the city centroid or KATT/Camp Mabry. The current `run.py` output shows the
   forecast anchored on KATT — that is the bug this seam fixes.
2. **Truth source:** `council._resolve_truth` must route "Austin" to KAUS IEM ASOS
   (not Meteostat/KATT). Implementation follows the existing `_IEM_OVERLAY_TZ` / pinned
   ICAO pattern used for Karachi/Jeddah/London, but with IEM as both settlement and
   backtest source.
3. **Grain:** whole °F at KAUS (the contract's native unit). The council's internal
   residual cloud may still be computed in °C, but the served bucket call for Austin
   must be rendered in °F and map to the Kalshi 2°F inclusive buckets.
4. **DST window:** the 01:00–00:59 local day for max attribution — KAT with a synthetic
   midnight-straddling peak (reuse WP-2 pattern).
5. **API field discipline:** same as SF seam rule 5 — `*_dollars` / `*_fp` strings;
   absent fields parse to None, never 0.
6. **Cross-venue logging:** `tools/kalshi_logger.py` must snapshot KXHIGHAUS alongside
   KXHIGHTSFO; Polymarket has no Austin market, so only Kalshi is recorded.

## Known limitations frozen at S1

- **No 10-year KAUS fine-grain archive yet.** The `tools/finegrain_read.py` pattern layer
  is KSFO-only; KAUS will get live fine-grain reads but no historical `pattern_rate`
  until a 10y archive is built (a separate data-acquisition task, not required for S1).
- **Intraday lock not configured.** Austin is not in `_LIVE_REGISTER` / `_HOURLY_STATION`;
  lead-0 runs get a day-ahead-style band, not a certified intraday lock. Adding that is
  a future S1+ instrumentation task with its own prereg.
- **Meteostat lag mismatch resolved:** once KAUS is pinned, the council no longer
  backtests Austin on stale KATT Meteostat data from the wrong season.

## S1 corrections record (measurement errors made and caught this session)

- Initial full-stack read anchored the council forecast on **KATT/Camp Mabry** while the
  Kalshi contract settles on **KAUS/Austin Bergstrom** — a station/source mismatch that
  produced a ~3–4°F divergence (council ~98°F vs market/NWS read ~95°F). Caught by
  comparing `rules_primary` to the rendered anchor station. This prereg exists to prevent
  that mismatch from ever entering the served path.

## What S2 is (built-to spec from this file)

KAUS IEM ASOS overlay for `fetch_station_daily`; council truth routing for "Austin" →
KAUS; whole-°F bucket rendering; Kalshi logger updated for KXHIGHAUS; ≥20 market-days
accrual before any S3 probe. NOTHING is asserted, scored, or traded under this file.
