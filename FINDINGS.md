# FINDINGS — weather-verdict, complete and adversarially gated

*As of 2026-07-02. Every claim below carries a **gate label** and a **Challenge** (what would falsify it / why to doubt it). Nothing is asserted as proven that has not cleared a leak-free gate. If a number here is used to make a decision, read its Challenge first.*

## Gate labels
- **PROVEN** — cleared a leak-free walk-forward gate (disjoint-fold sign-stability on a proper score AND the economic object, past the run-to-run noise floor).
- **MEASURED** — computed on real settled data, but n-limited and/or not gated. Directional, not decisive.
- **ASSERTED** — stated but not yet gated. Treat as a hypothesis.
- **DEAD** — tested and failed a gate (in `ledger/dead_candidates.jsonl`, D01–D13). Do not relitigate.
- **OPEN** — genuinely undecided; accruing data or awaiting a gate.

---

## 1. The object being predicted (PROVEN mechanics)
Emit the **daily-max whole-°C bucket** for the two basket cities (**Singapore/WSSS, Manila/RPLL**) that a Polymarket contract settles on. Settlement = **round-half-up** on the **Wunderground / The Weather Company (TWC) airport record** (whole-°F → °C). Singapore intraday reads WU natively; Manila reads IEM METAR (°C).

**Challenge:** the settlement model is only verified for the basket + London/HK. Manila's intraday uses IEM (°C grain), not the WU (°F) grain the market pays on — a sub-degree boundary mismatch is possible and **untested for Manila**. HK settles on a daily-max-only record (no hourly), so it structurally cannot use the intraday lock. If a city's truth source drifts from the market's actual oracle, every score below is measuring the wrong thing.

---

## 2. The central finding: the σ-ceiling (PROVEN)
Day-ahead single-bucket **pinpoint accuracy is information-limited, not effort-limited.** Continuous forecast error (MAE ≈ **0.85 °C**) ≈ the **1 °C bucket width**, so a forecast near a bucket boundary is a coin-flip: `P(exact bucket) ≈ P(|N(0,σ)| < 0.5)`.

**Evidence** (173 settled verdicts, all cities): **hit 47% · off-by-one 38% · gross 15%**, MAE 0.85. The 38% off-by-one IS the ceiling (boundary flips); the 15% gross are anomalous days (capped/hot surges).

**Challenge:** "physical, not this model" is only credible because **0/13 day-ahead accuracy levers cleared the gate** (§6). If someone produces a lever that clears the disjoint-fold gate, this claim weakens. It has survived 13 attempts, but absence-of-a-lever is not a theorem — it is strong induction, not proof.

**Consequence:** the honest day-ahead deliverable is a **BAND**, not a pinpoint. The pinpoint requires the peak (§4).

---

## 3. Forecast verification — CORRECTED by reviewer methodology (`verify_skill.py`)

**Three defects in my original computation (retained below for the audit trail) were fixed:** LOO climatology degenerate at small n → **season base rate** reference (181 independent WU days); skill sign from point estimates → **paired bootstrap blocked BY DATE** (95% CI must exclude zero to earn a sign); distance-blind Brier → **RPS** (ordinal) as the primary score. Reproduce: `python3 verify_skill.py --records records.jsonl --climo season_base_rates.json`.

**Result (all-leads, n=33 scored; HK/Tokyo/Chicago skipped — no WU base rate, never imputed):**
- **Brier skill: NOT EARNED.** mean(climo−model) +0.062, 95% CI **[−0.077, +0.206]** — includes zero (day-ahead: +0.094 [−0.154, +0.368]). **The old "+0.22 beats climatology" does NOT survive a sign test → downgrade to MEASURED-PENDING, not PROVEN.**
- **RPS skill: EARNED.** mean(climo−model) +1.71, 95% CI **[+0.89, +2.68]** — excludes zero (day-ahead +2.94 [+1.30, +4.97]). On the distance-aware score the model DOES beat climatology, because its misses are **close** (off-by-one) where climatology's are diffuse. *Caveat:* magnitude is **London-dominated** — London's 180-day climatology spans buckets 2–36 (cross-seasonal) → climo RPS 5.86 → London RPSS +0.88 inflates the pool. Tropical-basket RPSS is **modest: Singapore +0.21, Manila +0.32.**
- **Manila is genuinely weak:** BSS **−0.04** (at/below climatology), RPSS +0.32.
- **Band coverage — the Mistake-2 adjudicator:** stated conviction ~85–92%; empirical coverage by city — Singapore **90%** ✓, Hong Kong **91%** ✓, **Manila 64%** ✗, London **58%** ✗. **Manila's bands are overconfident → Mistake 2 is CONFIRMED, not asserted.**

