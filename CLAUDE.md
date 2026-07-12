# OPERATOR MANUAL — weather-verdict (auto-loaded every session; do not deviate)

You are operating a measured, gate-disciplined temperature-settlement prediction system.
Every number this system serves was earned by a ledger. Your job on day-to-day tasks is to
RUN the mechanisms, not redesign them. Deeper context: HANDOFF.md, SESSION_STATE.md,
FINDINGS.md, ledger/preregistered/*.

## THE ONE LAW (all six recurring failure classes reduce to this)
Serve the number the evidence has earned for THIS case, in the vocabulary grade the evidence
has earned, spoken by the machine from a ledger — never a blended statistic, never a story,
never from memory. When a verdict/accuracy issue appears: name its class → apply the shipped
mechanism → new mechanisms get ONE pre-registered probe → fold gate → labeling-first →
the ledger certifies.

## DAILY RUNBOOK (the commands; everything is PYTHONPATH=. from repo root)
- Full-stack verdict:      python3 run.py "<City>" --lead N [--intraday] [--market]
  * Compute the CITY-LOCAL date first; --lead is relative to the city's today, not yours.
    (Recurring miss: London/Singapore tick over midnight and lead-1 targets the wrong day.)
- Daily automation runs ITSELF (launchd → tools/accumulate.py): snapshots for CITIES =
  Manila, Singapore, London; TWC/PoP/p2b/lock ledgers; watchdog chain (emit crossover →
  resolve truth config → compare, cities RPLL,WSSS,EGLC); settlement audit; db snapshot.
  Do NOT launchctl load anything from the agent shell (classifier-denied; user-side task).
- Read the machine's own status: python3 tools/eval_harness.py  (liveness heartbeats,
  vocabulary guard, ranked NECESSARY NEXT). Trust its ranking over your instincts.
- Before ANY "improve/optimize/clean up" task:
    python3 tools/improvement_analyzer.py --propose "<the task>"
    grep -il "<lever keywords>" ledger/dead_candidates.jsonl        # D01–D17
  If it matches a dead entry: cite the ID and STOP. Do not relitigate.

## CITY CONFIG (settlement is per-city; never assume)
| City      | Anchor           | Truth feed                   | Grain / bucket rule        | Basket |
|-----------|------------------|------------------------------|----------------------------|--------|
| Singapore | WSSS Changi      | WU oracle (live, current)    | whole-°F → °C round-half-up| YES    |
| Manila    | RPLL             | WU oracle (live)             | whole-°F → °C round-half-up| YES (serving only; improvement OUT OF SCOPE by user directive) |
| London    | EGLC City Airport| SETTLE: WU oracle (live) · BACKTEST: IEM-EGLC (10y deep) | whole-°C, round-half-up (17.6→18) | YES    |
| San Francisco | KSFO         | WU oracle (live)             | WHOLE-°F (2°F market buckets)| NO — on-demand; headline pmf still °C (registered blocker: sf_verdict_blockers.md). For SF quote the SETTLEMENT-section °F number, never the °C headline. |
| Hong Kong | HKO Observatory  | HKO open-data                | 0.1°C, FLOOR (28.6→28)     | removed |

> London's SETTLE≠BACKTEST split (user directive "wunderground only", 2026-07-07): the market
> pays on the WU EGLC record, so SETTLEMENT (live lock, forward scoring, the SETTLEMENT-RECORD
> display) reads `wunderground_daily_series` — it catches between-obs peaks the whole-°C IEM
> METAR rounds away (07-07: WU 90°F=32 vs IEM 31). The multi-year BACKTEST keeps the deep IEM
> archive (WU history is too shallow to calibrate 10y). Config: EGLC ∈ storage._WU_SETTLE_TZ
> and council._WU_SETTLE_C_ICAOS, but "london" ∉ council._WU_TRUTH_STATIONS. Do not collapse
> the two or revert settlement to IEM (KAT: test_london_settlement_is_wunderground_backtest_is_iem).

## INTRADAY LOCK — the only conviction lever (certified semantics, 2026-07-06)
- VOCABULARY IS MACHINE-CHOSEN (2026-07-12 Karachi fix — docs/INTRADAY_PROTOCOL.md, read it
  before ANY intraday read): quote the BUCKET CALL block verbatim. "LOCK/final" prints iff
  post-sunset (real solar calc) or peak-closed+endpoint-stable+not-rising+declining; a cur_f
  lead prints as an unresolved COIN-FLIP (sustained per the tape = corroborated, single-read =
  wait); the settling `wunderground_daily_max` endpoint (value+n) headlines the block. The
  machine's cross-run memory is ledger/intraday_tape.jsonl (endpoint motion, rule-G4 lead
  sustainment, measured lead-bank rate) — weather_council/intraday_{grade,tape}.py.
- The running max is a RATCHET: "banked ≥ N" = observation-grade floor, can never go down.
  The FLOOR is not the SETTLE — holding days climb (July: London 37% @16:00, SG ~30% @14:00).
- "DECLINING" requires the last TWO reads below runmax−0.3°C (certified: single-read declines
  are false 16–30%; 2-consec thirds it, +30min). One tick-down = still HOLDING = the trap.
- While HOLDING: serve "INTRADAY FLOOR — PROVISIONAL (peak NOT formed)". NEVER headline a
  90%+ lock on a holding day (user caught this twice). Quote the STATE-conditional rate.
- Served raise-risk is (state × meteorological season × hour), n≥30 cell, state-only fallback
  (certified, Brier +6–14% both cities both halves). It is season-aware — trust it.
- Lock clocks (decipher the CURVE, not the clock; lock on persistent decline):
  * Singapore: peak ~13:00 SGT; 16:00≈97.5% certified; 17:00≈99.5%; FINAL post-sunset ~19:10.
  * London: peak 15:00 (July IQR 13–16, late-spike 25% after 16:00); July: 17:00≈92%,
    18:00≈97%, 19:00=100%. Spring/autumn runs earlier — season matters.
  * San Francisco: peak ~14:00 PDT; easiest city (trap 14%); declining@15:00≈96%, @16:00≈99%.
- Register (WU v3 max24) is floor-raise-only and bounded on THREE sides (weather_council/
  sources.py `_fuse_live_floor`, KAT test_live_floor.py): (1) TIMING — a pre-dawn register may be
  YESTERDAY's peak, so it needs attribution (a42ffa2); (2) MARGIN — it must sit within a between-
  obs spike (~3°F) of today's own obs, else it's an un-attributable carryover (a42ffa2); (3)
  PHANTOM CAP — it can NEVER exceed WU's own daily-max endpoint (`wunderground_daily_max`), which
  already aggregates real between-obs peaks (6533fca: Jeddah 07-09 register 102°F vs settled 100°F
  served a phantom 39). A corroborated register (== daily-max) still fuses — that is its legit
  07-04/07-07 job of leading the lagging hourly rows. If a served intraday bucket looks 1-2°F hot,
  suspect the register: cross-check `wunderground_daily_max` and the hourly obs before trusting it.
- Certainty hierarchy (vocabulary must match): observation (banked floor, 100%) > physics
  (post-sunset/persistent-decline = final) > climatology (backtest %, label "backtest,
  uncertified" unless in the certification table) > model (day-ahead pmf).

## DAY-AHEAD — honest posture
- The council does NOT beat the market day-ahead (measured: 40% vs 43%). Day-ahead output is
  the pmf + BAND, spoken as a coin-flip near boundaries. Do not manufacture conviction.
- Surface divergent signals (TWC, market, regime, own-model-informational); NEVER dismiss
  them to zero; widen the band toward them. Never override the council's modal with a story.
- Own-model (k-analog, D17): informational line ONLY, WSSS-trained, never blended/served.

## HARD RULES (deviations here are how this system has failed before)
1. NO served-number change without: pre-registration file (frozen BEFORE scoring) → leak-free
   walk-forward probe → sign-stable on BOTH chronological halves (and both cities if shared
   surface) → ship + KAT + stamp the prereg CERTIFIED. Fail → dead ledger entry, one attempt.
2. Labeling/honesty fixes ship WITHOUT the gate; anything touching a served probability or
   pick does NOT.
3. A fix is a HYPOTHESIS. Never say "improved" from a ship — only from n at the frozen bar.
4. Never claim a change helped by comparing two live runs (feed revisions ≈ 0.1 CRPS noise);
   only frozen-data A/B counts.
5. Anti-directives (measured dead, do not spend effort): day-ahead accuracy levers (0/17,
   σ-ceiling physics), consensus overrides, retro-computed lock rows, LLM-as-signal,
   latency arb, Windy-vision, paid-API-for-accuracy.
6. Frozen artifacts — do not touch: p2b logger design (its own single-read day_state is
   pre-registered; leave it), tercile constants 84.3/97.7, crossover baseline (re-pin only as
   a documented breakpoint), training-table state cols (OLD single-read semantics — regenerate
   before conditioning anything new on them).
7. WU_API_KEY is the free site key — single point of failure; if it dies, that is continuity
   work, not an accuracy lever.

## OPEN CLOCKS (waiting is the work; do not peek-and-claim)
- p2b 12:00 forward ledger: gate at n≥60 settled non-fallback (ledger/p2b_1200.jsonl).
- TWC 9th-member: gate at n≥40 settled pairs (never asserted into the blend before).
- Lock certification bins: n≥20 per hour (Singapore); uninstrumented hours need the two
  user-side plists (midday/evening) loaded.
- PoP regime split: n≥15 dry days. D17 carve-out: Stage-B-only re-gate at n≥40 snapshots,
  model frozen at 4bf504b, no re-tuning.
- Registered next refactors (do properly, with KATs, not hot-patched):
  ledger/preregistered/london_lock_instrumentation.md (lock_logger per-city + London
  crossover guard). sf native-°F headline pmf: ATTEMPTED, FAILED the gate 2026-07-12
  (D19 — the °C pmf is the better °F answer at day-ahead σ; naive °F grain over-fits).

## FILE MAP (where each mechanism lives)
- run.py — verdict render (SETTLEMENT_REFERENCE, holding-cap, banked/final lines)
- weather_council/intraday_ceiling.py — the lever: _day_state (2-consec), state_late_risk
  (state×season), remaining-rise pmf
- weather_council/sources.py — feeds: WU oracle (WU_GEO/WU_LOCATION/_WU daily+v3),
  IEM METAR (fetch_metar_daily; grain detect frac_f>frac_c&≥0.4), _IEM_OVERLAY_TZ (EGLC)
- weather_council/council.py — _WU_TRUTH_STATIONS, PINNED/STRICT anchors
- weather_council/storage.py — _WU_SETTLE_TZ, settle_market_snapshots, scorecards
- tools/accumulate.py — the daily spine; tools/watchdog_core.py — duties 1–3
- tools/eval_harness.py — liveness + vocabulary + directives; tools/lock_logger.py — cert ledger
- tools/p2b_1200_logger.py / twc_forecast_logger.py / singapore_pop_logger.py — accrual clocks
- tools/backfill_obs_history.py (--source wu|iem) / build_training_table.py — datasets
- ledger/dead_candidates.jsonl (D01–D17) + ledger/preregistered/*.md — the memory that binds
- data/{wsss,eglc,ksfo}_hourly_iem.jsonl (10y each) + wsss_hourly.jsonl (3y WU grain)

## COMMIT FLOW
branch → add → commit (pre-commit runs the full 420-test gate; message ends with
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>) → checkout main → merge --ff-only →
push origin main → branch -d. Never bundle a structural change with a prompt/text change.
