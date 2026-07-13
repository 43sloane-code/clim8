# Pre-registration — Polymarket trade-tape KILL test, five cities (frozen before scoring)

*2026-07-14. The Kalshi-S2a schema applied to Polymarket (user-approved host
data-api.polymarket.com): a HISTORICAL distribution answer to the question the five
frozen ask-fill post-peak preregs are accruing toward on calendar time. PARALLEL and
COMPLEMENTARY — this tests what TRADED (executed tapes); those test what was QUOTABLE
(recorded asks at snapshot instants). They are never merged, and those preregs continue
untouched. ONE attempt: design and criteria frozen here; chunked/resumed fetching is
pure IO and allowed. Probe: `reports/backtest_polymarket_tape.py`. Recommend-only.*

## Frozen design
- **Universe:** the distinct settled (place, target_date) market-days already recorded in
  our own `market_snapshots` ledger (~89 across Singapore, London, Karachi, Jeddah, San
  Francisco) — a point-in-time list, not a fresh enumeration (no selection after the fact).
- **Resolution to venue ids:** event slug derived from the recorded market_title
  (lowercase, strip punctuation, spaces→hyphens; verified live: "Highest temperature in
  London on July 12?" → highest-temperature-in-london-on-july-12). Failures counted.
- **Winner market:** the gamma event's bucket market whose settled outcomePrices give
  Yes = 1; CROSS-CHECKED against our recorded settle (pm_resolved_label / realized
  bucket) — mismatch → flagged, excluded, counted.
- **Tape:** data-api /trades by the winner's conditionId, paginated. Yes-equivalent
  price per trade: price if outcome=="Yes" else 1 − price.
- **Afternoon window (per city, the frozen entry hours of the ask-fill preregs):**
  local 15:00 Singapore/Karachi/Jeddah/SF, 16:00 London → end of local day; DST-aware.
- **Per-day statistic:** volume(size)-weighted yes-equivalent price over the window's
  trades; gap_day = 1 − vw. **Cost floor (frozen):** Polymarket trading fees are zero,
  so the de-minimis threshold is 0.01 (one cent — comparable to Kalshi's fee-derived
  ~0.6–1.0¢ at favorite prices). killable_day := gap_day ≤ 0.01.

## Frozen verdict order
1. **ABORT (unscored, not a kill):** slug-resolution failures > 20% of the universe, OR
   empty afternoon tapes on > 30% of resolved days where our snapshots show the market
   traded — the API's history depth is insufficient; record and stop.
2. **n floor:** scored days < 60 → ACCRUING/no-verdict.
3. **Kill:** killable on ≥ 80% of scored days → the post-peak residue does not exist in
   the EXECUTED record either → dead-ledger entry **D25**, and the five ask-fill preregs
   inherit a hostile prior (they stay open — designs differ — but any future survival
   must explain why quotable ≠ traded).
4. Otherwise: report the full gap distribution (deciles, per-city, era halves) as the
   historical upper bound; the ask-fill preregs remain the tradability instruments.

## Asymmetry clause (identical to Kalshi S2a)
Hindsight-winner, executed-trade design — an UPPER BOUND on opportunity (the vw includes
trades made before the outcome was knowable, and executed prices survive only where a
counterparty existed). Survival is permission to keep testing, never tradability.