**Corrected read:** the model's earned skill is **being CLOSE (RPS sign earned), NOT exact-bucket (Brier sign not earned)** — exactly what the σ-ceiling predicts (off-by-one dominates). Manila is at/below climatology with under-covered bands. All magnitudes stay PROVISIONAL: the sign test's own rule requires **n_dates ≥ 40** (we have 21 all-leads, 11 day-ahead).

---

*Original LOO computation, **SUPERSEDED** — the BSS column below is VOID (LOO degenerate at small n); kept only for the audit trail.*

Multi-category Brier over the bucket distribution (range 0–2), climatology = leave-one-out per-city base rate:

| | n | model Brier | climatology Brier | skill (BSS) |
|---|---|---|---|---|
| **Pooled** | **46** | **0.723** | **0.928** | **+0.22** |
| Singapore | 10 | 0.617 | 0.790 | +0.22 |
| Manila | 11 | 0.854 | 0.980 | +0.13 |
| Hong Kong | 11 | 0.670 | 0.900 | +0.26 |
| London | 12 | 0.732 | 1.008 | +0.27 |

**Read:** model beats climatology in every city → **NOT** the "no-skill / broken" failure class. It is **real-but-modest skill capped by the σ-ceiling**. Manila is the weakest (worst Brier, lowest skill).

**Challenge (three, all material):**
1. **Mixed-lead.** n=46 is deduped per city-day and *includes post-peak snapshots*, whose `model_prob` may be lead-0 (day-informed), not day-ahead. The **day-ahead-strict subset is ~18** and would score worse. Any *day-ahead* skill claim must use that subset — which I have not fully recomputed. The +0.22 is an **upper bound** on day-ahead skill.
2. **Multi-category (0–2), not 2-class (0–1).** Do not compare these to a coin-flip's 0.25 or a binary Brier.
3. **n is tiny** (10–12/city). BSS at n=10 has a huge SE; per-city ordering (Manila worst) is suggestive, not significant. Climatology-LOO at n=10 is itself noisy.

**Day-ahead-strict recompute (n=17, snapshots issued BEFORE the settlement day):** model Brier **0.733** — *essentially identical* to the mixed-lead 0.723. So the model Brier is **lead-stable**, and my "mixed-lead is an optimistic upper bound" worry above was **wrong**: `model_prob` really is day-ahead-natured (the council forecast does not converge intraday). Climatology **1.216**, skill **+0.40**. But the *higher* strict skill is a **small-n climatology artifact** (LOO climatology at n=2 for Manila → Brier 2.0), not real improvement. **Robust conclusions:** (i) model Brier ≈ **0.72–0.73**, lead-stable; (ii) model beats climatology in **sign on every cut**; (iii) the skill **magnitude (+0.22 to +0.40) is unreliable** at these n and must not be quoted precisely. The "real skill, not broken" claim survives; a precise skill number does not.

---

## 4. The intraday lock — the only real conviction lever (PROVEN on London, MEASURED live)
Running max + a leak-free empirical remaining-rise, resampled through the settlement quantizer. **PROVEN on London EGLC** (the only city with a settlement-grade hourly archive to backtest on): exact-bucket hit **16% (09:00) → 30% (12:00) → 89% (15:00) → 99% (18:00)**, sign-stable on disjoint folds. Singapore WU-native honest read: **79% (14:00) / 95% (15:00) / 98% (16:00)**. Live: caught 06-30 (28), 07-01 (32), 07-02 (30 & 35) — every day the day-ahead missed.

