# Weather-Verdict — Accuracy Program State

_Compacted session state. Last updated 2026-07-09. Standing mandate: constantly
improve day-ahead → intraday bucket-prediction accuracy for the two tracked
cities to match Polymarket settlement. Ship only gate-respecting changes;
abstain/close when no robust edge._

---

## Session 2026-07-09 — WU-city expansion + register hardening (see FINDINGS §13–20)

Shipped (all on `origin/main`, gate 448 green, each KAT-pinned):
- **tz-aware early-settle** (`1dc5847`) — WU-oracle cities settle `realized_label` at city-local
  day-end (T-1), closing the proxy-vs-contract alarm's 1-day blind spot.
- **Register bounded on THREE sides** (the recurring weak link, all in `_fuse_live_floor`):
  (1) TIMING — pre-dawn carryover can't floor today (`a42ffa2`, Singapore 37°C bug);
  (2) MARGIN — must sit within a ~3°F between-obs spike of today's obs (`a42ffa2`);
  (3) PHANTOM CAP — can never exceed WU's own `wunderground_daily_max` (`6533fca`, Jeddah
  102°F-vs-settled-100°F, user-caught). CLAUDE.md register line updated (`70fca2c`).
- **Daily-LOW market support + KSFO intraday lever** (`9c56dfc`) — `compare_low` on the low
  event; grain-aware quantizer so SF's whole-°F lever works.
- **New WU settlement cities** (London pattern: WU-settle whole-°C, IEM-backtest+overlay,
  IEM hourly + WU register): **Karachi/OPKC** (`453b8b0`), **Jeddah/OEJN** (`4850b20`). The
  `_IEM_OVERLAY_TZ` add fixed a ~110-day Meteostat lag (was backtesting March data in July).
- **10-year IEM archives** for OPKC + OEJN committed gzipped (`cbaee3e`), same convention as
  wsss/eglc/ksfo — puts their backtest/climatology/conviction on decade-deep footing.
- **Empirical intraday-conviction curves** derived per city (floor-lock = P(runmax bucket @ H
  == final bucket), 10y): lock windows 15:00-16:00 local (SF/Karachi/Jeddah), 16:00-18:00 London.
  In-sample backtest grade, NOT frozen-A/B certified — quote as "15:00 → 95% (historical)".
- **LAX (Los Angeles / KLAX) removed** from the working tree per user directive — stashed,
  never committed; main was always LAX-free.

Today's four locks (all resolved honestly, incl. two the day-ahead got wrong): Karachi **34°C**
(matched), Jeddah **38°C** (register phantom retracted), London **34°C** (WU record reconciled
up), SF **66-67°F** (intraday beat a too-warm 68-69 day-ahead). WU roster now: Singapore, Manila,
London, San Francisco (°F), Karachi, Jeddah.

---

## 1. Prime directive

Emit a **daily-max temperature bucket** verdict per tracked city that matches
Polymarket settlement:

- **Settlement rule:** `round_half_up` to whole °C on the **Wunderground
  (api.weather.com) airport record** (whole-°F → °C path).
- **Stations:** Manila = **RPLL**, Singapore = **Changi WSSS**, London = **EGLC**
  (London is on-demand only; see basket below).
- HK historical floor case (`HKO` 0.1°C, bucket `N°C` = `[N, N+1)`) is the
  sub-degree exception; EGLC whole-°C means round == floor.

## 2. Tracked basket

**Manila + Singapore** (commit `e2b0521`), both Wunderground-anchored.

- HK removed earlier (institutionally incapable of the intraday lever).
- London swapped **out** for Singapore on 2026-06-20 — still fully supported
  on-demand (`run.py "London"`), only auto-tracking removed.
- Singapore backtests **cleaner** than Manila: effective σ 1.4 (vs 2.5), flat
  field (spatial σ 0.7), high-bucket hit 51% (vs 29%), rank-histogram
  calibrated, point reliability HIGH — equatorial stability + clean Changi field.
- Touch points: `daily_healthcheck.BASKET`, `accumulate.CITIES`,
  `ensemble_accumulate.CITIES`. `reports/baseline.json` was `git rm`'d on the
  swap so the monitor rebaselines for the new basket.

## 3. The two-stage accuracy model

### Stage 1 — Day-ahead: the σ≈bucket conviction ceiling (CLOSED)

Day-ahead single-bucket hit ≈ `P(|N(0,σ)| < 0.5)`, information-capped at
**~51–54%**. This ceiling is **exhaustively confirmed closed**:

- 6 statistical correctors, regime-gating — no held-out gain.
- AIFS (`ecmwf_aifs025_single`) as a 9th council member — noise (×3 attempts).
- High-res NWP, London lead-1, 60d, 3 folds — **all closed**: UKMO-2km CRPS
  −0.009 / MAE-high −0.033 (fold0 flips); ICON-D2 −0.002 / −0.019 (folds 0,2
  flip); ICON-EU +0.002; AROME +0.007.
