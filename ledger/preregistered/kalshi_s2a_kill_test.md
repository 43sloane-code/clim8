# Pre-registration — S2a: Kalshi SF historical KILL test (frozen before scoring)

*2026-07-14. Executes the reordered S2a of kalshi_expansion.md under the
distribution-over-verdicts law: the cheapest decisive test, on ~180 settled market-days of
KXHIGHTSFO trade history, run BEFORE any forward logger is built. ONE attempt: the design
and criteria below may not change after the probe's numbers are first read (chunked/resumed
FETCHING is allowed — pure IO). Probe: `reports/backtest_kalshi_s2a.py`. Recommend-only;
no served number; no trading under any outcome.*

## Truth sources (both pinned, cross-checked)
- Settlement: each market's own `result` field (Kalshi's settlement).
- Cross-check: the IEM parsed-CLI archive (`/json/cli.py?station=KSFO&year=YYYY`) —
  VERIFIED 2026-07-14 against the live CLISFO product (07-12: high 76, 3:46 PM, SAN
  FRANCISCO INTL, WFO MTR; 193 rows for 2026; every row links its raw NWS product).
  A day where Kalshi's settled bucket disagrees with the CLI high is FLAGGED, excluded
  from scoring, and counted (a seam specimen, not noise).

## Frozen design
- Universe: ALL settled KXHIGHTSFO events (~180 days, 26JAN14 → 26JUL12).
- Winner market per day: `result == "yes"` (exactly one per event; days violating that
  are flagged+excluded+counted).
- Afternoon window: local America/Los_Angeles 15:00 (the certified declining@15:00≈96%
  hour) → market close, DST-aware.
- Per-day statistic: the winner's VOLUME-WEIGHTED yes-price over the window's trades
  (`yes_price_dollars`, else 1 − no_price). gap_day = 1 − vw_yes.
  cost_day = 0.07 · vw_yes · (1 − vw_yes) — Kalshi's published taker-fee form (exact
  formula; per-contract rounding ignored, which is PRO-survival, i.e. conservative for a
  kill test's purpose of killing).
- Days with ZERO afternoon trades in the winner: gap-unobservable — reported separately,
  never imputed.

## Frozen verdict order
1. **Illiquidity kill:** zero-afternoon-trade days > 50% of the universe → **EXPANSION
   DEAD** (nothing to trade at the hour that matters), dead-ledger entry.
2. **n floor:** scored (trade-bearing) days < 100 → ACCRUING/no-verdict (state it; the
   forward logger decision returns to the user).
3. **The registered kill:** gap_day ≤ cost_day on ≥ 80% of scored days → **EXPANSION
   DEAD** (the deep book prices the afternoon to within costs; the venue-depth
   hypothesis is false), dead-ledger entry.
4. Otherwise → SURVIVES S2a: report the full gap distribution (deciles, by-month halves
   for era stability) and proceed to S2b (forward dual-venue logger) per the expansion.

## The asymmetry clause (read before quoting any survival)
This test uses the HINDSIGHT winner — the strongest possible version of the opportunity —
so it is a NECESSARY-condition test built to KILL. Survival does NOT establish
tradability: the honest tradable version (entry conditioned on the leak-free running-max
state at trade time, IEM archive) is S3's separate, own-prereg probe, permitted only if
this survives. A pass here is permission to keep testing, nothing more.

## INTERIM — first scoring 2026-07-14: ACCRUING (n=67 < 100 floor; no verdict permitted)

**Universe truth discovered:** the public API retains settled markets only ~67 days back
(180 settled events exist; 113 older ones return ZERO markets by every route — a retention
window, verified at series level: 402 settled markets = 67 event-days). The accessible
history IS 67 days, and it ERODES DAILY — the trade cache banked by this probe
(reports/streams/kalshi_s2a_cache.jsonl) is irreplaceable point-in-time data.

**Facts on the 67 days (facts, not verdict):** kill-rate 0.0% vs the ≥80% bar (not one
day priced the afternoon winner to within costs); gap deciles 10/25/50/75/90 =
1.0/1.2/2.9/10.3/23.0¢, mean 8.8¢; era halves 6.9¢/10.6¢ (not decaying); 67/67 days had
afternoon winner trades (the Polymarket no-ask mirage is ABSENT); median afternoon winner
volume 5,342 contracts; CLI cross-check mismatches: 0/67.

**Asymmetry reminder (frozen above, restated):** hindsight-winner design — these gaps are
an UPPER BOUND on opportunity; the vw includes trades made before the outcome was
knowable. Tradability is S3's running-max-conditioned question, on these same 67 days'
tapes, under its own prereg.

**Per the frozen verdict order:** ACCRUING; the S2b forward-logger decision returns to
the user — with the note that S2b now serves double duty: it accrues toward the n=100
floor AND arrests the daily erosion of the only history that exists.

**Probe IO repairs during fetching (design untouched):** nested-market results absent on
older events (re-resolved via /markets, then /markets?status=settled, then the series
listing — all zero for pre-May events, confirming retention, not a bug); the completeness
check initially treated refetchable flags as final (fixed before any verdict was read).
