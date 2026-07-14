# Pre-registration — per-day member-dispersion CONDITIONAL residual cloud (frozen before scoring)

*2026-07-15. The one live day-ahead DISTRIBUTION-precision lead. The machine's own
recommend-only monitor (weather_council/calibration.conditional_spread_eval, wired at
council.py:1928) fired RECOMMEND on the 07-14 San Francisco run: held-out CRPS 0.444 vs
0.461 (+3.6%, +2.4σ past noise, disp↔|err| r=+0.39, n=92), independently corroborated by
the spread–skill check (r=+0.40, RELIABLE). The module's own docstring records that the
basket cities have historically DECLINED ("member dispersion doesn't track error well
enough") — that live-vs-history tension is exactly what this frozen probe adjudicates.
ONE attempt; probe `reports/backtest_conditional_dispersion.py`; FAIL → dead ledger
**D28**; PASS → ship the conditional cloud into the serving path (compare) with KATs and
stamp CERTIFIED.*

## Ledger adjacency (checked per the analyzer law; none binds)
- **D26** (scalar variance-match inflation): killed the SCALAR/recency form only; its
  autopsy explicitly permits "a future CONDITIONAL design, which would need its own
  registration." This is that registration.
- **D11** (intraday convective-cap conditioner — the analyzer's keyword match): killed
  conditioning the INTRADAY remaining-rise on ERA5 weather covariates; this candidate
  conditions the DAY-AHEAD cloud width on a council-internal measured quantity. Different
  surface, different covariate, different failure mode.
- **D07/D08** (window artifacts): this design has NO tuned window — the covariate is the
  same day's measured panel dispersion; nothing is swept.

## Driver-first statement
- **Driver:** flow-dependent predictability — the bias-corrected member panel's
  disagreement on a day measures that day's true forecast uncertainty (synoptic
  toss-ups scatter the centers; locked patterns collapse them). Measured, not assumed:
  SF disp↔|err| r=+0.39 (n=92); spread–skill reliability r=+0.40 over 5 bins (n=102).
- **Kill condition ON THE DRIVER:** disp↔|err| Pearson r < +0.10 on a basket city's
  frozen stream (the module's own MIN_DISP_CORR floor) — the driver series itself
  announces death there, regardless of CRPS luck.
- **Regime:** day-ahead, both attributes, basket cities (the serving surface
  compare.py dresses is shared). SF's live RECOMMEND is MOTIVATING evidence only — SF
  is on-demand with no healthcheck stream and does not gate.

## Frozen design (nothing here changes after the first scored number)
- **Data:** the healthcheck backtest streams for the two monitored basket cities
  (Manila, Singapore) via `daily_healthcheck._city_votes` on the live variant
  (CURRENT_BIAS, CURRENT_POWER) — the same frozen loader idiom as the D26 probe.
- **Pairs:** per test day (dates[WARMUP:]), per attribute (high, then low — the
  council's own calib_pairs order), r = obs − blend and disp = pstdev(bias-corrected
  member panel) (0.0 if <2 members), pooled high+low in day order — replicating
  council.py's calib_pairs object exactly.
- **Candidate:** `calibration._conditional_cloud` VERBATIM (standardise strictly-earlier
  residuals by their own day's dispersion, re-scale by today's, centre preserved;
  fallback to the incumbent cloud when disp ≤ DISP_EPS or usable priors < WARMUP=10).
  No parameter is introduced or tuned by this probe.
- **Scoring:** leak-free walk-forward, day i dressed by pairs[:i] only; CRPS via
  `scoring.crps_sample`; 80% interval coverage via `scoring.interval_coverage` on the
  SAME prior/conditional clouds.

## Frozen pass criteria (ALL required)
1. **Module gate, per city:** `conditional_spread_eval` on the full pooled stream
   returns recommend=True under its own frozen constants (improvement > 0, z ≥ 2.0,
   disp_corr ≥ +0.10) for BOTH Manila and Singapore.
2. **Fold gate:** CRPS improvement (incumbent − conditional) > 0 on BOTH chronological
   halves of BOTH cities (four cells, split on scored days).
3. **Coverage guard:** the conditional cloud's walk-forward 80% coverage must not sit
   farther from 80% than the incumbent's by more than 1.0pt, per city (the standing
   under-coverage defect must not be worsened by day-to-day narrowing).
4. **n ≥ 60 scored days per city**, else ACCRUING (floor calibrated to the data that
   exists: ~102 pooled pairs/city → ~92 scored, the D26-lesson arithmetic).
- FAIL → **D28**, and the recommend-only monitor line remains the honest treatment.
- PASS → implement in the serving path (compare dresses the conditional cloud), KATs
  pinning: centre preservation, DISP_EPS/warmup fallback to incumbent, and the
  recommend line switching to "SERVED"; stamp CERTIFIED.

## OUTCOME 2026-07-15 — FAIL → D28 (one attempt spent)

Manila: CRPS 0.531 vs 0.530 (−0.3%, −0.2σ, disp_corr +0.35, n=212) — module gate
declines; halves +0.0016/−0.0048 fold-unstable; coverage 74.1→86.3 (overshoots 80).
Singapore: CRPS 0.492 vs 0.490 (−0.4%, −0.5σ, disp_corr +0.15, n=212) — declines;
halves −0.0039/−0.0000; coverage 74.1→78.3. **Criteria 1 and 2 FAIL on both cities.**

Autopsy (full text in dead ledger D28): the DRIVER survives its own kill condition on
both cities (r=+0.35/+0.15 ≥ 0.10) but the extraction loses CRPS — driver real,
extraction dead, the VRP shape. The Manila coverage overshoot shows the mechanism
over-widens exactly where dispersion is noisiest; CRPS caught it. SF's live RECOMMEND
is city-specific and licenses nothing (on-demand, no serving stream, out of gate by
this file's own frozen scope). The recommend-only monitor line remains the honest
treatment; day-ahead distribution precision now has NO open probe — the honest posture
is the standing flags plus the TWC and band-cover clocks.

## What this does NOT claim (stated before scoring)
Scaling around the cloud centre leaves the modal bucket essentially unchanged — this is
a DISTRIBUTION-precision lever (CRPS, interval honesty, bucket-tail mass), not a
bucket-hit lever. Day-ahead point/bucket-hit remains σ-ceiling-bound (0/17 dead; the
TWC 9th-member clock is the only live point lever, n=28/40).