- Ensemble new-source: Open-Meteo ensemble stores 0 days of member history →
  not backtestable. Flow-dependent-spread mechanism already exists
  (`spread_skill.py` / `calibration.py`, recommend-only).
- Richer intraday features (dew point, slope) — closed: dew-point @09:00 +7.1
  but fold0 regresses; slope @12:00 +3.3 but still coin-flip. Physical
  irreducibility (peak not determined by the morning).

**Takeaway:** day-ahead is at its information ceiling. No day-ahead lever has
cleared the gate.

### Stage 2 — Intraday-ceiling lever (the ONE validated edge, SHIPPED)

Running max-so-far + empirical **leak-free remaining-rise** (learned from
strictly earlier days), resampled through the settlement quantizer. Code:
`weather_council/intraday_ceiling.py`; gate:
`tools/intraday_ceiling_backtest.py`.

**Why it works:** accuracy arrives once the running max has **captured the daily
peak** — then the lever near-*observes* rather than forecasts.

**Hourly crossover (the answer to "why not earlier than 15:00"):**

| Local hour | Singapore | Manila | London |
|-----------:|----------:|-------:|-------:|
| 10:00 | 29% (< clim 37%, fold-unstable) | — | — |
| 12:00 | 47% | — | — |
| 13:00 | 64% | — | — |
| **14:00** | **91% (folds 93/88)** | **87%** | 73% |
| 15:00 | 97.5% | — | 89% |
| 16:00 | 99% | — | — |

- ⚠️ **CORRECTION (06-21 bust): the table above is IEM whole-°C and runs ~12pts
  OPTIMISTIC at the peak hour.** Served Singapore 31 @ "91% locked" at 14:00;
  settled **32** (late 15:30 spike). IEM's coarse °C grain hides the °F boundary
  fragility that actually settles. **Singapore now runs WU-native** (`_WU_INTRADAY`,
  settlement-faithful whole °F): **14:00 79% · 15:00 96% · 16:00 98%** (disjoint-
  fold stable) — NOT "locked." Manila/London still IEM. So 14:00 is the *floor* of
  the confident range, not a lock; a late peak (14:00 ±1.6h) is irreducible — only
  post-peak (temps falling) is genuinely high-confidence.
- London peaks later/choppier → needs **15:00+**.
- **10:00 is a physical floor**: the peak is hours away; the morning carries *less*
  signal than guessing the seasonal modal bucket.
- Flags: `--hours`, `--city` (`fb6f50c`); WU-native via `_WU_INTRADAY` this session.

## 4. Gates (every change must clear all of these)

1. **Frozen-data A/B** (`tools/ab_backtest.py`) — record-on-miss / replay-on-hit
   HTTP cache so both arms see byte-identical Open-Meteo data; determinism
   self-check. `--member "model|Display"` A/Bs any Open-Meteo model.
   _Necessary because held-out CRPS/MAE drifts ~0.1 run-to-run from live-feed
   revisions — 10× a member's effect — so a before/after across separate live
   runs proves nothing._
2. **Disjoint-fold sign-stability** — improvement must hold on BOTH chronological
   halves (post-noon hours required to hold for the intraday gate).
3. **CRPS (proper score) + market high-bucket hit** on every fold.
4. **Leak-free** — only strictly-earlier days inform any estimate.
5. **One variable per candidate** — never bundle a structural change with a
   prompt/feature change (un-confounding is what produced the wins).
6. **Tests-green floor** — pre-commit runs the full suite
   (`PYTHONPATH=. python3 -m unittest discover -s tests`).
7. **Live scorecard** (`storage.live_bucket_scorecard`) accrues real settled days.

## 5. WU-truth advantage (correctness fix, not a skill claim)

Wunderground is current (lag ~0); Meteostat lags ~91 days → out-of-season
backtests. Switching truth to WU fixed an honest-window bug:

- Manila window Jan–Mar (lag 91) → Apr20–Jun19 (lag 0). Out-of-season optimism
  removed: high-bucket hit **59% → honest 29%**.
- Wiring: `sources.wunderground_daily_series` / `wunderground_current`,
  `council._WU_TRUTH_STATIONS`, intraday + intraday_ceiling configs,
  `run.py SETTLEMENT_REFERENCE`. Tests: `tests/test_wunderground_truth.py`.

## 6. Live status (today, 2026-06-21)

- **Singapore 06-21:** last full-stack run at 12:00 SGT, running max 31.0°C.
  Floor **≥31 locked**; single-bucket lead **32** (intraday ~49% at noon,
  WU analogs 56–58%, ≤32 ~90%); high-conviction lock at **14:00 SGT (~91%)**.
- **ECMWF EPS cross-check:** raw mean ~29–29.9, but ECMWF runs ~1.7°C cold at
  Changi → bias-corrected ~30.7–31.6 — confirms the council, not a dissent.
