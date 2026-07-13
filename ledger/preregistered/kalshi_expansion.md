# REGISTERED — Kalshi weather-market expansion (staged; nothing proceeds past S0 without the user)

*2026-07-13. The answer to "what is the most important thing being missed": every study
this week concluded REAL SIGNAL, DEAD VENUE — a fact about five thin Polymarket books,
not about the method. This registers the venue question so it is investigated with the
same discipline that produced the refusals, and so no future session builds toward Kalshi
ad hoc. REGISTRATION ONLY: no host contacted from repo code, no data layer built, no
hypothesis scored under this file — each later stage carries its own pre-registration.*

## Verified facts (2026-07-13, via public web — sources dated, not from memory)
- **KXHIGHNY confirmed end-to-end** (Kalshi API, unauthenticated public market data):
  "Highest temperature in NYC", daily, Category Climate and Weather, settlement source
  "NWS Climatological Report" (forecast.weather.gov CLI product, issuedby=NYC).
- **Depth verified live (KXHIGHNY-26JUL13):** ~39,316 contracts event volume across six
  2°F buckets; 1–2¢ spreads; two-sided quotes even on non-modal buckets (e.g. 0.10/0.11).
  Our Polymarket weather books run $345–4,500 with one-sided afternoon books — the depth
  ratio is roughly 10–100×.
- **KXHIGH*/KXLOW* families** exist across multiple US cities (NYC/Chicago/Miami/Austin
  confirmed by secondary sources; one source claims ~20 cities; the full lineup is
  enumerable from the series API at S1 — do not trust the ~20 figure until enumerated).
- **Settlement quirks (from Kalshi's own help center — OUR seam class):**
  (a) settles on the FINAL NWS Daily Climate Report, typically next morning — preliminary
  vs final CORRECTIONS exist; (b) settlement may be DELAYED when the CLI high is
  inconsistent with 6-hr/24-hr METAR highs; (c) **DST WINDOW TRAP: during daylight saving
  the "day" is 1:00 AM → 12:59 AM the FOLLOWING day** — a 24h window OFFSET from the
  civil day (the WP-2 local-day-straddle class, pre-identified instead of discovered by
  a miss); (d) the NYC CLI station is Central Park (KNYC) — a NON-airport station; per-
  city station identity must be pinned from each contract's terms PDF, never assumed.

## Driver-first statement
- **Identity (top tier):** the running-max ratchet holds wherever a daily max settles.
- **Driver:** mechanical, systematic reads of the PUBLIC settling record. Kalshi's chain
  (ASOS/METAR obs → NWS CLI) is MORE mechanically readable than WU's redistribution, and
  we already consume the underlying feed (IEM ASOS — the KSFO °F pipeline is the
  working precedent).
- **Venue hypothesis (what the expansion actually tests):** at 10–100× depth with
  professional makers, does ANY afternoon residue survive? Counter-hypothesis, stated
  with equal weight: deep books reprice faster than thin ones; the residue may be
  EXACTLY zero and the expansion's honest output is a measured refusal at real n.
- **Kill condition on the expansion itself (frozen now):** if, after ≥20 accrued
  market-days of point-in-time snapshots on the pilot city, the afternoon
  (post-certified-hour) favorite gap is ≤ the spread on ≥80% of days, the venue-depth
  hypothesis is DEAD — close the expansion with a dead-ledger entry; no sunk-cost
  building past it.

## Stages (each gated; order fixed)
- **S0 — USER DECISION (blocks everything):** allowlist `api.elections.kalshi.com`
  (public market data, no auth — verified) and, for contract terms,
  `kalshi-public-docs.s3.amazonaws.com`. New hosts are the user's call alone. NO Kalshi
  trading API, NO keys, NO account — market data only, recommend-only posture permanent
  under this registration.
- **S1 — seam registration (one PILOT city):** enumerate the series lineup from the API;
  pick the pilot (airport-station city preferred over KNYC to reuse the METAR seam
  verbatim); pin station identity from the contract terms; register the CLI parsing +
  corrections + DST-window rules as the city's settlement seam (its own prereg with
  KATs — the Manila/London precedent, done BEFORE any number is trusted).
- **S2 — data layer + accrual (assert nothing):** IEM ASOS backfill via the existing
  pipeline; a Kalshi snapshot logger writing the same point-in-time ladder schema as
  market_snapshots; certified clocks earned from the archive (crossover replay) exactly
  as WSSS/EGLC earned theirs. Accrue ≥20 market-days before ANY hypothesis prereg.
