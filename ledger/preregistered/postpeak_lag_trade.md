# Pre-registration — POST-PEAK SETTLEMENT-LAG TRADE, Singapore (frozen before scoring)

*2026-07-13. Driver-first market-microstructure candidate (docs/DRIVER_AUDIT.md conventions;
harness-optimizer MANUAL discipline). ONE attempt on the recorded snapshot history; the
probe script is `reports/backtest_postpeak_lag.py` and may not be edited after its numbers
are first read. Recommend-only: no served number, no live orders, nothing deployed under
any outcome. Guards run: improvement_analyzer (D18 keyword match = false positive — different
mechanism/object, documented here), dead-ledger grep (no market/microstructure entries;
paper_pnl measured DAY-AHEAD modal bets — a different trade).*

## Driver (named at true strength)
Information latency between the PUBLIC WU/IEM settling record — which this system reads
mechanically all afternoon — and thin prediction-market pricing. After Singapore's peak has
demonstrably passed (declining state, certified replay hit-rates 15:00 ≈ .934 / 16:00 ≈
.975), the running-max bucket's true probability is high; if the market's ask still prices
it materially lower, buying it collects the difference. This is a DATA advantage (systematic
settlement-surface reads) plus STRUCTURE OTHERS AVOID (books too thin for institutions).
**Confound named:** the ask may be low BECAUSE the book is stale/empty — an untradeable
mirage (the paper_pnl thin-quote artifact class). Hence fills at the RECORDED ask only,
and untradeable snapshots are counted, not skipped silently.
**Kill on the driver:** the (certified-probability − ask) gap series closing toward zero.
**Regime:** post-peak afternoon (SGT ≥ 15:00, declining), boundary-distance dependent.

## Frozen design
- Universe: Singapore `market_snapshots`, lead-0 (issued_at SGT date == target_date),
  settled (pm_resolved_label; realized_high bucket as fallback), targets ≤ 2026-07-12.
- LEAK-FREE state at issue: from `data/wsss_hourly_iem.jsonl`, obs with hh ≤ SGT
  issue-hour only; day_state = the shipped 2-consecutive-reads rule; running-max bucket =
  round-half-up °C.
- Entry predicate: SGT hour ≥ 15.0 AND day_state == "declining". ONE trade per day — the
  FIRST qualifying snapshot; no re-entry, no exit (binary settles).
- Fill: BUY the running-max bucket at its RECORDED best_ask (must exist, 0 < ask ≤ 0.97).
  No ask → UNTRADEABLE (counted). Return per unit staked: win (1−ask)/ask, lose −1.
  Fees: Polymarket trading fee 0; the vig is paid by crossing to the ask.
- Capacity: report the bucket's recorded liquidity (USDC) distribution; no size claims
  beyond it.
- Driver series (reported regardless of verdict): per-trade gap = certified hit-rate at
  the entry hour (pinned crossover baseline: 15:00 .9338; ≥16:00 .9752) − ask.

## Frozen verdict bar (ALL required to PASS)
1. Eligible trade-days n ≥ 20, else **ACCRUING — no verdict** (the honest thin-n output).
2. Pooled mean net return per unit > 0.
3. Mean net return > 0 on BOTH chronological halves (sign-stability).
4. Hit rate > mean ask (beats the price actually paid, not the mid).
5. Untradeable rate < 50% (else the edge exists only where it cannot be bought).
6. Median recorded bucket liquidity ≥ $50 (else capacity ≈ 0 and the result is a toy).
FAIL any of 2–6 at n ≥ 20 → dead-ledger **D20**, greps registered. PASS → recommend-only
forward paper ledger (the funding_forward pattern) BEFORE any capital discussion —
promotion never flows from this backtest alone.

## Honest priors (stated before scoring)
>95% of such hypotheses die. This repo's own ledger: day-ahead direction dead (44%=44%);
prior paper-P&L "profit" was a thin-price artifact; the market was RIGHT on the London
07-12 coin-flip. The most likely failure modes here: (a) untradeable mirage (no real ask
post-peak), (b) the market already reprices by 15:00 (gap ≈ 0), (c) n too thin. Expected
outcome: ACCRUING or FAIL.

## INTERIM — first scoring 2026-07-13: ACCRUING (n=9 < 20; no verdict permitted)

Facts, not verdicts: predicate days 9 (holding-skips 12). The running-max bucket settled
correctly on 9/9 — the near-lock condition is as strong live as certified. BUT 6/9 were
UNTRADEABLE (no ask on the record) — prior failure mode (a), the mirage confound, is the
DOMINANT phenomenon: sellers exit once the bucket is decided; the market is not asleep,
it is one-sided. The 3 fills (asks .96/.95/.91) all won: +4.2/+5.3/+9.9 per unit — and
the driver gap when tradeable was only +1.5 to +2.5 CENTS (the market had already
repriced to within ~2pts of certified probability wherever an ask existed). Recorded
bucket liquidity on filled days: $345–533. The design and criteria stay FROZEN; the
probe re-runs only as recorded days accrue past n=20 (~4–6 weeks at current density —
the accumulate automation logs the needed snapshots daily; nothing to build). One-attempt
applies to the design, not to waiting for n.
