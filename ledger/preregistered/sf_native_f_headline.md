# Pre-registration — SF native-°F HEADLINE bucket pmf (sf_verdict_blockers #4)

## RESULT — FAILED THE GATE (2026-07-12, one attempt spent → dead ledger D19)

Probe run once, criteria as frozen below, 3,628 eligible walk-forward days (grain sanity
3629/3649 = 0.995 integral-°F):

| Criterion | H1 (n=1814) | H2 (n=1814) | Verdict |
|---|---|---|---|
| C1 modal °F-bucket hit, °F vs °C-derived | .099 > .080 PASS | .092 < .116 **FAIL** | not sign-stable |
| C2 mean log score, °F vs °C-derived | −3.372 < −3.159 **FAIL** | −3.314 < −3.165 **FAIL** | fails both |
| C3 80%-set coverage ∈ [.70,.90] | .766 PASS | .777 PASS | honest |

**The °C headline is not the defect it looked like.** At day-ahead σ (~4°F cloud) with
n≈160 residuals, a whole-°F empirical pmf over-fits bin noise across ~15 buckets; the °C
bucketing acts as an accidental regularizer (2°F bins, mass split). The naive native-°F
headline SERVES WORSE °F answers than the current °C pmf read with a uniform split.
Disposition: headline stays °C; SF stays on-demand, out of the basket. Any future °F
headline is a NEW mechanism (smoothed/shrunk density, its own pre-registration) and must
beat the °C-split baseline. The intraday °F lever (post-peak, σ collapsed — fine grain
wins there) and the °F settlement-reference rendering (#3) are unaffected.

*2026-07-12. FROZEN BEFORE the probe is scored. One attempt (HARD RULE 1): any criterion
failing on either half → dead-ledger entry, the headline stays °C, SF stays out of the
basket, and this file records the failure. The probe script is
`reports/backtest_sf_native_f.py`; it may not be edited after its numbers are first read.*

## The change under test (served probability — hence this gate)

`run.py _bucket_call` builds the day-ahead HEADLINE pmf by quantizing the residual cloud
(council point + held-out residuals) in hardcoded whole-°C. San Francisco's market settles
whole-°F (2°F Polymarket buckets): a °C bucket spans ~2 °F buckets, so the served headline
cannot even name a settlement outcome. Proposed: the SAME cloud, the SAME construction,
quantized through the EXISTING settlement quantizer `_native_reading_int(·, grain, sub)` in
the CITY'S OWN GRAIN ("F" for San Francisco only). No new information, no new parameter —
a grain-correctness change to a served probability.

## Probe design (leak-free, deterministic, stdlib)

- **Data:** `data/ksfo_hourly_iem.jsonl` (3,649 local days, obs as [frac_hour, °C]).
  Daily high = max obs of the local day; settlement bucket = round-half-up of the day's
  °F high.
- **Grain sanity ABORT (not a pass):** ≥95% of daily °F highs must be integral within
  0.05°F (KSFO reports whole-°F natively). Below that the data grain is broken and the
  probe aborts unscored.
- **Leak-free cloud, mirroring run.py's construction:** for each day t, point = previous
  day's high (persistence — zero-parameter, exists all 10y); residuals = (high − point)
  over the trailing 160 strictly-earlier days; day t is eligible iff ≥20 residuals.
  Deterministic full resample (no RNG).
- **Candidate** `pmf_F`: bucket(point + e) via round-half-up whole-°F, for each residual e.
- **Baseline** `pmf_C→F` (the information content of TODAY'S served °C headline, asked the
  settlement's °F question, read as favorably as possible): bucket the same cloud in
  whole-°C, then split each °C bucket's mass UNIFORMLY across the °F integers f with
  round_half_up((f−32)·5/9) = that °C bucket.
- **Halves:** eligible days split chronologically into first/second half.

## Frozen pass criteria — ALL must hold on BOTH halves

- **C1 (modal hit, the economic object):** exact settlement-°F-bucket hit rate of the
  `pmf_F` modal is STRICTLY GREATER than the `pmf_C→F` modal's on both halves.
- **C2 (whole-distribution):** mean log score ln p(realized °F bucket), probabilities
  floored at 1e-6, is greater for `pmf_F` than `pmf_C→F` on both halves.
- **C3 (honesty, not just sharpness):** `pmf_F`'s smallest credible set reaching ≥80%
  mass covers the realized bucket at a rate within [70%, 90%] on both halves.
- **C4 (identity regression, KAT-stage):** the grain-parameterized code path with
  grain="C" reproduces the current °C pmf EXACTLY for °C cities (asserted in the shipped
  KAT on EGLC-shaped inputs; any nonzero probability diff fails the ship).

## Ship spec (only if C1–C3 pass; C4 enforced by KAT)

1. Public helper `settlement_grain(place) -> "C"|"F"` in `weather_council/intraday_ceiling.py`
   (single source: the existing `_SETTLE_GRAIN`/`_city_key` maps — no second grain map).
2. `run.py _bucket_call`: quantize the day-ahead residual cloud and the day-ahead modal in
   the city's grain; rule label says the actual rule ("round-half-up / whole °F" for SF);
   span/pmf-top/day-ahead-vs-intraday comparisons all same-grain (fixes the latent °C-vs-°F
   integer comparison in the override note).
3. `_bucket_call_lines`: render in the native unit (the intraday grade path is already
   grain-aware).
4. KATs: SF °F headline end-to-end; °C city byte-identity (C4); span math in °F.
5. Stamp this file CERTIFIED with the probe numbers; update `sf_verdict_blockers.md` #4.

## What this does NOT do (scope guards)

- Does NOT touch the intraday ceiling pmf (already °F-native for SF via `_SETTLE_GRAIN`).
- Does NOT promote SF into CITIES/the basket (a separate decision after this ships).
- Does NOT change any °C city's served numbers (C4 is the proof obligation).
- The persistence proxy exists ONLY to compare quantizers on a real leak-free cloud; its
  absolute hit rate is NOT a claim about council skill and must not be quoted as one.