**Challenge (this is the most important set of caveats in the document):**
- **It is post-peak. This is "the finish line."** By the time σ collapses, the market has watched the same thermometer and converged. The lock is *accurate*, not *tradeable* (§7). Do not confuse the two again.
- **Pre-peak it is not just diffuse — it can be actively misleading.** 07-02 @ 04:49 it read "34°C @ 26%" (a warm-night artifact: a warm pre-dawn + unconditional big rise). Reporting a pre-peak lock number is a mistake; it is reliable only near/after the peak.
- **Manila uses IEM °C, not WU °F.** The London/Singapore proof does not transfer to Manila's grain; Manila's lock is **ASSERTED, not proven**.
- **HK cannot use it** (no hourly record) → HK is permanently band-only.
- **The analog-conditioned version is DEAD** (D13): weighting prior records by today's morning level adds nothing (Δhit +0/−2%). The *unconditional* rise is optimal — so there is no known way to make the lock confident *earlier* than the peak.

---

## 5. The mistakes exposed 2026-07-02 (MEASURED — behavioral, on me)
Both day-ahead coin-flip calls missed, and in both the **signal I dismissed was closest to the truth:**
- **Singapore settled 30.** I wrote "TWC (30) carries zero weight." TWC was the *only* point signal that hit; council/market/pattern (31) off-by-one, season/regime (32) off-by-two.
- **Manila settled 35.** I called the cross-check's "regime 36" a "thin-sample artifact"; **35 landed there**, and everything I trusted (31–32) grossly missed.

**M1 — Dismissing divergent signals.** On the ~44–56% of days the council-cluster misses, the dismissed outlier is often what catches it. The dismissal is the error.
**M2 — Manila hot-tail under-dispersion.** Band 31–33 didn't even *contain* 35; Manila's base rate spans 32–37 (35:17%, 36:16%). The band's 80% conviction is overconfident. A **real, testable model defect**, distinct from the σ-ceiling.
**M3 — The cross-check `recent-regime` is a shipped bug** (uses the n=4 live scorecard → garbage mode).
**M4 — Fake independence.** Council · market · WU-analogs ≈ one NWP view repeated (market echoes NWP; analogs are D13-null). The independent signals are TWC and the WU base-rate.

**Challenge (do NOT overcorrect):** this is **n≈2 days**. TWC hitting Singapore-30 is 1 event; it *missed* Manila (32 vs 35) the same day → TWC is **1/2**, not a savior. "Stop dismissing outliers" is right; "trust the outlier" is the mirror-image mistake ([[feedback_trust_council_straight]]). The correct move is **widen the band toward divergence and lower conviction**, not flip to following TWC/regime. M2 (Manila dispersion) is the one claim here strong enough to *build* on — and even it must clear the frozen-A/B gate before touching a served band.

---

## 6. The dead ledger — everything tested and killed (DEAD; D01–D13)
| ID | Lever | Why dead |
|---|---|---|
| D01 | 6 statistical correctors + regime-gating | no held-out gain across folds |
| D02 | ECMWF AIFS as 9th member | noise, 3 attempts |
| D03–D06 | UKMO-2km / ICON-D2 / ICON-EU / AROME hi-res | folds flip / no gain |
| D07 | dew-point @09:00 feature | +7.1 but fold0 regresses |
| D08 | morning slope @12:00 | +3.3 but still coin-flip; physical irreducibility |
| D09 | Open-Meteo ensemble member-history | not backtestable (0 days stored) |
| D10 | boundary abstention / coverage recal / hierarchical pool / low-side | single bucket is the point; deferred-rejected |
| D11 | intraday convective-cap conditioner (CAPE/cloud) | fold-unstable, ERA5 upper-bound only |
| D12 | online cross-day error-persistence (err autocorr +0.28) | real but HURTS Singapore/HK on folds, all damping |
| D13 | analog-conditioned remaining-rise | null (Δ +0/−2%); unconditional rise already optimal |
| D14 | Singapore day-ahead **two-bucket band** (cool-skew) | **PRE-REGISTERED kill** — cool 6/12=50% (<60% floor), no 2-band clears 75%; σ 1.36 bimodal; the cold-skew *premise* was falsified (misses lean warm). See `ledger/preregistered/singapore_two_band.md`. |

**D14 is the first *pre-registered* kill:** the falsifiable criteria (Gate 1 ≥75%, dead <60%) were frozen in `ledger/preregistered/singapore_two_band.md` and committed *before* the backfill was scored, so "we decided the bar before seeing the data" is a checkable claim (the commit hash), not honor-system. It also demonstrates the **kill-before-instrument** rule: because the backfill failed, the proposed PoP-logging clock was *deliberately not started* — no instrumentation for a hypothesis that failed its own viability floor.

