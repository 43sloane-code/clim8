# Weather-Verdict Roadmap

Consolidated backlog for aligning the council's backtested verdicts with how
weather prediction markets actually settle, plus an optional read-only
market-data layer.

**Governing principle.** Every output must be data-derived and backtested,
never LLM-hallucinated. Use the strictest top-band, mutually-independent
sources. No arbitrary heuristics — every threshold must be anchored on earned
backtested signal. Never relabel output to look right; diagnose root cause.

**Hard boundary.** This project is *verification-only*. It does not place
trades, hold funds, sign transactions, or connect a crypto wallet. The
market-data layer (Checklist C) is read-only: it ingests public market prices
to *compare against* the model's verdict, nothing more.

---

## Checklist A — Settlement mechanics (how a market resolves)

How a weather contract pays out: an oracle reads a specific named airport
station's METAR, in that station's native integer grain, and maps it to a
bucket. The verdict must be expressible in those same terms.

- [x] **A2. Quantize verdict to native integer grain.** Snap the continuous
  verdict to the station's reporting grain (round-half-up) and report the
  settlement bucket. Grain is *data-detected*, not hardcoded: US ASOS reports
  integral °F (→ °F grain), international stations report integral °C (→ °C
  grain). Detected from the fraction of raw obs integral in each unit.
  Implemented in `sources.quantize_to_grain` + `council._settlement`; surfaced
  in CLI (`run.render`) and web UI (`index.html`). Verified end-to-end on
  Hong Kong (VHHH, °C grain, 33.7→34°C).
- [x] **A4. Validate Meteostat against the settlement source (METAR).** Fetch
  raw airport METAR (IEM ASOS archive) and compare the daily extremes against
  the Meteostat truth we backtest on, over the overlapping window. Reports
  signed mean/median/largest Δ and count of tail days ≥3°C apart.
  **Finding (VHHH, 60 days):** Meteostat clips daily highs on hot days vs raw
  METAR — high Δ mean +0.6, median 0.0, largest +4.0°C, 3 tail days; low Δ
  median 0.0. This is the root cause of the original HK high-side
  under-prediction: bias-correcting on an under-reading truth pulls the
  forecast down. Surfaced honestly in CLI + UI.
- [ ] **A1. Identify the exact settlement station per market.** A market names
  a specific airport (e.g. the one Weather Underground displays). Resolve the
  market's station → ICAO so the verdict is anchored on the *same* sensor the
  oracle reads. *Partial:* the two pinned cities are already aligned to their
  settlement instrument (London→EGLC, Hong Kong→HKO Observatory; see A5). What
  remains is the **generic** per-market station→ICAO resolution so any city in
  the basket matches its market's sensor, not just the two hand-pinned anchors.
- [ ] **A3. Map to the market's bucket definition.** Beyond rounding, encode
  the actual bucket edges a market uses (ranges / over-under thresholds) and
  report which bucket the quantized verdict lands in, with the distance to the
  nearest edge. *Partial (C8):* whole-degree markets are mapped via C4 edges;
  for **sub-degree markets the comparison now refuses to snap rather than
  fabricate.** `WeatherMarket.settles_sub_degree()` flags any market whose
  settlement precision is finer than its whole-degree bucket labels (the HK
  Observatory at 0.1°C). `compare_high` returns None for these and
  `compare.grain_support_note()` explains why; CLI prints a "MARKET COMPARISON
  (withheld)" line and the web shows a withheld card. Verified live HK June 5:
  the 33.9°C verdict is reported as settling 33.9°C, NOT a rounded 34. The real
  decimal→bucket convention still needs the contract rules (+ the right station,
  see A5) before HK can be compared for real.
- [x] **A5. Truth-source fork — DECIDED: option (b), already shipped.** For
  cities pinned to a specific settlement station, truth is repointed to that
  station's own settlement-grade instrument, not Meteostat:
  - **Hong Kong** anchors strictly on the **HKO Observatory open-data** daily
    record (`council._resolve_truth` → `data_source="hko_opendata"`; live "now"
    from the HKO rhrread / 1-minute 0.1 °C feed). The VHHH airport may **never**
    substitute; if the Observatory feed is stale the verdict drops to the honest
    ERA5 grid rather than jumping sensors.
  - **London** anchors strictly on the **EGLC METAR** record
    (`data_source="iem_metar"`); Meteostat survives only as the older-days
    coverage base under the METAR overlay.
  - Meteostat is retained for non-pinned cities and as the **disclosed-bias
    reference** (A4), never silently mixed into a pinned city's anchor.
  Signed off 2026-06. The remaining sub-degree HK *market-comparison* mapping is
  a data-acquisition task (the contract bucket rules), tracked under A1/A3 — it
  is **withheld, not fabricated**, until those rules are in hand.

