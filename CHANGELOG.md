# CHANGELOG — weather-verdict

Complete iteration history of the weather-verdict project, **every commit, omitting
nothing**, generated from `git log` of the live repo
(`Desktop/mock projects/weather-verdict`).

- **Range:** 2026-06-06 (initial commit `b5c2005`) → 2026-07-29 (`1e6862a`)
- **Totals:** 251 commits across 37 active days
- **Format:** newest conventions aside, entries are listed **chronologically**
  (oldest first within each date), grouped by commit date. Each line is
  `` `<short-hash>` <commit subject> ``.
- **Branching:** work happened on short-lived feature branches merged `--ff-only`
  into `main` (branch names are preserved in the repo: `daily-verdict-automation`,
  `singapore-wu-native-intraday`, `replace-hk-with-manila`, `audit-integrity-fixes`,
  `watchdog-regression-guard`, `paper-pnl-validation`, `improvement-analyzer`,
  `live-scorecard-reality-check`, `obs-history-cache`, `high-conviction-range`,
  `bucket-call-honest-conviction`, `auto-intraday-lead0`, `fix-launchd-verdict-tcc`,
  `fix-live-basket-settlement`, `fix-settle-station-budget`,
  `wunderground-settlement-anchor`, `hk-intraday-accumulator`,
  `abbacktest-target-cache-fix`).
- **Snapshot copy:** `../weather-verdict copy/` is a frozen clone as of 2026-07-19
  (227 commits, ending at `062bee9`); it contains **zero commits unique to it** —
  this history is a superset and is authoritative.
- **Uncommitted working-tree state at generation time (2026-07-29):** modified
  accrual ledgers (`ledger/intraday_tape.jsonl`, `ledger/kalshi_snapshots.jsonl`,
  `ledger/singapore_lock.jsonl`, `ledger/singapore_pop.jsonl`,
  `reports/streams/kalshi_s2a_cache.jsonl`) plus untracked additions including
  `KIMI_MOBILE_CARRYOVER.md`, `.github/workflows/hk-accumulate.yml`,
  `HANDOFF_CODE_BUNDLE.txt`, `Weather Council.command`,
  `ledger/finegrain_divergences.jsonl`, `ledger/kaus_cli_wu.jsonl`,
  `data/oejn_hourly_iem.jsonl`, `data/opkc_hourly_iem.jsonl`, and several
  `reports/backtest_*.py` scripts.

---

## 2026-06-06 (3 commits)

- `b5c2005` Initial commit: weather-verdict backtested council
- `e49492d` Defer to the market when the settlement-station transfer is stale
- `f9d94e2` Always compare London verdicts against the Wunderground EGLC record

## 2026-06-07 (18 commits)