**Challenge:** the ledger is only as good as the gate. All were killed on **disjoint-fold sign-stability** — a strict gate that could produce false negatives at small n (a real lever that happens to flip one thin fold). D12's error-autocorrelation (+0.28) is genuinely suggestive and died partly on n. If n grows, D12 is the one worth *one* honest re-test on frozen data — not the others.

---

## 7. The trading detour — NO live edge (MEASURED, strong; parts DEAD)
The system was pushed to find a *tradeable* edge. Exhaustively, there isn't one on this venue:

- **Forecast edge:** none — market ties/beats the model day-ahead (§8).
- **Intraday timing:** none — pinpoint = post-peak = market already converged (finish line).
- **Dead-bucket arb:** DEAD — 0 residual bids on mechanically-impossible buckets (crowd is efficient).
- **MECE field arb:** artifact — an apparent +3.2% buy-the-field on Chicago **vanished to −0.6% on re-pull** (stale Gamma); sell-field +1.2–2.4% is within inter-pull jitter; **clob.polymarket.com is not allowlisted**, so only lagged Gamma is readable.
- **Favorite-longshot / calibration:** DEAD — 94 settled markets, no monotone bias, **SELL-longshots EV negative at every threshold**, on de-vigged (0% overround) probs.
- **Day-ahead consensus:** market does NOT beat council (44% vs 44%, n=16 day-ahead-strict); the naive "market 69%" was a **post-peak snapshot artifact** (56/74 same-day snapshots captured after the peak).

**The only measured edge in the entire body of work is crypto RISK PREMIA** (a different project): funding carry clears the hardened gate but is **modest (~+10.7%/yr unlevered), conditional (loses in bear), and UNLEVERABLE** (L=3 → negative, liquidations); VRP FAILED the same gate; FX carry is not an edge. A `funding_sizer.py` was built — recommend-only, currently **ABSTAIN** (funding ~0 + bear).

**Challenge:** "no edge" is MEASURED not PROVEN — it rests on small n and on artifact-identification (which could itself be wrong). But the artifacts were *reproduced* (Chicago re-pull, snapshot-timing histogram), and the wall is structural (§8), so the confidence is high. The one thing that could overturn it: **live CLOB access** (not available), which might reveal a real, tiny, fast field arb — but that needs infra this operator does not have, and would face adverse selection on lagged data.

---