### Quarantined lore (do NOT encode as fact)
Pasted market folklore that is unverified or self-contradictory and must not be
hardcoded: the "Hairdryer Incident," the "2.7-second window," and the
contradictory Paris airport codes (Le Bourget LFPB vs CDG). See
`memory/project_polymarket_settlement.md`.

---

## Checklist B — Settlement-alignment hardening (prior items)

Supporting work so the settlement view is trustworthy.

- [x] Detect and display native reporting grain with evidence (% of obs
  integral per unit).
- [x] Keep the headline verdict as the backtested continuous value; the
  settlement bucket is additive, never a silent relabel.
- [ ] **B1. Round-trip the bucket through backtest.** Score the *quantized*
  verdict against the *settlement source's* own integer record (not Meteostat)
  to report a settlement-accuracy hit rate distinct from the continuous skill.
- [x] **B2. Tail-day diagnostics.** The settlement source-check now names the
  specific days where raw METAR diverges ≥3°C from the Meteostat truth we
  backtest on — `source_check.tail_days` = `[{date, metar_high, observed_high,
  delta}]`, sorted worst-first — so the divergence is auditable, not just a
  count. Surfaced in `run.render` (the worst few, named) and the web settlement
  card (`index.html`). Verified in `tests/test_council.py::TestSettlementTailDays`.
- [x] **B3. Grain-detection guardrail.** `fetch_metar_daily` now returns a
  `grain_confidence` ("high"|"low"): the detected grain is only asserted with
  confidence when the chosen unit's integral fraction clearly dominates
  (`GRAIN_DOMINANT_FRAC=0.9`) on enough obs (`GRAIN_MIN_OBS=24`). Thin or
  ambiguous evidence — a half-degree / 0.1° station, or a near-empty window —
  is flagged low rather than silently claimed as a whole-degree grain.
  Propagated through `council._settlement` and shown with a ⚠ caveat in CLI +
  web. Verified in `tests/test_sources.py::TestGrainDetection`. *(Noted while
  here: `_clean_temp_cell` screens each raw cell through a °C plausibility band,
  so the °F column's grain evidence is unreliable for warm US readings >60 —
  a pre-existing latent quirk, out of scope as both pinned cities settle in °C.)*

---

## Checklist C — Read-only market-data layer (optional)

Ingest public Polymarket market data to compare the model's P(exceed) against
the market's implied probability. **Read-only. No trading, ever.**

- [x] **C1. Allowlist `gamma-api.polymarket.com` in the sandbox.** Added to
  `security.ALLOWED_HOSTS` (HTTPS-only, SSRF guard applies) plus a
  `get_json_array` fetch method (the `/events` body is a top-level JSON array,
  which `get_json` rejects).
- [x] **C2. Respect the request budget.** `MarketData` caps itself at
  `DEFAULT_MARKET_REQUEST_BUDGET=4` paginated calls and counts against the
  shared `MAX_REQUESTS_PER_RUN`. In practice all open temperature events come
  back in **one** request.
- [x] **C3. Fetch & parse markets.** `weather_council/market.py` →
  `MarketData.fetch_temperature_markets()` pulls the "Highest temperature" tag
  (`tag_id=104596`) and parses each event read-only into city / date / station /
  grain / precision / per-bucket Yes-price. `outcomes`, `outcomePrices`,
  `clobTokenIds` are JSON-encoded **strings** — each `json.loads`'d
  defensively. Verified live: 72 events, 0 missing a station, 1 request.
  **Settlement findings (feed A1/A5):**
  - Not one uniform oracle. 67/72 Wunderground airport METAR (US whole °F,
    intl whole °C), 3 NOAA airport (whole °C), **2 Hong Kong Observatory at
    0.1°C — not airport, not whole-degree.**
  - Paris settles on **Le Bourget (LFPB)**, confirmed by the WU URL — resolves
    the old contradictory-codes lore.
  - *(Resolved, see A5.)* The HK verdict no longer anchors on VHHH airport: it
    is repointed to the **HKO Observatory open-data record** (the Observatory's
    own gauge — the same instrument the HK market settles on). What remains is
    only the sub-degree **bucket-edge** convention (0.1 °C labels), still
    withheld under A3 pending the contract rules — never fabricated.
- [x] **C4. Parse bucket edges + match a verdict to its bucket.** Each bucket
  label parses to inclusive integer edges in the native unit (`MarketBucket.lo`
  /`.hi`; open tails = `None`). `WeatherMarket.bucket_for_high(verdict_c)` maps
  a continuous °C verdict to its settlement bucket by rounding to the native
  whole-degree reading (reusing `sources._round_half_up` — the *same* rounding
  the settlement block uses, so the two can never disagree).
  `edge_distance_c()` reports how far the verdict sits from the nearest bucket
  boundary (fragility signal; feeds A3). Buckets are sorted into a proper
  ladder. Verified live across 71 events: 0 unparseable, 0 non-contiguous
  ladders, 0 center-match failures.
