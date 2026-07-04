# Pre-registration — P2: predictor-conditioned remaining-rise (FROZEN before scoring)

*2026-07-04. The plan's Phase-2 experiment, per PLAN_OWN_FORECAST.md. ONE attempt; failure
closes as D15. This file is written BEFORE the probe is scored; the commit hash is the freeze.*

## Hypothesis
The intraday remaining-rise distribution at hour H, conditioned on (day-state@H × one frozen
morning predictor cell), beats the UNCONDITIONAL lever at the pre-peak hours where headroom
exists (12:00–14:00) — the literature basis being Taillardat (gains come from predictors) and
SINGV (peak-timing is the local weakness). D13 (level-kernel) is dead; D8/D11 (slope/weather
proxies at small n) are dead; this differs by 10-year depth + the fold-stable state split +
leak-free predictor bins, and gets exactly one shot.

## Frozen design
- Data: `data/wsss_training.jsonl` + `data/wsss_hourly_iem.jsonl` (training grain, whole-°C
  round-half-up quantizer). Walk-forward: warmup = first 400 days; every later day scored with
  strictly-earlier days only.
- Conditioning cells at hour H ∈ {12, 13, 14}: state@H (holding/declining) × cloud_8_13
  TERCILE, tercile thresholds computed ONCE from the 400-day warmup block only (leak-free by
  precedence). Cell min-n = 30 strictly-earlier days, else that day falls back to unconditional
  (recorded).
- Baseline: the current unconditional remaining-rise resample at the same H, same quantizer.
- Scores: exact-bucket hit of the modal, AND discrete ranked probability score (RPS) of the
  bucket pmf. Both.

## Frozen gate (all four required)
1. Conditional ≥ unconditional on BOTH scores at EVERY H ∈ {12, 13, 14};
2. hit-rate improvement ≥ 2.0 points pooled 12–14 (noise floor: SE ≈ 0.9pt at n≈3200);
3. sign-stable on BOTH chronological halves (no fold flips, any H, either score);
4. NO regression at 15:00 or 16:00 (hit within 0.5pt of unconditional).

## Outcomes (pre-committed)
- CLEARS → recommend-only artifact: a written recommendation + the probe output. It still may
  NOT touch the served lever until (a) replicated on the WU settlement-grain 3-year set and
  (b) passed the frozen-A/B serving gate with a documented breakpoint. Two more gates, stated now.
- FAILS any criterion → **D15** in dead_candidates.jsonl, greps registered, no relitigation;
  the unconditional lever stands and the plan's P2 lane closes.