## 8. The structural reason there is no edge (the deepest finding)
Trading value requires knowing the bucket **before** the market. But the market watches the **same real-time airport thermometer**. So:
- **Pre-peak:** both are uncertain (the peak hasn't formed — the information does not exist).
- **Post-peak:** both are certain (shared observation).

**The knowledge-advantage window is empty.** On lagged public (Gamma) data the operator is the *slow money* — adverse selection, not alpha. "Being right earlier" is impossible when your feed is itself behind theirs.

**Challenge:** this assumes the market is efficient on the shared feed and that we have no faster/private data. Both hold here. It would break only with a genuine data or speed asymmetry — which is the correct thing to hunt if trading is the goal, and is **absent in weather buckets on a retail venue.**

---

## 9. What is shipped (code; all recommend-only or gate-guarded)
- **Verdict leads with the intraday lock** when sharpened, grounded in "running max + N prior-day records"; day-ahead labeled a PRELIMINARY BAND, not a pinpoint (`run.py:_bucket_call_lines`).
- **Cross-check panel** — council · market · recent-regime, flags council-outlier/agree/split (`_cross_check_lines`). **Contains the M3 bug.**
- **WU-native Singapore intraday** (settlement-faithful °F grain).
- **TWC forward-logger** (`tools/twc_forecast_logger.py`) in the daily accrual loop (§10).
- **Settle/verify are WU-aware** for the live basket; daily automation via launchd; 395 KATs green.

**Challenge:** none of these *move a served number without a gate* — the cross-check is display-only, TWC is logged-not-blended, the lock is read-only. That is by design. The one shipped thing that is *wrong* (not just un-gated) is the M3 regime bug — fix it first.

---

## 10. Open / accruing (OPEN)
1. **TWC as candidate 9th member** — the settlement oracle's own forecast, uniquely station-aligned; **un-backtestable now (live-only, D09-class)**, so forward-logged. Gate after **≥40 settled (TWC, WU) pairs** (~6 weeks): does TWC-added-to-council beat council-alone on disjoint-fold CRPS + bucket-hit? Ship only if it clears, else D14. Prior unfavorable (forecast members 0/6). **Currently n=1 real pair** (Singapore 30 HIT). Do not promote early.
2. **Manila hot-tail calibration** — the strongest open lever (M2). Probe a Manila-specific spread inflation, leak-free, through the **frozen-A/B + disjoint-fold gate** (it touches the served band, so the bar is high). Related: [[project_tail_calibration_emos_closed]] closed a *shape* fix for HK; this is a *scale* fix for Manila — different, re-open allowed.
3. **Fix M3** (min-n guard + WU base-rate fallback for recent-regime). Small; do first.
4. **A frozen-A/B harness** is the prerequisite for #2 — live-feed revisions drift ~0.1 run-to-run (10× a lever's effect), so no before/after across live runs is valid ([[feedback_backtest_ab_needs_frozen_data]]).

**Challenge:** #1 and #2 both have unfavorable/hard priors. The honest expectation is that #1 closes as D14 and #2 yields a *modest* Manila dispersion fix at best. Bank on nothing until the gate speaks.

---

## 11. The gates (the discipline that makes any of this trustworthy)
A change to a served number ships **only if**: leak-free walk-forward; **disjoint-fold sign-stability on CRPS AND bucket-hit**; beats the **run-to-run noise floor** (~4pt at n≈120, ~0.1 CRPS on live feeds) on **both** halves; and on **frozen** data (record/replay), never a live before/after. Promotion is **recommend-only** — a human decides.

**Challenge:** the gate is deliberately strict and *will* reject real-but-marginal levers at small n (false negatives). That is the accepted trade: this project's failure mode is *false positives* (shipping noise as signal), and the entire dead ledger is the gate doing its job. If the goal ever shifts from "defensible" to "ship something," the gate is the thing to relax — consciously, not by accident.

---

## 12. The honest bottom line
A **calibrated forecaster with real-but-modest skill** (+0.22 BSS over climatology) whose day-ahead single-bucket accuracy is **capped by physics** (σ ≈ bucket width) and **resolved only intraday** (the lock, post-peak). It is **not a trading edge** — the market is efficient on the shared thermometer and the operator reads lagged data. Its measured weaknesses are **Manila hot-tail under-dispersion** (a real, fixable defect) and **me editorializing outliers to zero** (a behavioral one). Everything proposed to improve it is either **dead** (0/13 day-ahead levers), **un-backtestable-yet** (TWC), or **awaiting a gate** (Manila dispersion). The most valuable honest output of this project is not a verdict — it is a **disciplined machine for telling the difference between a signal and a story**, and a ledger of every story it has correctly refused.

*Postscript — the reviewer's `verify_skill.py` overturned part of this document, which is the point of writing it to be attackable. The **Brier** skill sign is **NOT earned** (bootstrap CI [−0.08, +0.21] includes zero) → the "+0.22" is void. The **RPS** skill sign **IS earned** ([+0.89, +2.68]) → the model is reliably **close**, not exact — consistent with the σ-ceiling — though basket-modest and London-inflated. **Manila's band coverage (64% vs ~85% stated) confirms the one defect worth building on.** So the honest posture, now gate-tested rather than asserted: **only the RPS sign is earned; every magnitude is provisional (n_dates 21 < 40); Manila is the confirmed weak point; and the first story I told — "+0.22 real skill" — was refused by the gate applied to my own work.** Signs earned, magnitudes provisional, stories refused — including mine.*

---

## 13. Settlement-timing seam closed — tz-aware early-settle (INSTRUMENTATION, score-neutral)
*Added 2026-07-08.* `settle_market_snapshots` (writer of the anchor-station PROXY `realized_label` the alignment alarm cross-checks) used one blanket host cutoff of `today − 2 days`. But the two settlement clocks run at different lags: the **contract** backfill (`pm_resolved_label`) resolves at **T−1**, while the **proxy** waited to **T−2**. That 1-day skew left the alignment alarm structurally blind on the freshest resolved day — **London 07-07 audited as `pm_resolved_label=32°C` with `realized_label=None`**, so the divergence could not even be evaluated (it showed as `proxy = -`, not as a gap).

**Fix (DRY port of the already-audited sibling `settle_tracked_forecasts`, written after the 2026-07-03 host-behind-SGT audit):** the host cutoff is now a broad prefilter only; per row, a **WU-oracle station settles once its CITY-LOCAL day is over (T−1)**, while **lagged-truth (Meteostat-bulk) stations keep the conservative 2-day buffer**. City-local readiness (not a naive host `today−1`) avoids settling a US-west day that is still mid-afternoon locally after the host ticks over.

**Gate label: INSTRUMENTATION (labeling/timing, HARD RULE 2 — ships without the served-number gate).** It changes *when* a day's realized bucket is recorded, never the bucket VALUE: WU dailies do not revise, proven for the motivating case (EGLC 07-07 = **32°C** whether read at T−1 or T−2, and = the contract's paid 32°C). No forecast, pmf, weight, or served pick moves. Pinned by KAT `tests/test_settle_tz_early.py` (4 cases: WU-city-yesterday settles · WU-city-today skipped/no-leak · lagged-station T−1 buffered · lagged-station T−4 clears) + full gate **429 green**.

