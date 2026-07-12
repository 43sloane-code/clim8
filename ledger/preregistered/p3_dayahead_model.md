# Pre-registration — P3: predictor-powered day-ahead distributional model (FROZEN before code)

*2026-07-04. The plan's Phase-3 experiment (PLAN_OWN_FORECAST.md). Literature basis now
partially VERIFIED by direct source reads: EUPPBench 24h T2m raw 1.21K → EMOS 0.82K →
DRN/BQN 0.67K, NN-over-EMOS gap 18%@24h → 5%@120h (arXiv 2309.04452 Table 4); Rasp & Lerch
t2m 48h raw 1.16 → EMOS-loc-bst 0.85/0.80 → NN 0.82/0.78, i.e. ~3% over boosted EMOS / ~29%
over raw (ar5iv 1805.09091 Table 2). The MNWC XGBoost −24–29% figure remains CITED-UNVERIFIED
(source pinned: Hieta 2025 Met. Apps 10.1002/met.70074; paywalled) — it is an intraday claim
and nothing in P3 leans on it. Expectations stated up front: our council is already EMOS-class,
so the literature's remaining headroom here is the predictor-powered ~3–19% CRPS step, which at
our σ is a few bucket-hit points — near the noise floor. The likely honest outcome is closure.*

## Hypothesis
A distributional model of NEXT-day WSSS Tmax, conditioned on a Taillardat-style predictor set
(today's observed structure + today's ERA5-grade aggregates + seasonality), beats (Stage A)
climatology AND persistence on 10 years out-of-sample, and (Stage B) the SERVED council pmf on
the live overlap window. Taillardat's finding — the gain is the predictors, not the learner —
sets the design: the simplest learner that can carry predictors.

## Frozen design
- Data: `data/wsss_training.jsonl` (3,649 days). Target: day t+1 `tmax_c` (training grain,
  round-half-up °C buckets). Predictors, all knowable by end of day t: prev_tmax (=day t
  tmax), prev_peak_hh, doy_sin/cos, cloud_8_13, sw_8_13, precip_0_13, wind_11_14 of day t.
- Learner (fixed in advance, stdlib): k-nearest-analog quantile ensemble — standardized
  predictor space, k=150, distance-weighted; the predicted distribution is the analog days'
  NEXT-day Tmax values resampled through the quantizer. (Chosen because it IS the predictor-
  carrying minimal learner; no GBM unless this clears — same simple-first rule P2 used.)
- Walk-forward: warmup 400 days; standardization stats and analog pool from strictly-earlier
  days only. Scores: CRPS of the continuous predictive AND exact-bucket hit of the modal.
- **Stage A gate (screen, on the 10y table):** beats BOTH baselines — (a) day-of-year
  climatology (±15-day window over prior years), (b) persistence-plus-climatological-delta —
  on BOTH scores on BOTH chronological halves. Fail any cell → **D17**, P3 closed, footnote
  recorded ("literature deltas do not reproduce at single-station n").
- **Stage B gate (economic, only if A clears):** on the settled council overlap window
  (verdicts.db), model pmf vs served council pmf: CRPS delta beats the ~0.1 run-to-run noise
  floor AND bucket-hit delta ≥ +4pt, both halves, leak-free. Fail → **D17** with Stage-A
  result recorded as "beats climatology, not the council" (real but not deployable).
- Stage A uses ERA5 aggregates for day-t predictors: knowable-by-end-of-day-t in principle,
  but cleaner than any live feed — so Stage A is an OPTIMISTIC upper bound by construction.
  A Stage-A pass therefore proves nothing servable; only Stage B (and later a live-feed
  forward ledger like P2b's) can. A Stage-A fail, however, kills honestly — if the clean
  version can't beat climatology, the live version won't.
- One attempt per stage. No predictor re-selection, no k re-tuning after seeing scores.

## Outcomes (pre-committed)
- A fails → D17. A clears, B fails → D17 (with the honest split recorded). Both clear →
  recommend-only shadow member (`tracked_forecasts source='own'`, P4), promotion only via the
  same 40-pair gate as TWC. Nothing served changes under any outcome of this prereg.

## Driver adjudication — ADDED 2026-07-12 (pre-completion, Stage-B carve-out clock at
## n=6/40; criteria above UNTOUCHED — this governs attribution AFTER a pass, not the pass)

Driver-first audit (docs/DRIVER_AUDIT.md): Stage A proved the PREDICTOR driver real vs
naive baselines; the Stage-B re-gate's remaining question is pure REDUNDANCY — the own
model sees today's local state, the council members embed tomorrow's assimilated physics,
so a genuine gain must live in the thin slice where next-day structure is predictable
from local today-observables BEYOND what NWP assimilated. IF the re-gate passes at n≥40
(model frozen at 4bf504b, no re-tuning), BEFORE any P4 shadow promotion:
1. **Attribution strata:** report the model-vs-council gain stratified by (a) council-vs-
   own divergence tercile and (b) predictor-signal days (high cloud_8_13 / unusual
   prev_peak_hh) vs quiet days. The driver predicts the gain CONCENTRATES in (b)-active,
   high-divergence cells; a uniform gain at n=40 is more consistent with noise.
2. **Independence:** own-model error vs council error correlation (the TWC G4 convention).
   Errors ~spanned by the council = no new information, whatever the score says.
3. A pass whose gain cannot be attributed per 1–2 promotes (if at all) labeled
   "driver UNRESOLVED", never "predictor driver confirmed".
