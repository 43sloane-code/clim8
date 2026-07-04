# PLAN — Own forecast software for WSSS Tmax (research-grounded, gated, kill-criteria first)

*2026-07-04. Deep-research basis: 100-agent fan-out over primary sources (AMS MWR ×2, Copernicus
ESSD/EUPPBench, arXiv 2309.04452, BAMS review, SINGV evaluation, IEM/NOAA-ISD docs). CAVEAT
RECORDED: the adversarial-verification phase was rate-limited to 0 votes — every number below is
**cited-but-unverified**; the three load-bearing ones are re-verified before any Phase-3 code
(see §Verification). Scope: Singapore only. Nothing here touches a served number without the
frozen gate.*

## What the research says (the convergent picture)
1. **The skill ladder is consistent across three independent benchmarks:**
   raw ensemble → EMOS-class: **−18…−32% CRPS** (Taillardat/PEARP 36h: 1.221→0.804; EUPPBench
   24h: 1.21K→0.82K; ESSD: up to −50% at early leads). EMOS-class → NN/GBM distributional:
   **−3% (Rasp & Lerch vs boosted EMOS) to −19% (EUPPBench DRN/BQN 0.82→0.66K @24h)** — and the
   NN advantage **shrinks with lead** (19.5% @24h → 4.8% @120h), i.e. largest exactly at our lead.
2. **Taillardat's decisive detail: the ML gain is mostly EXTRA PREDICTORS, not the learner** —
   QRF fed only ensemble-temperature stats ties EMOS (~±2–3%); QRF with 40+ multi-variable
   predictors wins. Our residual cloud currently conditions on almost nothing.
3. **Intraday post-processing works:** XGBoost on an operational nowcast (MNWC) cuts T2m RMSE
   **24–29% vs direct NWP at 0–12h leads** (first-party repo, re-gridded via Gridpp).
4. **Singapore-specific target confirmed:** SINGV — the dedicated 1.5-km convection-permitting
   model — shows a **~1-hour diurnal PHASE DELAY + systematic warm bias** at 19 AWS. Peak
   *timing* is mis-modeled even by the best local NWP; tropics are structurally harder (sparse
   obs assimilation + convection representation; 3-h errors largest in the tropics). This is
   literature cover for exactly our measured failure modes (late peaks, holding-day tail).
5. **Data:** IEM ASOS is **global** (WSSS included), programmatic (`asos.py` CGI, 1 s/IP
   throttle), **synced every ~10 min** (usable as a second low-latency feed); NOAA ISD 1901–
   present, 35k stations; open stacks exist (IMPROVER, Himan, R crch) but a stdlib port of the
   *methods* fits this repo better than importing a framework.

## Where our system already sits on that ladder (honest placement)
The council (skill-weighted blend + gated bias correction + empirical residual cloud) is
approximately **EMOS-class local post-processing already** — which is why D01–D06 (more members,
more correctors) died: the −30% raw→EMOS step was already banked. The literature's remaining
day-ahead headroom for us is the **−3…−19% CRPS** step, POWERED BY PREDICTORS — worth roughly
σ 0.85→~0.75–0.80°C, i.e. **a few bucket-hit points at best**, near our noise floor. That is a
Phase-3 experiment with modest expectations, not a revolution. The intraday/peak-timing space
(§4 + our fold-stable holding/declining split + 51–80% conviction zone at 12:00–14:00) is the
higher-headroom, already-certified domain.

## Phases (each gated; later phases die freely)
- **P0 — DONE:** 3y settlement-grain WU dataset (1,095 days, `data/wsss_hourly.jsonl`);
  register feed; state labeling; liveness sentinel; certification ledgers.
- **P1 — Training archive (data only, no served change):** extend to **10y via IEM** (°C METAR
  grain — training/backtest use; settlement grain stays WU) + ERA5 hourly predictors
  (allowlisted archive API: cloud, shortwave, wind, RH) for the same window → one frozen
  training table `data/wsss_training.jsonl` (day × predictors × outcomes: Tmax, peak hour,
  late-climb flag). Deliverable is a FILE, not a model.
- **P2 — CLOSED AS D15 (2026-07-04, same day):** the frozen gate ruled — 12:00 +5.9pt fold-stable but 13:00 regressed both folds and pooled +1.5 < +2.0. See dead ledger D15. Original design: on 10y,
  learn P(remaining rise | hour, day-state, morning predictors) — analog/quantile first, GBM
  only if the simple version clears. GATE (pre-registered before scoring): beats the current
  unconditional lever at 12:00–14:00 on held-out CRPS **and** exact-bucket hit, sign-stable on
  BOTH chronological folds, post-peak hours not regressed; else **D15** and the unconditional
  lever stands. (D13 conditioned on max LEVEL and died; P2 conditions on state+predictors with
  10y depth — one attempt, then closed.)
- **P3 — Day-ahead distributional model (modest expectations, stated):** GBM/quantile
  regression with the Taillardat-style predictor set (ensemble spread, ERA5-derived indices,
  day-state climatology; TWC if its 40-pair gate passes) vs the served residual cloud. GATE:
  frozen-A/B (record/replay) + disjoint folds on CRPS + bucket-hit past the ~4pt noise floor.
  KILL CRITERIA: if the gain is under the noise floor on either fold — expected outcome per
  our own D01–D06 history — it closes, and the literature's percentages get a local footnote:
  "does not reproduce at n available on one station."
- **P4 — Shadow member:** any survivor serves as `tracked_forecasts source='own'` (like TWC),
  earning a live record; promotion only through the same 40-pair machinery. Never asserted.

## Verification debts (named, not hidden)
- Re-verify the 3 load-bearing numbers before P3 coding: EUPPBench 24h ladder (1.21→0.82→0.66K),
  Rasp & Lerch ladder (1.16→0.85→0.82), XGBoost nowcast −24–29% — via the workflow's verify
  phase re-run after the rate-limit reset, or direct source reads.
- IEM 1 s/IP throttle honored in P1 (yearly chunks, sequential).
- All P2/P3 numbers remain "backtest, uncertified" in the eval-harness vocabulary until their
  own ledgers fill — a fix is a hypothesis; only consistency upgrades it.
