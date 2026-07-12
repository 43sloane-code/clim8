# NWP literature → this system: what's implemented, what's dead at our grain, what's wrong in the textbook summary

*2026-07-12. Source under review: Haupt, Jiménez, Lee & Kosovic, "Introduction to Meteorology
for Renewable Energy Forecasting" (in Kariniotakis ed., Renewable Energy Forecasting), plus a
primitive-equations summary. Purpose: pin each textbook concept to its disposition HERE, with
the load-bearing citations, so no future session relitigates a dead lever from textbook
enthusiasm. This system is a CONSUMER of NWP (council of ECMWF/GFS/ICON + GEFS/ICON-EPS/GEPS
ensembles) predicting whole-degree settlement buckets — the economic grain every claim below
is measured at.*

## 1. Already implemented (concept → where → certification)

| Chapter concept | Here | Status |
|---|---|---|
| Multi-model consensus (Ebert 2001 "poor man's ensemble"; Mahoney et al. 2012) | The council: 3 deterministic + 3 ensemble systems, gated bias correction, lineage blend | Live; member set FROZEN by D02 (below) |
| MOS bias removal (Glahn & Lowry 1972) | `applied_bias_correction`, residual clouds, walk-forward gated | Certified path |
| Spread–skill consistency (Leutbecher & Palmer 2008; Fortin et al. 2014) | `weather_council/spread_skill.py` → SPREAD–SKILL CHECK render | Served, recommend-only; includes the √N averaging factor (raw member spread overstates blend error) |
| Flow-dependent uncertainty width | Conditional-spread recommender in render (CRPS vs incumbent, z-scored) + `reports/backtest_cloud_scale.py` | Probed: **SHIPS=False both cities**; live recommender currently agrees ("no change") |
| Ensemble calibration checks (rank histogram; PIT; Gneiting, Balabdaoui & Raftery 2007 calibration+sharpness) | ENSEMBLE-CALIBRATION CHECK render; band coverage 90% certified | Served |
| Probabilistic > deterministic (chapter §5) | Bucket pmf + band is THE day-ahead product; boundary = coin-flip vocabulary | Doctrine (CLAUDE.md) |
| Persistence/situational awareness beats NWP at short range (chapter §1.2; Bauer et al. 2015) | The intraday lever: running-max ratchet + remaining-rise + tape/grade engine — obs, not model | The ONLY conviction lever (certified 56→89→99% London) |
| Ramps = fronts/clouds passage | Endpoint-motion + sustained-lead detection (intraday tape, 2026-07-12) | Shipped |
| Regime-dependent methods (McCandless et al. 2016a,b) | Honest form: regime→Kelly SIZE multiplier (certified); PoP regime split (accruing, n≥15 gate) | Pick-conditioning form is DEAD (D15, D18) |
| Equation of time in solar geometry (chapter §3.4: ≤16-min error if omitted) | `intraday_grade.sunset_local_hour` includes the NOAA eqtime term | Validated ±5 min vs certified lock clocks |
| Validation planning (Warner 2011b) | Pre-registration → frozen probe → fold gate → KAT → ledger; dead ledger D01–D19 | The repo's constitution |

## 2. Dead at our grain — where the textbook's gains do NOT transfer

The chapter's post-processing gains (10–15% blend improvement, AnEn skill) are real **in
MAE/CRPS on continuous wind/solar power**. Our economic object is the exact whole-degree
settlement bucket, and the council's residual σ ≈ one bucket width. Sub-bucket-width signal
— which is what most post-processing harvests — converts to ZERO bucket-hit improvement.
This is measured, not asserted:

- **Analog ensemble** (Delle Monache et al. 2013; Alessandrini et al. 2015): D13 (analog-
  weighted remaining-rise: +0 to −2pt every hour) and D17 (k-analog day-ahead model, dead as
  served; informational line only). The unconditional empirical cloud already uses every
  prior record optimally at this grain.
- **Adding members**: D02 (AIFS, 3 attempts, noise); forecast members 0/6 as a class
  (AIFS/UKMO-2km/ICON-D2/ICON-EU/AROME). Only TWC earned an accrual clock — settlement-
  alignment, a mechanism no NWP member has.
- **Physics-informed conditioners** (cloud/insolation/dew-point/slope — chapter §3.2's
  parameterization variables as predictors): D07, D08, D11 — real signals, fold-unstable or
  sub-bucket. D18: r=+0.20 regime signal, stable both halves, worth +0.065°F — nothing.
- **Variance scaling / EMOS-style width correction**: backtest_cloud_scale SHIPS=False;
  GEFS-reforecast EMOS tails closed (cand 50 — London tails are thin, not fat).
- **BMA member reweighting** (Raftery et al. 2005): not ID-dead, but its marginal CRPS gains
  sit below the frozen-A/B detection floor (live-feed revisions ≈ 0.1 CRPS run-to-run —
  feedback_backtest_ab_needs_frozen_data). Disposition: not until a frozen archive of member
  forecasts is deep enough to detect it; do NOT claim it from live before/after.