- [x] **C5. Per-bucket probabilities from the empirical residual distribution.**
  `council.Validation` now keeps the *signed* held-out errors
  (`residuals_high/low` = observed − council prediction). `compare.compare_high`
  resamples those errors onto the verdict (predicted actual = verdict + eᵢ),
  quantizes via C4, and tallies an empirical distribution over the bucket
  ladder — no assumed shape, no invented σ, real skew/tails preserved. Floors
  at `MIN_RESIDUALS=10`. Reports model-modal vs market-modal, the market's raw
  overround, and a settlement-bias caveat. Verified live (London): warm-skewed
  residuals (mean +1.0°C) correctly move the modal bucket 18°C→19°C off the
  point verdict. **Probabilities are on the backtest-truth scale and the
  market's vig is NOT removed — both deliberately left to C6/C7.**
- [x] **C6. De-vig the market price.** `WeatherMarket.implied_probabilities()`
  normalises the Yes prices over the complete bucket partition (pᵢ = yesᵢ / Σ
  yes — standard multiplicative de-vig; assumption documented vs additive/Shin).
  `overround()` exposes the vig. `compare_high` now reports raw `market_yes`
  *and* de-vigged `market_prob` per bucket, so model vs market is like-for-like.
  Verified live: de-vig sums to 1.0 across all 71 events, ranking preserved,
  shrinks raw prices by the overround.
- [x] **C7. Calibrate — honest accounting, not a fabricated curve.**
  `council.Validation` now retains the *signed* held-out errors; `compare.py`
  adds a `Calibration` (n, bias, spread, skew, p10/p50/p90, and a weak
  out-of-sample 80%-band coverage self-check that learns the band on the
  chronologically-earlier half and measures the later half). `compare_high`
  attaches it plus `largest_gap = max|model−market|`. **Crucially
  `is_edge_validated` is always False:** a real reliability curve needs model
  P(exceed) scored against *realized market outcomes* accumulated over time,
  which the free archive cannot backfill (no historical price snapshots, and
  re-running the council per past day is infeasible). Rather than fabricate a
  curve we report the residual diagnostics and label the comparison NOT an edge
  — consistent with "never claim what you haven't measured." The realized-outcome
  curve is the future `storage`/`--verify` path. Verified live (London June 5):
  coverage 0.85 on held-out errors, `is_edge_validated=False`.
- [x] **C8. UI surface.** Model P vs de-vigged market P shown side by side, both
  CLI and web, **opt-in** so a normal run never hits Polymarket:
  - CLI: `run.py --market` fetches the matching market (sharing the run's
    `SafeHTTPClient` so it counts against the same request budget), matches by
    city+month/day (`compare.match_market`), and renders a "MARKET COMPARISON"
    ladder (model P, mkt P, Δ), verdict bucket + edge distance, modal buckets,
    overround, calibration line, settlement-bias caveat, and an explicit "NOT a
    validated edge" footer. Also emitted under `market_comparison` in `--json`.
  - Web: `server.py` honours `?market=1`; `index.html` adds a "Compare to
    Polymarket" checkbox and a comparison card (per-bucket model-vs-market bars,
    Δ colouring, calibration, the same NOT-an-edge warning). Verified end-to-end
    live (London June 5): model & market distributions both sum to 1.0, modal
    19°C for both, largest gap 24.7 pts at the 18°C bucket.
- [ ] **C9. WebSocket live prices (deferred — deliberate).** Real endpoint is
  `wss://ws-subscriptions-clob.polymarket.com/ws/market` (the tutorial's
  `ws://subscriptions.clob.polymarket.com` is wrong *and* insecure — plaintext).
  Deferred on two grounds: (1) **security** — the sandbox (`security.py`) is
  HTTPS-request-only with an SSRF guard, byte cap, and per-run request ceiling;
  a hand-rolled long-lived WebSocket would bypass every one of those controls,
  which violates the project's sandbox principle. (2) **need** — the comparison
  is a point-in-time snapshot beside a backtested verdict; a streaming book adds
  no accuracy, only attack surface. The one-shot Gamma fetch already covers the
  use case. Revisit only if a live-updating UI is ever required, and only behind
  an equally-strict WS guard.

### Explicitly out of scope (hard NO)
Placing/canceling orders, signing transactions, wallet/private-key handling,
fund movement of any kind. These are prohibited financial actions and are not
part of this project under any interpretation.