**Challenge:** the readiness test trusts that a WU city's daily max is final the instant its city-local calendar day ends. If the WU oracle ever *revised* a prior-day max after midnight-local (it has not been observed to), settling at T−1 would lock a pre-revision value the T−2 buffer would have caught. The proxy-vs-contract alarm remains the backstop: any such revision that changed the paid bucket would surface as a genuine gap on the next audit.

---

## 14. Register attribution defect fixed — pre-peak carryover no longer floors today (CORRECTNESS)
*Added 2026-07-09.* `_fuse_live_floor` (sources.py) fused the WU v3 24h-register into today's
intraday running-max whenever it "exceeded yesterday's max." At pre-peak hours that gate is
insufficient: the register carries yesterday's TRUE peak, which clears a whole-°F-rounded
yesterday row by pure granularity (89°F register vs an 88°F daily row) while today has barely
warmed. Live defect (Singapore 07-09, 06:54 SGT): today's obs max was 27°C (current 81°F) but
the ceiling floored today at 31.7°C on the 89°F register, then projected remaining-rise on top
→ an impossible **35–38°C** sharpened pmf for a ~30°C day.

**Fix:** add an attribution-margin gate — the register fuses only when it also sits within a
real between-obs spike (**3°F**; observed 07-04/07-07 gaps were 1°F) of today's OWN freshest
evidence (obs run-max + current, already in `floor_c`). A register far above today's readings
is an unattributable carryover and is dropped; a register close to them is genuinely today's
peak the lagging rows missed. Live re-check: Singapore ceiling now reads run-max 27.2°C and a
sane pmf (32°C 28% · 31°C 26% · 33°C 21%).

**Gate label: CORRECTNESS (bug fix).** It changes served output ONLY in the buggy regime (a
register unattributable to today); the certified afternoon lock is untouched — at the peak
today's current is within the margin, so the 07-04/07-07 register recoveries still fire
(pinned). KAT `tests/test_live_floor.py`: `test_stale_register_predawn_not_attributed_to_today`
(defect rejected) + `test_register_at_peak_still_fuses_when_today_corroborates` (no over-reject);
all prior live-floor KATs unchanged. Full gate **431 green**.

**Challenge:** the 3°F margin assumes a true between-obs spike never exceeds ~3°F above today's
freshest reading. If a genuine sharp spike did (rare at 30-min obs cadence), it would be
rejected and the lock would use the slightly lower current reading — conservative, not wrong.
The settle-cross-check divergence alarm remains the backstop for any register/settlement gap.

---

## 15. Daily-LOW market support added — the low event is no longer invisible (FEATURE, read-only)
*Added 2026-07-09.* `run.py` only ever fetched the `highest-temperature-…` event (slug hard-
coded, `HIGHEST_TEMP_TAG` paging, `compare_high` only), so a whole tradeable market — e.g.
"Lowest temperature in London on July 9?" (volume ~16.7k USDC) — was never modelled. User-caught.