- Day-ahead levers total: **0/19**. The ceiling is information, not method (44%=44% market
  tie says the public information frontier sits at the same place).

## 3. Corrections to the primitive-equations summary (the deep intricacies)

1. **"Navier-Stokes governs wind movement."** Operational NWP solves the PRIMITIVE
   equations — filtered forms (historically hydrostatic; modern global cores nonhydrostatic).
   Richardson's 1922 failure was exactly the UNfiltered equations: gravity-wave noise in an
   unbalanced analysis produced a 145 hPa/6h surface-pressure tendency (Lynch 2006). The
   first success (Charney, Fjørtoft & von Neumann 1950) filtered all the way to barotropic
   vorticity. "Which waves you delete" is as load-bearing as the conservation laws.
2. **"Grid cells several km; smaller processes are parameterized."** Understated: the
   EFFECTIVE resolution is ~7Δx, not Δx (Skamarock 2004; Jiménez et al. 2016b) — a 3-km
   model resolves ~20-km features. And between mesoscale and LES lies Wyngaard's (2004)
   "terra incognita" where PBL closure assumptions are invalid at exactly the grid spacings
   that look most attractive. Parameterization error is state-dependent (Stensrud 2007) —
   stable-boundary-layer and coastal-inversion regimes (our SF marine layer) are where
   councils earn their bias corrections.
3. **"Run the model dozens of times; similar outcomes = high confidence."** Three
   corrections. (a) Random perturbations need HUNDREDS of runs to span the pdf (Kolczynski
   et al. 2012) — operational centers use fastest-growing perturbations instead (singular
   vectors, Molteni & Palmer 1993; bred/EnKF modes). (b) Agreement ≠ confidence: an
   under-dispersive ensemble agrees AND is wrong; spread is a valid uncertainty signal only
   if the spread–skill relation verifies (Fortin et al. 2014) — which is why our render
   checks it daily instead of trusting it. (c) Member spread estimates the error of ONE
   member; the blend's error is ~√N smaller — read raw spread as blend uncertainty and you
   overstate σ (the served averaging-factor line).
4. **"Butterfly effect → doomed at future times."** The predictability limit is
   SCALE-DEPENDENT (Lorenz 1969; Bauer et al. 2015): convective scales saturate in hours,
   synoptic in ~2 weeks. For a daily-max bucket product the binding constraint is not chaos
   at day 10 — it is the σ≈bucket information floor at day 1 (shared with the market), and
   chaos is irrelevant INTRADAY, where the realized running max progressively replaces the
   forecast entirely. We beat "chaos" by waiting for observations, which is the chapter's
   own §1.2 point.
5. **Data assimilation (§2) maps onto the settlement side, not the model side.** We
   assimilate nothing into NWP; but the intraday tape IS a sequential assimilation of the
   settlement surface: the ratchet floor is a monotone observation operator, endpoint
   corroboration is QC, the frozen-v3-stamp rejection is an innovation check, banked-vs-
   leading is background-vs-observation. The KF-class residual corrector was probed on the
   forecast side: the residual mean-reverts (Hurst ~0.5–0.7), so AR(1) wins and the Kalman
   local-level HURTS — Kalman machinery pays only when a persistent latent drift exists
   (project_own_forecast_program; timescale/residual_kalman modules).

## 4. Load-bearing references (the ones worth reading past the abstract)

Bjerknes 1904 (the NWP manifesto) · Richardson 1922 + Lynch 2006 (why filtering matters) ·
Charney, Fjørtoft & von Neumann 1950 · Lorenz 1963, 1969 (chaos; scale-dependent error
growth) · Bauer, Thorpe & Brunet 2015, Nature (the "quiet revolution": ~1 day of skill per
decade, and why) · Glahn & Lowry 1972 (MOS) · Ebert 2001 (poor man's ensemble) · Molteni &
Palmer 1993 (singular vectors) · Evensen 1994; Anderson 2001 (EnKF) · Talagrand 1997 (DA
definition) · Raftery et al. 2005 (BMA) · Wilks & Hamill 2007 (ensemble-MOS) · Gneiting,
Balabdaoui & Raftery 2007 (calibration + sharpness — the probabilistic-forecast constitution)
· Leutbecher & Palmer 2008; Fortin et al. 2014 (spread–skill) · Kolczynski et al. 2011
(linear variance calibration) · Delle Monache et al. 2013 (AnEn) · Skamarock 2004 (effective
resolution) · Wyngaard 2004 (terra incognita) · Stensrud 2007 (parameterizations) · Warner
2011b (NWP quality assurance — reads like this repo's CLAUDE.md) · Monin & Obukhov 1954
(surface-layer similarity, behind every LSM/PBL flux).

**Standing rule this file encodes:** a published post-processing gain transfers here ONLY if
it survives at the settlement-bucket grain under the frozen gate. MAE/CRPS gains on
continuous variables are the textbook's currency, not ours; 19 dead candidates say the
exchange rate is usually zero.
