# Pre-registration — band extension to cover the market's modal bucket (ACCRUING; frozen before scoring)

*2026-07-14. Registered off the SF specimen of that day: the served day-ahead band 28–30°C
(82%) printed one line above a cross-check that itself said "the independent signals agree
on 26°C; the COUNCIL (29) is the OUTLIER" — and WU settled 79°F = 26°C, a bucket the pmf
carried at 3.9% while the market carried it at 86.5%. The 07-02 directive ("surface
divergent signals, widen the band toward them") was implemented as PROSE (the cross-check
line) but not as MECHANISM (the band is pmf-top-k only, run.py `_bucket_call`,
`_SPAN_TARGET = 0.80`). n=1, means nothing — hence this file, not a hand-widened band.*

**Analyzer/dead-ledger check (per the per-named-candidate law):** improvement_analyzer
matched D14 on the keyword "band" — D14 killed a Singapore band-NARROWING design
(two-bucket cool-skew). This candidate is the opposite lever (cover an external signal's
bucket) and does not inherit or relitigate D14; the served 3-bucket pmf band stays the
incumbent throughout. Consensus-override anti-directive respected: the MODAL is never
touched, the pmf is never touched — only the displayed BAND may gain one bucket.

## Driver-first statement
- **Driver:** the market aggregates forecaster information beyond our 8-center panel
  (marine-layer/mesoscale reads, HRRR-class models we don't consume, position-backed
  locals). Already MEASURED at real n: market bucket-hit 43% vs council 40% (n=41+ per
  city scorecards) — the driver is real and small.
- **Why this surface:** when the market's modal falls INSIDE our band, both agree and the
  band already covers it. The only place the driver can add anything is the rare day the
  market modal falls OUTSIDE — exactly the day the band's self-assessed % is most suspect
  (measured coverage 73–74.5% vs printed ~80–82%).
- **Kill condition ON THE DRIVER:** if, on conditioned days, settle == market-modal no more
  often than the model pmf assigned that bucket (i.e. the market adds no information
  precisely where it diverges), the driver is dead here regardless of coverage arithmetic.
- **Regime:** day-ahead only (the intraday lock overrides the band same-day and is the
  standing resolver); all snapshot cities pooled, per-city reported.

## Frozen design (one attempt at the floor; nothing scored before it)
- Universe: earliest snapshot per (place, target_date) in `market_snapshots` with
  issued_at date < target_date (day-ahead), a settled label (pm_resolved_label or
  realized_label), and both model_prob and market_prob present in buckets_json.
- Reconstruct the served span EXACTLY as run.py `_bucket_call`: top-k model_prob buckets,
  descending, until cumulative ≥ 0.80.
- **Conditioned day:** market modal bucket (max market_prob) ∉ span.
- **Candidate:** span′ = span ∪ {market modal} — DISPLAY band only; modal, pmf, and all
  probabilities untouched.
- Scored on conditioned days only, chronological:
  1. settle ∈ span′ \ span (the added bucket HITS) on ≥ 25% of conditioned days — the
     width cost bar: +1 bucket must pay at least a typical band-bucket's share;
  2. driver kill-check: pooled hit rate on the added buckets ≥ 2× the pooled model_prob
     those buckets carried (the market must beat the pmf where they disagree, not tie it);
  3. sign-stable: criterion-1 rate positive on BOTH chronological halves of the
     conditioned set;
  4. **n ≥ 15 conditioned days pooled across ≥ 2 cities — else ACCRUING, no verdict.**
- FAIL at the floor → dead ledger **D27**, and the labeling flag (below) remains the
  permanent honest treatment. PASS → implement span′ in `_bucket_call` with KATs, stamp
  CERTIFIED.

## Status 2026-07-14 — ACCRUING (design input counted; ZERO outcomes read)
46 day-ahead settled snapshots exist; **conditioned n = 3** (Manila 1, Singapore 1,
San Francisco 1 — the 07-14 specimen). Settle outcomes on those 3 days were NOT read.
The condition fires on ~6.5% of snapshot days; the daily automation snapshots 3 cities,
so the n=15 floor is months out. Cheapest-decisive-test law satisfied: the historical
data was exhausted FIRST and cannot carry a verdict — this file is the forward clock.

## Shipped now, gateless (rule 2 — labeling/honesty, no probability touched)
run.py `_band_market_flag` + `_market_modal_c` (extracted from the cross-check's °F→°C
conversion): when the market modal sits outside the served band, the band line gains an
explicit flag quoting the divergent bucket and the MEASURED band coverage (73–74.5%)
beside the pmf-self-assessed %. KAT: tests/test_band_market_flag.py. This label is not
the extension and licenses nothing.
