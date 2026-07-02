# HANDOFF — weather-verdict: the mistakes, and how to fix them

*Written 2026-07-02 after 07-02 settled. For the next session. Read before running another verdict.*

## 2026-07-03 FULL-STACK AUDIT — seven defects found running the production pipeline end-to-end
Ran `tools/daily_verdict.py` (the real launchd job) and audited every layer. Found, fixed, verified:

| # | Defect | Root cause | Status |
|---|---|---|---|
| A | WU-native validation gate DEAD since 07-02 ("insufficient history") | **obs-cache poisoning**: `_wu_hourly_raw` returns `[]` on network failure and `_cached_range_obs` cached it for the 7-day TTL (written during the 07-02 dead-DNS window) | **FIXED** — ≥25%-coverage floor on cache read AND write (`sources._obs_days_covered`); poisoned keys self-heal; 4 KATs (`test_obs_cache_guard`). Verified: gate back (14:00 79.3% / 15:00 94.2%, fold-stable) |
| B | 07-02 09:00-WAT launchd cycle silently produced NOTHING (whole run died in ~1s, dead DNS) → **permanent TWC hole for 07-03** (point-in-time can't backfill) | single daily shot per ledger | **MITIGATED** — TWC + PoP loggers now also run in `daily_verdict` (3 windows/day, idempotent). The 07-03 hole itself is unrecoverable — the honest cost of point-in-time |
| C | The failure in B was INVISIBLE — accumulate logged only the LAST stdout line ("rc=0 \| …40 more pairs…") while the fetch-failed line scrolled by | last-line-only logging | **FIXED** — `_tail_status` surfaces any "fail/error" line alongside the last line |
| D | **TWC 40-pair clock would have accrued ZERO pairs forever** — `_anchored_actual` had no WU branch (rows with `station_id=None` fell to lagged ERA5) AND the cutoff used host-local `today-2` (host runs a day behind SGT) | settle path WU-blind + host-vs-city-local date | **FIXED** — WU branch added (mirrors `settle_market_snapshots`); readiness is now CITY-LOCAL per station. Verified: 2 pairs settled (Singapore 30 **HIT**, Manila 32 miss vs 35) |
| E | M3 cross-check thin-regime bug — flagged 07-02, still shipped (Manila n=4 → "regime 36") | no min-n guard | **FIXED** — recent-regime requires n≥8 settled days. (Honest note: on 07-02 the buggy signal was *closer than my read* — the guard suppresses noise, it does not make thin modes oracles) |
| F | TWC + PoP were logged but NOT SURFACED in the verdict — violating the 07-02 lesson (don't bury divergent signals) | panel predated the ledgers | **FIXED** — cross-check now shows `twc-oracle` at face value + the PoP regime tag as context ("cold squall tail is LIVE" when convective). Verified on the 07-04 panel: `council 32 · market 32 · twc-oracle 32 · recent-regime 32 · PoP 55% → CONVECTIVE` |
| G | Lock rows from the "15:00 job" sometimes land in the **14:00 bin** (the lever keys on the WU obs-hour, ~30-min lag) | honest binning vs wall-clock naming | **DOCUMENTED, no code change** — binning by obs-hour is the *correct* conditioning; it just spreads n across adjacent bins (mitigated by 3 runs/day). The "95% @ 15:00" claim certifies at the lever's hour 15, not the job's wall time |

Meta-lesson: every defect was in the **plumbing between correct components** (cache↔fetch, settle↔truth-source, log↔visibility, panel↔ledger) — not in any model. End-to-end production runs ARE the test that finds these; unit suites (403→407 green throughout) caught none of them.

---

## The scoring that exposed the mistakes (07-02, both bucket cities)

| City | Settled | My day-ahead call | Result |
|---|---|---|---|
| Singapore | **30°C** | coin-flip 31-vs-32, **lean 31**, band 29–31 | single MISS (30); band OK |
| Manila | **35°C** | coin-flip 31-vs-32, **lean 32**, band 31–33 | **GROSS MISS** — 35 is 2 buckets *above* the band |

**Both coin-flip calls missed — and in both, the signal I explicitly DISMISSED was the closest to the truth:**
- Singapore settled **30**. I said *"TWC (30) carries zero weight, today it's just logged."* **TWC was the only point signal that hit.** Council/market/pattern all said 31 (off-by-one); season/regime said 32 (off-by-two).
- Manila settled **35**. I said the cross-check's *"recent-regime 36 is a thin-sample artifact, the real regime is 32."* **36 was the closest number on the board.** Council/market/TWC/season all said 31–32 and grossly missed a hot day.

TWC's first scored pair: **Singapore 30 HIT, Manila 32 miss (vs 35)** → 1/2, but its hit landed exactly on the day the whole council-cluster missed.

---

## Forecast verification (numbers for the failure-class diagnosis)
Multi-category Brier over the settled bucket distribution (range 0–2; halve to think in 0–1 terms). Climatology = leave-one-out per-city base rate.

| | n | model Brier | climatology Brier | skill (BSS) |
|---|---|---|---|---|
| **Pooled** | **46** | **0.723** | **0.928** | **+0.22** |
| Singapore | 10 | 0.617 | 0.790 | +0.22 |
| Manila | 11 | 0.854 | 0.980 | +0.13 |
| Hong Kong | 11 | 0.670 | 0.900 | +0.26 |
| London | 12 | 0.732 | 1.008 | +0.27 |

**Read:** the model BEATS climatology in every city (+0.13 to +0.32) → NOT the "no-skill / worse-than-climatology" failure class. It is *real but modest* skill, capped by the σ-ceiling (day-ahead forecast error ≈ bucket width, so boundary calls are coin-flips). **Manila is the weak point** — worst model Brier (0.854) + lowest skill (+0.13), consistent with the hot-tail under-dispersion (07-02: settled 35, served band 31–33).

**Caveats:** (a) multi-category Brier (0–2), not 2-class; (b) **n=46 is MIXED-LEAD** — deduped per city-day, includes post-peak snapshots; a day-ahead-strict subset is ~18 and scores worse (use that for a day-ahead claim); (c) small n per city (basket ~10–11 each).

## Mistake 1 — Dismissing signals that disagree with the council-cluster
The recurring error this session. When a signal diverged from the council/market consensus, I editorialized it away ("zero weight", "artifact") — and on the days the consensus missed (which is *most* days; live day-ahead is 5/9 ≈ 56%), the dismissed outlier was what caught it. **The dismissal IS the mistake.** Stop pre-judging outliers to zero. Report them at face value and let divergence *widen the band toward them*, not shrink it.

## Mistake 2 — Under-dispersing the tails, especially for hot/high-variance Manila
Manila settled 35; the served band was 31–33 — a **gross miss the band didn't even contain.** Manila's season base rate spans **32–37** (35:17%, 36:16%, mode 32 but median 33). The council + bias correction systematically **center Manila low (31–32) and quote a band far too narrow.** This is a real, testable model defect (under-dispersion / hot-tail miss), distinct from the σ-ceiling. Singapore's band (29–31) at least contained 30; Manila's did not. **Manila needs tail-aware calibration; its band conviction (80%) is overconfident.**

## Mistake 3 — The cross-check recent-regime is buggy for thin scorecards
I shipped the cross-check panel using `live_bucket_scorecard(place)['recent']` for the regime mode. Manila's scorecard has only **n=4 settled days**, so its "mode" (36) is noise — *except it was closer than the number I trusted.* Fix: guard `recent-regime` on a minimum n and fall back to the **WU season base-rate / last-10 window** (which had mode 32 AND showed the hot 33/34 tail), not the thin served scorecard. `run.py:_cross_check_lines`.

## Mistake 4 — Fake independence in the cross-check
The panel treats council · market · regime as independent votes. **They are not.** The market largely echoes the same NWP the council uses, and the WU-pattern analogs are the D13-null. So "3 signals agree on 31" is closer to *one* view repeated. The genuinely independent signals — **TWC (the oracle's own forecast)** and the **WU hot base-rate** — are the ones that diverged and were right on 07-02. Weight independence, not headcount.

---

## Improvement directions (all gated — no asserting, per the whole project's discipline)
1. **Manila (and hot/variable cities): tail-aware calibration.** Check Manila residuals for fat/hot-skewed tails; probe a Manila-specific spread inflation (leak-free, disjoint-fold gate, must clear on held-out CRPS + coverage). The 07-02 gross miss (35 vs band 31–33) is the evidence. Related: the HK under-dispersion note in [[project_tail_calibration_emos_closed]] — but that closed a SHAPE fix; this is a Manila SCALE fix, re-open only through the frozen-A/B gate.
2. **Fix the cross-check recent-regime** (min-n guard + WU base-rate/last-10 fallback). Small, do it first — it's an outright bug that shipped.
3. **Stop dismissing divergent signals.** Present TWC and the hot base-rate at face value; when they diverge from the council, that's a *widen-the-band* signal, not a "zero weight" footnote. (TWC is still recommend-only until its 40-pair gate — but "recommend-only" ≠ "editorialize to zero.")
4. **The real resolver is still intraday** — both 07-02 misses (Singapore 30, Manila 35) would have been caught by the afternoon lock (running max 30.0 / 35.0 by mid-afternoon). Day-ahead, the honest fix is *wider, tail-aware bands* + *not dismissing outliers*, NOT a better point pick (σ-ceiling, 0/13 levers dead).

## What is NOT the fix (don't relitigate)
- A better day-ahead point forecast — 0/13 accuracy levers, physical σ-ceiling.
- A market/consensus override — day-ahead the market is tied at 44% (post-peak-snapshot artifact); see [[project_no_live_edge]].
- Asserting TWC into the blend before its gate — un-backtestable now; see [[project_twc_forecast_member]].

## Pointers
- Verdict composition + the cross-check bug: `run.py` `_bucket_call_lines` / `_cross_check_lines`.
- Manila calibration: `weather_council/council.py` (bias/residuals), `intraday_ceiling.py` (Manila uses IEM, not WU).
- Gate discipline: `.claude/skills/harness-optimizer`, `ledger/dead_candidates.jsonl` (D01–D13), the frozen-A/B rule in [[feedback_backtest_ab_needs_frozen_data]].
- Live record: `storage.live_bucket_scorecard`; run `tools/accumulate.py` daily (now also logs TWC).