- **Caveat (user trust critique):** the 91% / 97.5% are **backtest** numbers.
  **Singapore has 0 live settled days** — live accuracy unproven. Only ≥31
  (mechanical floor) and ≤32 (~90%) are robust today.

## 7. Open decision (posed, awaiting user)

The only honest path to a *confident single bucket earlier than each city's
peak*:

- **(A)** Stand up a **live convective-cap feed** (radar / satellite cloud /
  precip-onset nowcast) to see the peak coming — requires a new host + allowlist
  sign-off. The single remaining lever.
- **(B)** Accept the validated 14:00 (tropical) / 15:00 (London) lock and let the
  live scorecard accrue real Singapore settled days to confirm the backtest
  holds live.

Deferred / implicitly rejected: boundary abstention ("single bucket is the
point"), coverage recalibration (under-dispersion fix, untouched), hierarchical
pooling, low-side intraday lever.

## 8. Environment

- Stdlib-only. `PYTHONPATH=. python3 -m unittest discover -s tests` (377 tests).
- Path: `/Users/43slauson/Desktop/mock projects/weather-verdict` (literal space).
- Repo `github.com/43sloane-code/clim8`, branch `main`. Latest commits this
  session: `fb6f50c` (--hours), `1b20315` (--city), `e2b0521` (basket swap).

## 9. Regression watchdog (SCAFFOLD — not yet wired, uncommitted)

Deterministic-first daily guard (harness-optimizer Mode B style): `python3`
stdlib core runs Duties 1–3 with zero LLM; a cron wrapper escalates to the LLM
only on RED/ABORT.

- `tools/watchdog_core.py` — detector. **Canary PASSES** (trips RED on known-bad
  fixtures, exit 0). **Duties 1 & 2 WIRED:**
  - Duty 1 → `storage.live_bucket_scorecard()` (no divergent ledger file): honest
    `n=0/20 live-unproven` today (0 settled days), auto-upgrades as days accrue.
  - Duty 2 → `reports/crossover_baseline.json` (distinct from the basket-MAE
    `reports/baseline.json`). Two-sided verified: GREEN clean, **RED on an
    injected 42pt regression** (WSSS@14:00 50% vs base 91.7%), GREEN on a 2pt
    within-band wobble.
  - Duty 3 ABSTAINs on empty input (no false-GREEN); REDs on real ECMWF bias
    drift (verified bias 2.9 → RED).
- `tools/intraday_ceiling_backtest.py` gained `--emit-crossover PATH` (merge
  `{icao:{HH:00:hit_rate}}`) and `--end DATE` (pin the window). **The crossover
  producer is this script, NOT the frozen `ab_backtest.py`** (which does day-ahead
  member A/B — a different instrument; the pasted wrapper conflated the two).
- `reports/crossover_baseline.json` — pinned at **--end 2026-06-20**, hours 12–15,
  both basket cities (WSSS 14:00 91.7% / RPLL 14:00 85.8%). Deterministic at a
  pinned window (verified byte-identical re-emit). **Operational contract:** the
  daily check MUST emit with the SAME `--end 2026-06-20`; re-pinning is a
  deliberate rebaseline (delete the file → Duty 2 ABSTAINs, adopts next clean run).
- `ledger/dead_candidates.jsonl` — 10 records (D01–D10) of every candidate
  already killed (the §3 "closed" list, machine-readable, with grep terms for a
  proposer pre-flight). Mirrors the `project_*` memories.
- `tools/daily-watchdog-cron.sh` — orchestration wrapper (corrected from the
  draft: crossover producer = `intraday_ceiling_backtest.py` pinned to
  `--end 2026-06-20`, NOT `ab_backtest.py`; bash-3.2-safe; `--ecmwf-bias`
  optional; canary gate + tests floor + LLM-on-RED escalation + cost cap).
  **Written, `bash -n` clean, NOT installed** — needs the watchdog files
  committed at `WX_BASELINE` and a `~/.wx-loop.env` (600) before any cron line.

**Remaining wiring gaps (each touches the pipeline — gate them, don't bundle):**
1. ✅ **Duty 1 DONE** — reads `storage.live_bucket_scorecard()` (the same scorecard
   `run.py:769` uses); honest n=0 today, becomes a real regression check once
   settled days accrue (≥3 → reality-check line; ≥20 → CONFIRMED).
2. ✅ **Duty 2 DONE** — `intraday_ceiling_backtest.py --emit-crossover` +
   pinned `reports/crossover_baseline.json`; RED on a real regression, GREEN
   within the 3pt determinism band.
3. **Duty 3 (partial)** — empty-input false-GREEN fixed; passing `--ecmwf-bias`
   already drives real RED/AMBER. Optional remaining: `tools/resolve_truth_sources.py`
   to auto-resolve truth-source strings (else the wrapper passes them in).
