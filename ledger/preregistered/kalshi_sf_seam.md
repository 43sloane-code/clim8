# Pre-registration — Kalshi SF pilot settlement seam (S1 of kalshi_expansion.md)

*2026-07-13. Executes S1: active-series selection, station pinning, and the settlement-seam
rules the S2 code must encode (with KAT requirements), all pinned from first-party sources
through the repo sandbox before any number is trusted. S2 (data layer + accrual) may begin
on this file's rules; S3 hypothesis probes remain gated on ≥20 accrued market-days.*

## Pinned facts (all fetched 2026-07-13 via SafeHTTPClient / first-party pages)

- **Active series: `KXHIGHTSFO`** ("San Francisco High Temperature Daily", daily) — selected
  by OPEN EVENT with live two-sided quotes, not by ticker name (the lineup carries dead
  generation variants). Open event verified: KXHIGHTSFO-26JUL13, **34,470 contracts volume,
  25,934 OI, 6/6 buckets two-sided** (the only checked city fully two-sided; NYC 43.7k/4-of-6,
  Chicago 45.7k/4-of-6, Miami 43.0k/3-of-6).
- **Settlement station: SAN FRANCISCO INTL AIRPORT (KSFO).** Three concordant sources:
  settlement_sources URL (forecast.weather.gov, site=MTR, product=CLI, **issuedby=SFO**);
  the live CLISFO product header ("San Francisco Airport", NWS San Francisco Bay Area);
  rules_primary on each market ("maximum temperature recorded at San Francisco ... according
  to the National Weather Service"). **Pilot condition SATISFIED — our 10y KSFO IEM archive,
  °F grain suite, and certified clocks (declining@15:00 ≈ 96%) apply to this station.**
- **Bucket structure:** 2°F, INCLUSIVE both ends (floor_strike/cap_strike, e.g. 82–83), plus
  two open tails (T-tickers: "75° or below" / "84° or above"). Grain whole °F. Binary
  markets, linear-cent price levels.
- **Clock rules:** last trading 11:59 PM ET regardless of data releases; expiration at the
  first 7:00/8:00 AM ET after the CLI release (or +1 week failsafe). The market day during
  DST is **01:00 → 00:59 local next day** (Kalshi help center) — NOT the civil day.

## THE CROSS-VENUE TRUTH SPLIT (the seam that pays for this whole registration)

Live specimen, 2026-07-12, same station KSFO, same day: **NWS CLI max 76°F (3:46 PM)** vs
the **WU record ~74°F** that Polymarket settled on. Two venues, two "official" records,
two degrees apart. Consequences, frozen now:
1. Kalshi truth = **the FINAL NWS CLI, never WU** — the London SETTLE≠BACKTEST split
   precedent, now cross-venue on one station. No code may ever settle a Kalshi row from
   the WU feed or vice versa.
2. The WU-vs-CLI divergence becomes a LOGGED SERIES from S2 day one (both records captured
   daily) — it is simultaneously a seam guard and a candidate information asymmetry, and it
   may be nonzero systematically (CLI ingests 6-hourly METAR max groups).
3. S2 must verify the suspected mechanism (CLI ≥ hourly-table max via 6-hr groups) against
   data before any use.

## Seam rules S2 code must encode (each with a KAT before any number is trusted)

1. **CLI parsing:** the MAXIMUM line (value + time) from the CLISFO daily climatological
   report; PRELIMINARY vs FINAL versions both captured; settlement rows may fill only from
   a FINAL report; a final that differs from a preliminary raises a logged correction flag
   (Kalshi itself delays settlement on METAR inconsistency — mirror that conservatism).
2. **Backtest truth source:** the IEM CLI archive on the ALREADY-ALLOWLISTED host
   (mesonet.agron.iastate.edu exposes parsed NWS CLI products) — to be probe-verified at S2
   against ≥30 recent CLISFO days before adoption; if it fails verification, fall back to
   direct CLI capture forward-only (no backfill fabrication).
3. **DST window:** the 01:00–00:59 day for max attribution — KAT with a synthetic
   midnight-straddling peak (the WP-2 class).
4. **Bucket mapping:** T/B ticker semantics (floor/cap inclusive; open tails) — KAT against
   the recorded JUL13 ladder.
5. **API field discipline:** this API serves `*_dollars` STRING fields and `*_fp` decimal
   strings (`volume_fp`, `open_interest_fp`, `yes_bid_dollars` …). Parsers must read those
   and MUST NOT silently default absent legacy keys to zero — KAT'd, because that exact
   bug produced today's false "empty books" reading (Corrections below).

## S1 corrections record (measurement errors made and caught this session)

- "SF's Kalshi book is empty" and the "all zeros incl. NYC" table were BOTH a parsing
  artifact (legacy field names read as None/0). Reversed same-session at raw-JSON level;
  the earlier WebFetch depth figures were real. Lesson encoded as seam rule 5.
- A cross-venue "40-point gap" between our morning Polymarket snapshot and Kalshi's live
  afternoon ladder was NOT claimed as an edge: the reads are hours apart on a live day.
  Cross-venue comparisons are valid only at matched timestamps — S2's snapshot logger
  records both venues in the same minute for exactly this reason.

## What S2 is (already registered in kalshi_expansion.md, restated as built-to spec)

IEM KSFO archive is DONE (10y). New: CLI capture (live + IEM archive probe), a Kalshi
ladder snapshot logger writing the market_snapshots-shaped schema (dollar-field aware,
both-venue matched timestamps), certified-clock reuse, ≥20 market-days accrual. NOTHING
is asserted, scored, or traded under this file; S3 probes get their own preregs against
the expansion's frozen kill condition.