- **S3 — frozen probes (own preregs, one attempt each):** first the calibration slice
  (weather_market_calibration.py already takes any ladder shape), then the post-peak
  analog with entry at the pilot's OWN certified hour. Same six-criterion bar; a PASS
  goes to a forward paper ledger, never capital.

## Scope guards
- Stdlib-only holds (Kalshi market data is plain JSON REST).
- The existing basket, automation, and every frozen registration are untouched.
- This file does NOT authorize trading, accounts, or auth of any kind — a future
  decision that would need its own registration and the user's explicit instruction.
- Budget: ONE pilot city through S3 before any second city is even proposed.

## S0 — EXECUTED 2026-07-13 (user-approved; both hosts allowlisted; verified through the repo sandbox)

- `api.elections.kalshi.com` + `kalshi-public-docs.s3.amazonaws.com` added to
  `security.ALLOWED_HOSTS` with full registration-referencing comments; the trading
  surface remains deliberately un-allowlisted. Smoke test THROUGH SafeHTTPClient (SSRF
  guard, size caps, allowlist in-path): KXHIGHNY series fetched clean.
- **Enumeration (S1 opening fact): 53 KXHIGH*/KXLOW* series.** Cities confirmed: NYC,
  Chicago, Miami, Austin, Denver, Houston, LA, Philadelphia, Atlanta, Boston, Dallas,
  DC, Las Vegas, Minneapolis, New Orleans, OKC, Phoenix, San Antonio, Seattle, and
  **San Francisco (KXHIGHTSFO)**. The lineup contains generation variants/duplicates
  (KXHIGHOU vs KXHIGHHOU; KXHIGHDEN vs KXHIGHTEMPDEN; KXLOWNY vs KXLOWNYC) — S1 must
  select the ACTIVE series per city by checking open events, never by name.
- **Pilot recommendation: San Francisco via KXHIGHTSFO** — the only city where the
  full pipeline already exists (10y KSFO IEM archive, °F grain suite, certified clocks:
  declining@15:00 ≈ 96%, @16:00 ≈ 99%, pattern layer). Seam work reduces to: pin the
  CLI settlement station from the contract terms (NWS "San Francisco" climate reports
  can be issued for the airport OR downtown — DO NOT assume KSFO), the
  preliminary-vs-final correction rule, and the DST 01:00–00:59 window. CONDITION: if
  the contract terms pin a non-KSFO station, the pilot decision is re-opened, not
  forced.

## S1 — EXECUTED 2026-07-13 (pilot CONFIRMED: San Francisco / KXHIGHTSFO)

Full seam registration: `kalshi_sf_seam.md`. Station pinned three ways to KSFO airport
(CLI issuedby=SFO; CLISFO product header; rules_primary). Depth verified at raw-field
level through the repo sandbox: 34,470 contracts / 25,934 OI / 6-of-6 buckets two-sided
(NYC/CHI/MIA also deep but not fully two-sided today). The earlier "SF book empty" read
was a parsing artifact (legacy field names vs this API's `*_dollars`/`*_fp` strings) —
corrected same-session and encoded as seam rule 5. Bonus specimen banked: the CROSS-VENUE
TRUTH SPLIT (07-12 KSFO: CLI 76°F vs WU ~74°F — Kalshi and Polymarket settle the same
station on different records). Next: S2 per the seam file — CLI capture + IEM CLI-archive
probe + dual-venue matched-timestamp snapshot logger + ≥20 market-days accrual.

## S2 REORDERED 2026-07-13 (distribution-over-verdicts law: cheapest decisive test FIRST)

The original S2 (build logger → wait ≥20 forward days) violates kill-velocity: Kalshi's
PUBLIC API serves TRADE HISTORY for SETTLED markets (GET /markets/trades, cursor-paginated,
same allowlisted host) — the KXHIGH*/legacy HIGH* families have months-to-years of settled
daily events, i.e. a HISTORICAL distribution of afternoon price paths vs CLI settles,
available today. Revised order:
- **S2a — HISTORICAL KILL TEST (before ANY build beyond a fetch script):** its own frozen
  prereg; reconstruct afternoon (post-certified-hour ET-equivalent) favorite prices from
  settled-market trade history across ALL available KXHIGHTSFO (and predecessor-series)
  days; the expansion's frozen kill condition applies to THIS distribution (afternoon
  favorite gap ≤ costs on ≥80% of days → DEAD now, zero further build). Target n:
  hundreds of market-days, not 20.
- **S2b — forward dual-venue snapshot logger + accrual:** built ONLY if S2a survives.
The seam prereg (kalshi_sf_seam.md) rules are unchanged and S2a must honor them (FINAL-CLI
truth via the IEM CLI-archive probe FIRST — the truth source is itself part of the test).