- `6406e65` Add recommend-only mechanism-convergence layer + verification infra
- `d400431` Add quantum-inspired fidelity-kernel study (backtested, no edge, recommend-only)
- `32820cb` Re-source London backtest truth from the EGLC settlement sensor (IEM METAR)
- `380d242` Add enforceable hypothesis→deploy feedback loop with recommend-only gate
- `2033b6e` Add systematic cross-timescale verdict rule (DM-tested, second→year)
- `4c80412` Source live "now" temperature from the settlement sensor for HK and London
- `9b52dda` Add recommend-only model-vs-market section to the daily health check
- `7d6b0f0` Stop tracking daily-generated health-check reports
- `3c8ab2a` Add network-free test suite for the SafeHTTPClient sandbox
- `b617f1f` Add a local stdlib-only pre-commit test gate
- `e548c58` Ignore all generated reports/*.txt, not just the health-check reports
- `5ebf695` Retire the AR(1) shrinkage cascade — measured dead weight
- `47ad35d` Extract observation step into observation.py
- `11bbf21` Split the test_calibration monolith into per-module test files
- `fb01e0a` Record A5 decision (option b) and correct stale VHHH roadmap notes
- `b6a52ef` feat: add Weatherbit as a sandboxed, recommend-only forecast source
- `44d5c00` feat: track Weatherbit head-to-head vs the council in the daily health check
- `128f7d4` feat: gate health-check recommendations on significance + decompose error

## 2026-06-08 (3 commits)

- `c3b573a` Add recommend-only monitoring surface, scoped config, behavior-preserving refactors
- `8be3f42` Add ensemble-calibration verification + spread-skill; narrow basket to settlement set
- `90951c5` Anchor settlement to the verdict's exact station in both ledgers

## 2026-06-09 (1 commit)

- `fa3f113` Surface leak-free walk-forward stream + bucket-verdict/recency evaluation

## 2026-06-10 (4 commits)

- `0287a22` Record official HKO June 2026 daily extract (01-09)
- `ae8d1fa` File official HKO April & May 2026 daily extracts for reference
- `3381bdd` Replace April 2026 HKO extract with the full official table
- `2d8d56e` Anchor verify() settlement to the verdict's exact station identity

## 2026-06-11 (7 commits)

- `1a66e8c` Extend HKO June 2026 provisional extract through 06-10
- `8626ea5` Fix sub-degree settlement to range-containment (floor) for the basket cities
- `45b098b` Wire HKO official Daily Extract as the fresh HK settlement feed
- `619bb73` Retire provisional-CSV display x-ref now the live HKO feed is wired in
- `12f9853` Add ECMWF AIFS as a 9th council member — lifts high-bucket hit for both basket cities
- `dbf034d` Revert AIFS 9th member — the iteration-1 "gain" was an uncontrolled measurement
- `90e5245` Add frozen-data A/B harness for honest council-config evaluation

## 2026-06-12 (3 commits)

- `0cf4d20` Candidate 52: TC halt gate — HK abstains when a TC cone threatens Hong Kong
- `b658224` cand47: CLOSE AIFS-for-HK (disjoint-fold gate); keep the gate that caught it
- `d5b4d28` cand48: intraday running-max dead-bucket eliminator (read-only, today only)

## 2026-06-17 (12 commits)

- `d080055` cand53: authoritative settlement backfill + true-settlement bucket scoring
- `9154b29` tighten: remove all dead code & stray f-strings — repo is now pyflakes-clean
- `88b361a` fix: per-station Sources in settle so recent days settle (realized_label)
- `2960a76` tighten: ruff safe-fix pass (real-bug rules) — comprehensions, simplify, pyupgrade
- `fe90b05` feat(intraday-ceiling): lead-0 conviction lever — sharpen the bucket from running max + remaining-rise
- `4afe460` feat(hk): prospective HKO hourly accumulator — the only path to HK intraday conviction
- `4646eaf` feat(verdict): honest BUCKET CALL — conviction from the bucket, intraday over day-ahead
- `6422adb` docs: mobile handoff status page (continue on iOS)
- `3c8836b` chore(hk): commit HKO accumulator ledger to data/ so the archive syncs to mobile/cloud
- `39745f1` feat(verdict): high-conviction RANGE call — a tight bucket span, not a coin-flip single
- `fd152eb` feat(verdict): show LIVE served-vs-settlement rate, not the backtest-optimistic %
- `209e455` feat(verdict): auto-use the intraday lever on the settlement day (lead 0)

## 2026-06-19 (8 commits)

- `d8382a6` feat: replace Hong Kong with Manila as a tracked basket city (intraday-capable)
- `8384b0f` feat(verdict): wire Wunderground as the displayed settlement anchor (IEM = cross-ref)
- `bbe2283` fix(ab_backtest): key the frozen cache by target date (stop stale-snapshot crash)
- `a8e1df7` feat(ab_backtest): --member flag to A/B any Open-Meteo model; close high-res NWP
- `36af3c3` feat(ensemble): prospective EPS-ensemble spread accumulator (flow-dependent lever)
- `c1c0872` fix(intraday_ceiling): label the station by name, not hardcoded "London City Airport"
- `0bcfc73` chore: declutter working tree; extend .gitignore for scratch/caches/backups
- `c969532` feat(manila): anchor backtest truth + live feed on Wunderground (settlement oracle)

## 2026-06-20 (3 commits)

- `ca17599` feat(singapore): add Singapore (Changi WSSS) on the Wunderground backbone
- `e2b0521` chore(basket): swap London -> Singapore in the tracked basket (now Manila + Singapore)
- `1b20315` feat(intraday_ceiling_backtest): parameterize --city; validate Singapore lever

## 2026-06-21 (4 commits)

- `fb6f50c` feat(intraday_ceiling_backtest): --hours flag finds the high-conviction crossover
- `1764138` Add deterministic regression watchdog (Duties 1-2 wired) + dead-candidate ledger
- `06437a5` Singapore intraday lever: read the WU settlement feed (whole °F), not IEM
- `9dcb92b` Cache immutable WU/IEM station history; always re-fetch the recent tail

## 2026-06-24 (3 commits)

- `08779b4` Fix float-hour :02d crash in verdict rendering (WU-native regression)
- `be8fda5` Add realized paper-P&L instrument: the money question, honestly
- `e5c1457` Repair live-basket settlement + verify: WU truth + Duty 3 resolver

## 2026-06-27 (1 commit)

- `040c1c6` Add daily Singapore verdict automation (full-stack report writer)

## 2026-06-28 (2 commits)

- `0b3540c` Add improvement analyzer (evidence-based, dead-lever-aware) + cleanup
- `b16b199` Fix daily-verdict launchd jobs: TCC blocks bash reading a Desktop .sh

## 2026-06-29 (1 commit)

- `21c31a2` Close intraday convective-cap conditioner (D11): fold-unstable, dead

## 2026-06-30 (1 commit)

- `67dd662` Lead the verdict with the intraday lock; day-ahead becomes a preliminary band

## 2026-07-01 (2 commits)

- `358d8d2` Add day-ahead cross-validation panel — the council is no longer the only signal
- `f1008dd` Forward-log The Weather Company's own forecast — start the 9th-member accrual clock

## 2026-07-02 (5 commits)

- `20c28df` Pre-registered kill of the Singapore two-bucket band (D14) + corrected skill verification
- `f7e26be` Open the Singapore PoP regime-split — pre-registered, instrumented, clock started
- `9ee7272` Live certification ledger for the intraday lock — the flagship claim gets a track record
- `11ecea3` Full-stack audit: fix 6 plumbing defects the end-to-end production run exposed
- `0b56452` Print the day-ahead pmf in the verdict — the named pair must come from the model, not a story

## 2026-07-04 (20 commits)

- `f112e98` Lock display: banked-vs-FINAL distinction — the 07-04 crux fix (additive, probe-backed)
- `26ce72d` Self-evaluation harness — the machine generates the honest brief the agent relays
- `e06912f` eval harness: NECESSARY NEXT — machine-ranked accuracy directives, not just honest state
- `603ef2f` Scope the improvement directives to Singapore only (user directive 2026-07-04)
- `6a6148e` Live-register floor feed (#1) + settlement cross-check (#2) — the 07-04 API fixes
- `76197f3` Code-health pass: lint-clean, de-duplicated, stale docs fixed (no behavior changes)
- `fa2ff2d` Cover the last operational bases: daily true-settlement audit + off-machine DB snapshot
- `982a07b` State-conditional NOT-FINAL risk: say whether TODAY's peak has formed (holding vs declining)
- `9c78ce7` Header honesty: 'Bayesian bias + Monte-Carlo pmf' -> what the code does (gated bias correction + empirical residual-cloud pmf). Class-2 vocabulary fix per the settled-issue-classes law.
- `109a659` Docstring straggler: same vocabulary fix as the section header
- `b2b42c2` Mechanism re-evaluation: wake the watchdog, fix its input chain, re-pin its baseline, pair TWC rows
- `bed46b8` Autonomy liveness sentinel — nothing watched the watchers; now the brief does, daily
- `81bb67e` Accumulate the 3-year settlement-grain WSSS dataset (1095 days, all seasons)
- `387b809` Own-forecast build plan (research-grounded, gated) + the 10-year training archive
- `95ee2aa` P1 complete (frozen 3,649-day training table, 100% ERA5 coverage) + P2 pre-registration
- `628e922` P2 closed as D15 on its own frozen gate (one attempt, as pre-registered)
- `155e966` Promote London City Airport (EGLC) into the daily tracked basket — following its own placeholders
- `4bf504b` P2b forward instrument (the one legitimate D15 descendant) + P3 prereg FROZEN before code
- `d726732` P3 closed as D17 on its own frozen gate: Stage A cleared 8/8, Stage B failed vs the council
- `97736c9` P2b: premature-day guard — a 01:15 SGT run logged midnight obs as runmax12 (caught live)

## 2026-07-05 (1 commit)

- `c4f2a44` Honesty cap: stop serving a HIGH lock % while the day is HOLDING (user-caught, twice)

## 2026-07-06 (7 commits)

- `3446e1c` Register candidate: persistent-decline lock trigger (from 10y curve-pattern scan)
- `8b2a6ae` Add San Francisco (KSFO) as a data/pattern layer — live verdict path BLOCKED, documented
- `e6a63ec` Unblock San Francisco verdict: live KSFO IEM overlay + correct °F grain detection (2 of 3)
- `e2231d8` Re-anchor San Francisco on its live Wunderground oracle feed (KSFO has a WU feed)
- `8f17f62` Ship BOTH certified lock upgrades (first full gate clears) + basket seam fixes
- `62bf5c5` Defect sweep: 2 doc fixes + 2 registered instrumentation gaps (1 false alarm retracted)
- `7199f29` CLAUDE.md — the operator manual every session auto-loads (use the mechanisms, don't deviate)

## 2026-07-07 (6 commits)

- `aa5d416` Register defect (user-caught live): register floor-raise over-reads across a bucket boundary
- `541ec7a` Settlement-truth DEFECT (user-caught): London 07-07 settled 32, we locked 31
- `75fb5aa` FIX the London settlement undershoot: consult the WU live register for EGLC (was Singapore-only)
- `3bb52ae` Pin London settlement to the Wunderground oracle (settle≠backtest split)
- `cee75d3` Headline the SETTLEMENT RECORD block with the WU oracle high (not IEM)
- `cd164b4` Render the SF SETTLEMENT RECORD block in native °F (was hardcoded °C)

## 2026-07-08 (1 commit)

- `1dc5847` Settle WU-oracle cities at T-1 once the city-local day is over (tz-aware early-settle)

## 2026-07-09 (10 commits)

- `a42ffa2` Attribution-gate the WU v3 register so a pre-peak carryover cannot floor today (intraday-ceiling correctness)
- `9c56dfc` Add daily-LOW market support and wire the KSFO intraday lever (whole-°F)
- `453b8b0` Add Karachi (Jinnah / OPKC) — WU-anchored whole-°C settlement, London pattern
- `4850b20` Add Jeddah (King Abdulaziz / OEJN) — WU-anchored whole-°C settlement, London pattern
- `cbaee3e` Commit 10-year IEM archives for Karachi (OPKC) + Jeddah (OEJN)
- `6533fca` Register phantom guard: WU max24 can never exceed WU's own daily-max
- `70fca2c` Doc: CLAUDE.md register line now describes all three guards
- `cccd6e6` Session state: record the 2026-07-09 WU-city expansion + register hardening
- `0f5f2c7` Gate test: SF marine-layer regime day-ahead lever — DEAD (D18)
- `71b76fd` Add informational DATA INTERPRETATION line to the verdict

## 2026-07-10 (15 commits)

- `bb2e9b8` Data interpretation line now reads the LOW too (both high and low)
- `35d1805` Phase 6a: UTC persisted-timestamp helper (charter: UTC non-optional)
- `cff2d49` Phase 6b: soft-failure surfacing — settlement swallows stop being silent
- `cb29377` Phase 6c: WU_API_KEY env-first (rotatable without a code change)
- `06efc22` Phase 1: allowlist clob.polymarket.com (read-only book-data host)
- `49fa81b` Phase 2: clob_book — order-book parse + executable depth-walk (pure)
- `010068a` Phase 3: book_snapshots table (additive migration)
- `1e21dad` Phase 4: order-book capture — archive executable depth beside each price snapshot
- `0dee5aa` Phase 5: executable paper P&L — walk the archived book, not the mid
- `2e0a9ef` Phase 6d: C7 edge report — CI-width (precision) readout
- `cc82f73` Fix book-capture focus set: drop Jakarta (no market), use Karachi; document CLOB pipeline + Phase 0
- `9f0976b` Fix two Phase 4/6b defects surfaced by the live healthcheck
- `aff87f3` Wire focus-basket book capture into the daily spine (executable-P&L accrual)
- `ce3e506` Fix SF cross-check °C/°F unit-mixing (labeling; no served-number change)
- `aba9366` Daily accrual: forward-log ledgers + db snapshot (2026-07-10)

## 2026-07-11 (28 commits)

- `0c92714` Add TWC (Weather Channel) as a display-only cross-reference with its data-derived WU bias
- `e48b64b` Plan 3 Phase 0: issue-time provenance capture (learning-loop foundation)
- `b6fa91c` Plan 3 Phase 3: post-mortem engine — decompose every settled error
- `5ed53d8` Plan 3 Phase 4: lessons aggregator + budgeted candidate queue (the throttle)
- `8d7f3d6` Plan 3 Phase 5 — shadow scorer + human promotion gate (L1→L2 boundary)
- `da94d9c` Fix wall-clock time-bomb in book_snapshot_coverage test fixture
- `3c8e714` Plan 4 Phase 0 — TWC endpoint probe + pre-registration (PROCESS, evidence-only)
- `5287fbc` Plan 4 Phase 1 — Sources.twc_forecast_daily() cross-reference fetch surface
- `41989f5` Plan 4 Phase 2 — pin the TWC accrual contract + harden the silent-drop gap
- `2fc6018` Plan 4 Phase 3 — signed-offset estimator (which way TWC runs vs the oracle)
- `f7babc7` Plan 4 Phase 4 — TWC signed-offset cross-reference block (report + JSON)
- `862d67a` Plan 4 Phase 5 — TWC independence audit + FINDINGS §29 (read-only)
- `2eafce1` Live-floor phantom cap honours cur_f, not just the lagging daily-max endpoint
- `b98fa41` guard-v2: Phase 0 — recon, ground truth, incident freeze (no serving-path code)
- `aa1ca2a` Code hygiene: audit doc + safe bug/bloat fixes (no served-number changes)
- `8fd882f` xref-analyst Phase 0 — pre-registration + source audit (doc only, no code)
- `2acc3ff` xref-analyst Phase 0 — add Wunderground/TWC forecast to the source registry
- `13df33f` xref-analyst Phase 0 — WU is THE oracle; IEM/TWC/etc are cross-references only
- `e48c185` served-number campaign Phase 0 — umbrella pre-registration (§§0-3, doc only)
- `8bec444` served-number campaign Phase 0 — integrity_flags field + inert filter (SAFE)
- `bd3145b` served-number campaign Phase 0 — Gate 0-B branch declaration (BRANCH C)
- `d9c9717` served-number campaign WP-1 addendum — fetch_resolution fix (pre-reg alone)
- `8fb47f1` served-number campaign WP-1 — fetch_resolution exact-match, fail-closed NO_MATCH
- `9a45b16` served-number campaign WP-2 addendum — daily-max local-day regroup (pre-reg alone)
- `0a5f38f` served-number campaign WP-2 — wunderground_daily_max regroups onto local civil day
- `2aca4cf` served-number campaign WP-5 + WP-6 addenda (pre-regs, doc only)
- `1f65723` served-number campaign WP-5 — quarantine unparseable market buckets from de-vig
- `409703d` served-number campaign WP-6 — compact_buckets contiguous interior (no dropped mass)

## 2026-07-12 (26 commits)

- `22bce87` served-number campaign WP-3 addendum — phantom cap on outage (pre-reg alone)
- `fe5916d` served-number campaign WP-3 — phantom cap degrades explicitly on endpoint outage
- `a24241d` served-number campaign WP-4 — STOP-AND-REPORT: reclassify to FREEZE (doc only)
- `44aa182` served-number campaign WP-7 addendum — _resolve_truth window+1 FREEZE (pre-reg)
- `5cf7e0e` served-number campaign WP-7 — window+1 frozen; non-positive-window guard + KAT
- `83786e0` served-number campaign Phase-7 hygiene batch (§A/D/G deferred, all SAFE)
- `4c3fc8c` intraday grade+tape — mechanical vocabulary gate for the 2026-07-12 Karachi miss
- `9d7d08b` docs: INTRADAY_PROTOCOL.md (one-page, literal) + CLAUDE.md pointer + close H1-H4/F2
- `4c31922` tape_logger — close the London intraday-coverage gap (tape was Singapore-only)
- `caadb46` pre-register SF native-°F headline pmf probe (frozen before scoring)
- `9833dcf` SF native-degF headline pmf FAILS its pre-registered gate -> dead ledger D19
- `6180534` docs: NWP literature map — chapter concepts pinned to implementations, dead-ledger dispositions, and textbook corrections
- `527732a` london lock instrumentation — per-city lock ledger + two-city crossover guard
- `6d2aec2` docs: stamp london_lock_instrumentation EXECUTED (WU supersession + WSSS drift finding recorded)
- `bd7c9c1` docs: adaptation manual for the 2026-07-12 changes + CLAUDE.md pointer
- `bfb468f` driver-first hypothesis law + TWC 9th-member gate pre-registered at n=25/40
- `923c6e4` correct the TWC driver statement — TWC is NOT the oracle (operator-caught)
- `498e67a` twc gate: the oracle is chosen for public verifiability — nothing TWC-branded settles
- `f7dba56` twc gate: collaborative driver revision — G3', two-driver decomposition, freshness confound
- `a0d9166` driver-first audit of the council mechanisms + G1 registration
- `ed31704` member-bias break watch — every (city, member) cell, alert-only (G1 executed)
- `13d2245` docs: stamp member_bias_break_watch EXECUTED (honest arming status recorded)
- `31d135e` driver-first audit: own-forecast program — D15 is the hierarchy explaining itself
- `816465b` p3 prereg: driver-adjudication amendment (omitted from 31d135e by an edit failure)
- `63f88bd` driver-first audit: the learning loop — cause != driver, adjudication convention added
- `a77d94a` docs: complete adaptation manual — all 19 weather + 2 trading commits of 2026-07-12, explicit

## 2026-07-13 (23 commits)

- `1013d61` pre-register post-peak settlement-lag trade probe (frozen before scoring)
- `60bffd2` post-peak settlement-lag probe: first scoring = ACCRUING (n=9<20, frozen floor)
- `457d74c` pre-register post-peak lag probe for London + Jeddah (frozen before scoring)
- `8fa9586` post-peak lag probes, London + Jeddah: first scoring = ACCRUING (n=2, n=1)
- `1d9fcc7` pre-register post-peak lag probe for Karachi (frozen before scoring)
- `7ba8d69` post-peak lag probe, Karachi: first scoring = ACCRUING (n=1)
- `81a6bcc` pre-register post-peak lag probe for San Francisco (frozen before scoring; grain-F rules explicit)
- `d94a9d8` post-peak lag probe, SF: first scoring = ACCRUING (n=2); correlated-session finding now three-books strong
- `d8a4dd7` docs: manual updated with the post-peak settlement-lag study (§14b) + live-state refresh
- `6ef5dd1` weather-market calibration — Jon-Becker win_rate_by_price method imported with fixes
- `8ce373a` calibration report: additive city-slice arg; Singapore slice run
- `4ca1144` docs: manual §14c — the five-market calibration study digested
- `d2c1aac` register the Kalshi weather-market expansion (staged, S0 = user allowlist decision)
- `8a0857a` kalshi expansion S0 EXECUTED — both hosts allowlisted (user-approved), verified, lineup enumerated
- `aff40f8` kalshi expansion S1 EXECUTED — pilot confirmed SF/KXHIGHTSFO; seam pre-registered
- `b8a0654` distribution-over-verdicts law (operator) + kalshi S2 reordered to historical kill test first
- `e3cddae` pre-register S2a Kalshi historical kill test (frozen before scoring)
- `b34e2e6` kalshi S2a first scoring: ACCRUING at n=67 (API retention window discovered); facts banked
- `f172aab` kalshi S2b — dual-venue logger built, wired, accruing (instrumentation only)
- `53bad7e` allowlist data-api.polymarket.com (user-approved) + pre-register the Polymarket trade-tape kill test (frozen before scoring)
- `53013d5` polymarket tape v1: ABORT unscored (frozen gate; wrong-event slugs diagnosed) + v2 registered
- `1f833a9` polymarket tape v2: SURVIVES at n=50 (upper bound, not killed) — cross-venue shape matches Kalshi
- `d70d2b3` docs: manual §14d — the trade-tape kill tests (both venues), verdicts + the two findings that outrank them

## 2026-07-14 (5 commits)

- `1c960ff` pre-register scalar dispersion inflation probe (frozen before scoring; D10/cand-50 lineage checked)
- `b75cde1` healthcheck DRIFT report adjudicated per the laws; dispersion inflation probed once -> D26
- `19c7ef0` docs: manual §14e — the healthcheck adjudication precedent (diff, don't re-argue)
- `c3b88f4` Band-vs-market-modal honesty flag (gateless label) + frozen extension prereg
- `86990ac` Manual §14f: band-vs-market-modal fix (labeling shipped, extension gated)

## 2026-07-15 (3 commits)

- `fca4d9b` Conditional member-dispersion cloud: frozen probe FAILS both cities -> D28
- `d7620f1` Audit batch 1/2: crash, data-integrity, security, ops fixes (7-agent sweep)
- `e45595b` Audit batch 2/2: labeling/honesty, dead code, gate coverage, audit report

## 2026-07-17 (1 commit)

- `062bee9` finegrain_read: the 00Z / T-group settlement read + pattern layer (manual 14g)

## 2026-07-19 (10 commits)

- `53971de` regenerate WU-derived season base rates and settled records (cross-reference feed; NWS anchor unchanged)
- `d9a3663` W2: archive legacy weather_agent.py (incompatible verdicts.db schema; pre-council prototype)
- `49ca018` W1: escape upstream strings before innerHTML injection in index.html
- `7a92752` W6: harden intraday_ceiling._city_key against substring city-name collisions
- `727a825` W8: verify_skill.py cosmetics and season-base-rate _meta provenance header
- `1628273` W4: serialize /api/verdict runs and use city-local default target in server.py
- `ee7dcfd` W5: document DNS-rebinding TOCTOU as accepted risk in security.py
- `c30edb7` W7: harden handoff bundle markers and manifest
- `b40a472` W3: harden verify input generator
- `030d83b` W9: update ROADMAP checkboxes to current state

## 2026-07-23 (1 commit)

- `348e0c5` audit: integrity bug fixes + behavior-preserving dedup (bugs & bloat only)

## 2026-07-24 (1 commit)

- `6020061` docs: hourly-grain trap — cross-check CLI-grade 6h extremes before high-conviction intraday serves

## 2026-07-25 (1 commit)

- `4297b10` SF settlement display: lead with NWS CLI (Kalshi oracle), demote WU to secondary cross-ref

## 2026-07-28 (9 commits)

- `4a556a3` SF intraday ceiling: CLI-scale seam guard (labeling-only) + KATs
- `86c087e` docs: 07-27 CLI-catch lesson + prereg for gate-bound seam-shifted pmf probe
- `56326d5` SF CLI-seam guard: distance predicate, data cleaning, S2 archive verifier, MC+10y validation
- `d8cade4` docs+data: distance-guard CLAUDE.md sync, prereg addendum, 10y CLI truth cache, validation logs
- `1a0480b` mc_verdict_sim: regime analysis — catch driver by meteorological season
- `11d9e5e` docs: full-stack verdict memory — 07-27 corrections incorporated across all surfaces
- `f0678f9` S2 CLI archive verification (IEM-AFOS mode) + frozen seam-shift probe scored: DEAD (D29)
- `a323053` docs+ledger: mandate re-oriented Kalshi-primary; D29 dead entry; prereg stamped FAILED
- `d09cce5` accrual: 07-27 tape/snapshot/seam rows + db snapshot (spine duty, no code)

## 2026-07-29 (2 commits)

- `08dfbd7` CLI-seam guard: book-aware strike lines (07-28 blindness fix) + shared seam loader
- `1e6862a` docs: CLI-scale guard bullet now book-aware (07-28), D29 reference