**What shipped (read-only, mirrors the high comparison; changes no existing served number):**
- `market.event_slug(city, target, kind)` — builds the `lowest-`/`highest-` slug; `resolved_event_slug`
  kept as the high alias. `_TITLE_RE` now matches `highest|lowest` (captures `kind`).
- `MarketData.fetch_market_by_slug(slug)` — pulls one event by its exact slug (low events sit under
  a different Gamma tag than the high paging enumerates).
- `compare_low` — the daily-LOW model-vs-market bucket comparison on `Validation.residuals_low`.
  Extracted a shared `_compare` core so compare_high/compare_low are thin wrappers (DRY; the
  existing high KATs guard the core). Basket low markets settle whole-°C, so a sub-degree low
  market is declined (the station-offset transfer is high-only).
- `run.py` renders a "MARKET COMPARISON — LOW" block when `--market` is set. Live 07-09 London
  low: model 22°C 71% vs market 22°C 42% / 23°C 35% — a genuine divergence (the market leans a
  bucket warmer; consistent with the intensifying-heatwave warm-nights ramp).

**Gate label: FEATURE (read-only annotation, same posture as compare_high — "NOT an edge, C7").**
It adds a served comparison but moves no forecast, pmf, weight, or pick, and no existing number
changes (high header/output byte-identical). KAT `tests/test_low_market.py` (slug, title regex,
fetch-by-slug, compare_low pmf built on the low cloud + re-centres with the verdict, MIN_RESIDUALS
decline). Full gate **436 green**.

**Registered follow-up (NOT done):** persisting low snapshots to the DB + settling them against the
daily MINIMUM + a low proxy-vs-contract audit. That is a storage schema/keying change (a snapshot
is currently one market per place/day = the high); scoped out here to keep this read-only and
un-gated. Until then the low comparison is display-only and is not entered into the C7 ledger.

---

## 16. Intraday lever wired for KSFO / San Francisco (whole-°F) — FEATURE, grain-aware
*Added 2026-07-09.* Both intraday modules skipped SF ("not a configured settlement city"), so
SF had no dead-bucket floor and no ceiling lock — the only conviction mechanism. The blocker was
grain: SF settles whole-°F (2°F Polymarket buckets), while the lever quantized hard-coded °C.

**What shipped:**
- SF added to `intraday_ceiling._HOURLY_STATION` (KSFO), `_WU_INTRADAY` (reads the WU hourly
  settlement feed, like Singapore), `_LIVE_REGISTER` (v3 current/register consult), and a new
  `_SETTLE_GRAIN = {"san francisco": "F"}`; and to `intraday._CITY_CONFIG` with `grain="F"`.
- The settlement quantizer is now grain-aware: `sharpen_pmf(..., grain)` and
  `state_late_risk(..., grain)` pass the grain to `_native_reading_int`, which converts the
  (always-°C) running max to the settlement unit before bucketing. Default stays "C" — the °C
  cities are byte-identical (441-test gate).
- run.py renders the SF blocks in °F (running max, floor bucket, pmf), and
  `_intraday_verdict_bucket` uses the city grain — fixing a false "verdict bucket ALREADY DEAD"
  alarm that compared the °C verdict bucket (18) against the °F floor (64).

**Live (SF 07-08, post-peak, declining):** floor **64°F**, verdict bucket **65°F live**, ceiling
**65°F @ 98%** — matching the market's 99% on the 64–65°F bucket and beating the day-ahead model's
warm lean. KAT `tests/test_sf_intraday.py` (config wiring both modules + grain-aware quantizer:
°F grain buckets at 65, °C grain at 18, from the same 18.3°C running max).

**Gate label: FEATURE.** The dead-bucket floor is observation-grade (certain). The ceiling pmf is
leak-free (resampled from SF's own strictly-earlier history) but its CONVICTION is **uncertified
for SF** — no frozen A/B yet, so it is forward-accruing exactly as the °C cities' locks were
before certification; today's 98% is observation-grounded (the peak has passed) rather than a
speculative pre-peak claim. Note the lever reports whole-°F station readings (65°F); the market's
2°F bucket mapping (65°F → 64–65°F) is handled by the market comparison, not the lever.
