# OPERATOR ADAPTATION MANUAL — every improvement of 2026-07-12, explicit, complete

*For the next session (Opus 4.8 or any model). This REPLACES the mid-session version of
this file. It covers ALL 19 weather-verdict commits (`4c3fc8c → 63f88bd`) and both
tv_trading_agent commits (`b14f423`, `da0cbbc`) from 2026-07-12. CLAUDE.md remains the
constitution; this manual is the complete delta: every new mechanism, every rule, every
refusal, every live number as of writing. Follow it literally. Where this manual and a
ledger disagree, the ledger wins — then fix this manual.*

---

# PART I — THE LAWS (read first; everything else is these applied)

## 1. The ONE LAW (unchanged, restated)
Serve the number the evidence has earned for THIS case, in the vocabulary grade the
evidence has earned, spoken by the machine from a ledger — never a blended statistic,
never a story, never from memory.

## 2. THE DRIVER-FIRST LAW (NEW — operator directive 2026-07-12, in CLAUDE.md HARD RULE 1)
Every hypothesis starts with a MACRO DRIVER — a causal reason the edge should exist at
all — never a spotted pattern. Verbatim consequences:
- **No driver = no probe.** A pattern with no driver is a coincidence not yet disproven;
  it dies only after losses. A driver gives a testable chain: what to test, what should
  kill the idea, and what regime it lives in. A driver edge announces its death IN THE
  DRIVER SERIES before the P&L/pmf shows it.
- **Hierarchy: IDENTITY > DRIVER > PATTERN.** Identities (a running max is monotone;
  sunset ends the day; the contract's quantizer) cannot die while the settlement rule
  holds. Drivers are physics/mechanisms, watchable in their own series. Patterns are
  quarantined to display or forward-falsification.
- **State the driver at its TRUE strength.** The operator caught the first application
  overstating ("TWC is the oracle" — it is not; see §12). An overstated driver produces
  a wrong death-watch as surely as no driver produces a blind one.
- **Every pre-registration must now name:** the driver, the kill condition ON THE DRIVER
  itself, and the regime it lives in. Template: `ledger/preregistered/twc_member_gate.md`.
- **A driver is necessary, NOT sufficient** (D18: real driver, sign-stable, sub-bucket →
  dead). The frozen gate stays sovereign over SIZE.
- **Driver monitors are MEASURED series with mechanical thresholds** — "the driver looks
  healthy" spoken as narration is a story.
- Run `improvement_analyzer.py --propose "<the candidate's OWN keywords>"` + grep
  `ledger/dead_candidates.jsonl` before RECOMMENDING any named candidate — conversational
  recommendations count (the AIFS/D02 near-miss, §13).

## 3. The vocabulary grades (unchanged, now MACHINE-ENFORCED — §4)
observation (banked, 100%) > physics (post-sunset / mechanical final) > climatology
(backtest %, labeled) > model (extrapolation, labeled). **banked** = on the settlement
record. **locked/final** = only when the mechanical gate prints it. **coin-flip** = both
buckets named, no pick. Legitimacy is grade-match, not outcome.

---

# PART II — THE INTRADAY SYSTEM (the day's core shipment; commits 4c3fc8c, 9d7d08b, 4c31932)

## 4. What was built and why
The 2026-07-12 Karachi miss ("32 effectively locked", settled 33) was a vocabulary breach
powered by MEMORYLESS runs: nobody tracked the settlement endpoint rising 90→91°F between
runs; "locked" had no mechanical gate; the LEADING copy carried a dismissal bias. Case
file: ISSUES_2026-07-12_INTRADAY_ACCURACY.md. Three pieces fix it:

**(a) The TAPE — `weather_council/intraday_tape.py` → `ledger/intraday_tape.jsonl`.**
Every live lead-0 run appends one read per city: `endpoint_f` + `endpoint_n` (the WU
daily-max record — the surface that pays), `cur_f` + `cur_ts` (the v3 nowcast and ITS OWN
obs timestamp). Pure derivations over the row sequence:
- `endpoint_motion(rows)` → (rising, stable): rising = max_f strictly increased on its
  latest change; stable = last 2 defined reads share one max_f; <2 reads → (False, False).
- `cur_f_sustained(rows)` → rule G4 mechanical: last 2 cur_f reads at-or-above the latest
  whole-°F AND ≥2 DISTINCT v3 timestamps (a FROZEN stamp = the London 07-11 stale
  over-read → NOT sustained; refreshing = the Karachi/Jeddah class).
- `lead_bank_rate(before_date=...)` → measured (banked, total) over COMPLETED days with an
  uncorroborated lead; replaces anecdotes in the render once n accrues.

**(b) The GRADE — `weather_council/intraday_grade.py`.** Pure classifier; chooses ALL
intraday vocabulary. Grades: `final` / `leading_coinflip` / `declining_provisional` /
`holding_provisional` / `banked_floor`. The single boolean `may_say_locked` is TRUE only:
post-sunset (REAL NOAA solar geometry from station lat/lon — includes the equation-of-time
term; validated ±5 min vs the certified lock clocks), OR peak-window-closed (the archive's
own leak-free peaked-by-q0.95 hour, computed by intraday_ceiling from the same history as
the rise pmf — unknown → NEVER closed) AND endpoint stable across ≥2 reads AND not rising
AND obs declining AND no live lead. Grain-aware: San Francisco renders °F. Post-sunset
with a still-standing lead → settles the BANKED bucket and says the lead never banked.

**(c) The WIRING — `run.py`.** `_grade_for(place, target, ceiling)` appends the tape row,
loads the day's rows, computes motion/sustainment/bank-rate, computes sunset, returns the
Grade. `_bucket_call_lines` renders EXCLUSIVELY via `grade_lines(...)` — the old
three-branch copy (LOCK on day_state alone; the "treat it as a lean, not a floor"
dismissal line) is DELETED. The JSON export certifies the grade (`_ceiling_to_dict`), so a
served read is auditable after the fact. `IntradayCeiling` gained fields:
`wu_daily_max_n`, `live_valid_local`, `peak_close_hour`.

## 5. HOW TO OPERATE IT — the three rules, no judgment allowed
1. `PYTHONPATH=. python3 run.py "<City>" --lead 0 [--market] [--intraday]` — compute the
   CITY-LOCAL date first. **Quote the BUCKET CALL block VERBATIM. Never upgrade a word.**
   If LOCK doesn't print, the word "locked" does not exist this read.
2. A lead renders as a **live coin-flip**: SUSTAINED (corroborated freshness — often banks
   via a between-obs peak) or SINGLE-READ (wait one refreshed read). Name both buckets.
   Pick neither. Never dismiss the lead; never call it banked. If the machine ALSO prints
   the market's modal, report the corroboration honestly (e.g. London 07-12: lead
   sustained BUT market 91% on the banked bucket = SPLIT corroboration — still a coin-flip).
3. The **settling surface headlines every block**: WU daily-max endpoint value + n. The
   on-hour table and any nowcast are context. Know the flip arithmetic (e.g. London
   endpoint 82°F: needs 84°F to flip 28→29; 83°F stays 28).

## 6. The tape scheduler — `tools/tape_logger.py` + `tools/com.weatherverdict.tape.plist`
- **STATUS: LOADED and verified firing** (user loaded 2026-07-12 ~14:54; 15:30 firing
  produced tape rows + lock rows). Fires 15:30 + 21:45 host-local (host = UTC+1, currently
  = London local; after late-October DST split it becomes 14:30/20:45 London — still
  serviceable, documented, do not "fix").
- Reads Singapore + London ONLY (both `_LIVE_REGISTER` + `_HOURLY_STATION`). Manila is
  EXCLUDED BY DESIGN: no v3 register consult, rows would be empty, and adding the consult
  touches a served number — out of scope by user directive 2026-07-04. Do not add it.
- The 21:45 firing is London's POST-SUNSET settle-grade read. tape_logger also runs
  lock_logger at its firings (§7) — its 15:30 run is the ONLY scheduled runner inside
  London's certification hours.
- SF is on-demand: for an SF lock, run lead-0 manually ~14:30–15:00 PDT.

## 7. Per-city lock ledger — `tools/lock_logger.py` (commits 527732a, 6d2aec2)
Executes `london_lock_instrumentation.md` §1. `CITIES` = Singapore + London. One ledger
file (`ledger/singapore_lock.jsonl`, historical name), rows carry `city`; legacy rows
migrate to "Singapore" on load. City-scoped settle (a Singapore settlement map can NEVER
settle a same-date London row — KAT'd); per-city coverage/certify/report; **Singapore's
frozen bar byte-unchanged** (hours 12–18, n≥20, −10pp). London: cert hours 13–18 local,
same frozen bar, **settles on the WU EGLC record** — the prereg's "IEM-EGLC" line was
SUPERSEDED by the 2026-07-07 "wunderground only" directive (documented in the stamped
prereg; never revert London lock settle to IEM). First London row: 2026-07-12 14:00 local,
modal 29 @ 0.73. eval_harness's Singapore view is unchanged (coverage defaults city="Singapore").

## 8. Two-city crossover guard (same commits)
`tools/accumulate.py` now emits BOTH cities' replay crossover (`--city singapore` then
`--city london`, merge-by-ICAO into `reports/crossover_now.json`).
`reports/crossover_baseline.json` re-pinned as a DOCUMENTED BREAKPOINT: WSSS values
byte-identical, EGLC added from a clean 2026-07-12 emit (13:00 .420 / 14:00 .655 /
15:00 .832 / 16:00 .933). Watchdog Duty 2 now REDs on a missing EGLC fold — by design.
**KNOWN PRE-EXISTING RED, deliberately NOT silenced:** Duty 2 was already red on
WSSS@14:00 (replay 75.0% vs pinned 79.3%) BEFORE this work. Do NOT re-pin WSSS to green
it. If it persists across several accumulate runs, adjudicate (window-roll noise vs real
regression) as its own item.

## 9. Member-bias break watch (commits ed31704, 13d2245) — G1 of the driver audit
`weather_council/member_break.py` + `tools/member_break_watch.py` + KATs; wired into
accumulate. Generalizes Duty 3b (which watched only ECMWF@Changi) to EVERY
(city, member) cell. Mechanics: raw_high − actual_high per member from settled provenance
votes (RAW bias is the driver series a provider's model-cycle upgrade breaks; the
correction consumes it downstream). First 20 settled errors per cell = FROZEN reference
(`reports/member_bias_ref.json`; code NEVER moves a written pin — re-pin is a human,
documented breakpoint). BREAK = rolling-10 mean outside the reference's seeded bootstrap
99% CI of 10-means — the same break test as the TWC monitor. Seasonal drift CANNOT
false-alarm (0.03σ/day for a month stays inside the CI; a 2σ step exits immediately —
both KAT'd). **Alert-only:** a BREAK routes a human to the fold-gated recalibration path;
it never auto-corrects. **STATUS: 0 cells, arming** — provenance logging began 07-11 and
none has settled; first pins ≈3 weeks out. The empty-join run honestly asserts nothing.

---

# PART III — THE GATES AND REFUSALS (what was killed, what is frozen, what fires next)

## 10. D19 — SF native-°F headline pmf: FAILED its own gate (commits caadb46, 9833dcf)
Pre-registered FIRST (`sf_native_f_headline.md`, committed before scoring), probed ONCE
(`reports/backtest_sf_native_f.py`, 10y KSFO, 3,628 leak-free walk-forward days, grain
sanity 3629/3649): quantizing the same residual cloud at whole-°F **LOSES** to the served
°C pmf read as a °F answer — log score fails BOTH halves (−3.372 vs −3.159; −3.314 vs
−3.165), modal hit not sign-stable (H1 +1.9, H2 −2.4). Mechanism: at day-ahead σ (~4°F)
with n≈160 residuals, a °F empirical pmf over-fits bin noise across ~15 buckets; °C
bucketing is an accidental regularizer. **Consequences you must obey:** the °C headline
STANDS on evidence; SF stays on-demand, out of the basket; **for SF always quote the
SETTLEMENT-section °F figures, never the °C headline** — and beware the °C cross-check
narration: it can manufacture a model-vs-market "disagreement" that does not exist at the
°F grain (observed live 07-12: °C said "council outlier", °F said both modal 72–73°F).
A future °F headline = a NEW mechanism (smoothed/shrunk density), its own prereg, must
beat the °C-split baseline. Do not re-run the probe as a fresh attempt.

## 11. D02 / the AIFS lesson (no commit — a refusal)
AIFS-as-member was proposed in conversation and is DEAD (D02, "noise, 3 attempts";
forecast members 0/6 as a class). The near-miss: the analyzer was run on the task phrase,
not the candidate's own keywords. **Procedure now:** analyzer + dead-ledger grep on every
NAMED candidate before recommending it. Saved to persistent memory.

## 12. The TWC 9th-member gate (commits bfb468f, 923c6e4, 498e67a, f7dba56) — FIRES ~1 WEEK
`ledger/preregistered/twc_member_gate.md`, registered at n=25/40, revised three times
pre-completion (all logged; pass thresholds G1/G2/G4 never touched):
- **The oracle correction (operator, twice):** TWC is NOT the oracle. The oracle is the
  Wunderground OBSERVATION record — station sensors TWC merely redistributes — chosen by
  the market for PUBLIC VERIFIABILITY; nothing TWC-branded ever settles. TWC's driver is
  modest: *plausibly* calibrated against the same redistributed record — and even that
  identity is an ASSUMPTION, tested by G3′.
- **Driver decomposition:** Driver A = station-MOS heritage (gain on divergence days; dies
  on offset BREAK). Driver B = settlement-convention alignment (gain on °F-boundary days;
  dies if their target isn't the WU-displayed record). Confound C = cycle FRESHNESS (a
  newer forecast beating latency-bound members is timing, not a driver).
- **G1** council+TWC beats council-alone exact-bucket, both folds. **G2** same on
  CRPS/log. **G3′ (amended — the original sign-stability test would have refused a
  PERFECTLY calibrated TWC):** offset CONSISTENT across folds — sign-stable, OR ~0 medians
  (CI-overlap) WITH TWC error-sd below the council's both folds; materially sign-flipping
  = refuse even if G1/G2 pass. **G4** independence (errors not spanned by the panel).
- **Adjudication before any Plan-3 promotion:** stratify the gain by °F-boundary
  proximity / divergence tercile / vintage proxy. Unattributable or freshness-explained →
  promote (if at all) labeled "timing advantage, driver UNRESOLVED".
- **Death-watch if shipped (decay-toward-zero REMOVED — offset→0 with shrinking spread is
  the driver IMPROVING):** offset BREAK (20-pair median outside prior 40-pair CI);
  error-sd crossover above the council's; °F-boundary stratum gain vanishing; correlation
  rise. Any one RETIRES the member before bucket-hit degradation is visible.
- **When the clock hits 40:** run the gate exactly as written. Ship or dead-letter. Do
  not renegotiate criteria at scoring time.

## 13. Own-forecast program amendments (commits 31d135e, 816465b)
- **The centerpiece insight (teach yourself this):** D15's autopsy — "morning-cloud
  information is absorbed into the running-max ratchet by ~13:00" — is an IDENTITY
  out-competing a DRIVER. The whole intraday conditioning family (D07/D08/D11/D13/D15)
  died racing the ratchet, not because its drivers were fake. P2b is the hour BEFORE the
  identity eats the information.
- **P2b (`p2b_1200_forward.md`, clock 8/60):** criteria 1–5 untouched; ADDED adjudication —
  at gate time the gain must live in the CLOUDY tercile (the physics' prediction); clear-
  tercile or uniform gains are recorded as noise-consistent; wrong-stratum passes proceed
  labeled "driver UNRESOLVED". Criterion 5 (live-feed degeneracy) recognized as the
  driver-integrity kill.
- **P3 (`p3_dayahead_model.md`, Stage-B carve-out clock 6/40, model frozen at 4bf504b, no
  re-tuning):** ADDED adjudication — a Stage-B pass must attribute its gain (council-vs-own
  divergence tercile × predictor-signal days) AND show council-error independence (the G4
  convention) before any P4 shadow promotion; unattributable → "driver UNRESOLVED".

## 14. Learning-loop convention (commit 63f88bd)
CAUSE ≠ DRIVER: postmortem attribution says WHERE an error lives; a driver says WHY. The
loop already refuses driverless hypotheses coarsely (lessons `_TRANSFORM`: INPUT is
logged, never a candidate). The convention (in the lessons.py docstring — an instruction
to the HUMAN at the promotion gate): before promoting any shadow-PROMOTED candidate,
cross-reference the driver diagnostics for its (city, cause) cell — member_break
(pipeline change), recency_bias (seasonal drift), Duty 2/3 (climatology/contract drift).
Diagnosis found → take the diagnosed path; the candidate is EVIDENCE of a break, not a
lesson. No diagnosis → promote labeled "driver UNRESOLVED". Budget priority for
driver-attributed candidates = recommendation only.

---

## 14b. THE POST-PEAK SETTLEMENT-LAG STUDY (2026-07-13 — five markets, five frozen preregs, ALL ACCRUING)

The first market-microstructure candidate ever probed here (dead ledger had NO market
entries; paper_pnl measured a different trade — day-ahead modal bets). Driver: information
latency between the public WU settling record (which we read mechanically) and thin
prediction-market pricing. Design (frozen per city BEFORE scoring, one attempt each):
lead-0 settled snapshots; leak-free state at issue from IEM archives (obs ≤ issue hour;
archives extended over end-gaps by live fetches of the same fixed past window); entry =
first snapshot/day with the shipped 2-consec DECLINING rule at/after the city's certified
hour; BUY the running-max bucket at its RECORDED best_ask only (no ask = UNTRADEABLE,
counted); win (1−ask)/ask, lose −1; six criteria incl. n≥20 floor, both-halves sign,
hit>mean-ask, untradeable<50%, capacity≥$50.

| Market | Prereg / probe | Days | Filled | Status |
|---|---|---|---|---|
| Singapore | postpeak_lag_trade.md / backtest_postpeak_lag.py (FROZEN — never edit) | 9 | 3 (.96/.95/.91, all won, ~2¢ gaps) | ACCRUING 9/20 |
| London | postpeak_lag_trade_ldn_jed.md / backtest_postpeak_lag_v2.py | 2 | 0 — both NO-ASK; 14/16 evening snapshots read HOLDING (EGLC °C plateau; predicate rare BY DESIGN, not re-tuned) | ACCRUING 2/20 |
| Jeddah | same file / v2 script | 1 | 1 @ .78 → +28.2% | ACCRUING 1/20 |
| Karachi | postpeak_lag_trade_khi.md / v2 (additive CFG) | 1 | 1 @ .87 → +14.9% | ACCRUING 1/20 |
| SF | postpeak_lag_trade_sf.md / v2 (additive grain-F branch: 2°F buckets hi-INCLUSIVE, settled-°F containment win) | 2 | 1 @ .83 → +20.5% | ACCRUING 2/20 |

**What the 15 pooled days actually say:** (1) the near-lock predicate is as accurate live
as certified — 15/15 settled the running-max bucket; (2) the dominant market behavior is
SELLER EXIT, not price lag — 8/15 decision days had NO ASK (the mirage confound, named in
the prereg before scoring); (3) fills are 6/6 winners at $160–533 capacity — BUT the
**central finding**: the three fattest fills (Jeddah .78, Karachi .87, SF .83) are ONE
afternoon — 2026-07-09, the phantom-register day AND the only multi-city manual afternoon
session. Two readings held open in the SF stamp: (a) register chaos left stale books
everywhere at once (episodic edge), or (b) sampling artifact — "fat fills on 07-09" may be
"we only looked on 07-09"; Singapore's automated 4×/day sampling (the only clean sampler)
shows ~2¢ gaps, which supports (b). **Rules for the next session:** never re-tune the
predicates/parameters after data (frozen; dead-ids D20–D24 reserved); never quote the
pooled 6/6 as evidence (one correlated session dominates); re-score ONLY by re-running the
frozen scripts as snapshot days accrue past n=20 (~4–6 weeks; the automation logs what's
needed — nothing to build); a PASS goes to a forward paper ledger first, never capital.

## 14c. THE CALIBRATION STUDY (2026-07-13 — win-rate-by-price on our own ladders, all five markets)

Method imported from Jon-Becker/prediction-market-analysis (`win_rate_by_price`) with the
two fixes that repo lacks and one addition: (FIX 1) inference clustered by MARKET-DAY via
seeded bootstrap — their analyses treat each trade as independent Bernoulli, but all
trades in one market share one resolution, so their CIs are pseudo-replicated; (FIX 2)
chronological era split; (ADDITION) the tradable ASK-side curve beside the de-vigged mid
— the cost model their headlines omit. Tool: `reports/weather_market_calibration.py
[City]` (descriptive report, read-only, seeded; NOT a prereg — re-running is allowed).
Data: 2,904 point-in-time bucket-price/outcome pairs, 89 market-day clusters.

**Universal findings (every slice — pooled, SG 21 clusters, London 20, Karachi 4,
Jeddah 4, SF 5):**
- **Sub-5¢ longshot burn at the ask: −0.93 to −1.00, 6/6 slices** (London a perfect
  0/487; SF burns flat through 15¢). The strongest regularity ever measured about our
  own markets. STANDING GUARD: never buy sub-5¢ weather buckets.
- **Mid-probability (0.30–0.50) negative at the ask** (pooled −0.165, CI excl. 0).
  STANDING GUARD: never buy mid-probability weather buckets at market.
- **Books are calibrated at the MID in every slice** — the market is honest where it's
  readable; the losses live in the cost of touching it.

**City-specific (know these before quoting any slice):**
- Singapore: favorite residue ~2–3¢ on thin asks; its 0.95–0.99 bin prints roi +0.028
  with a POSITIVE CI — **an all-wins-bin artifact**: a cluster bootstrap cannot
  manufacture unseen losses, so the CI reflects fill-price variation only; rule of three
  puts the true loss-rate bound near 26% at n=10, and one 0.97 loss erases ~36 wins.
  NEVER quote that bin as an edge.
- **Jeddah contributed the study's one OBSERVED favorite-loss** (a ~0.97 ask that went
  to zero) — the tail Singapore's sample lacked, demonstrated rather than theoretical.
- London: favorites structurally UNQUOTED (0–3 asks ever) — independent corroboration of
  the post-peak mirage; plus a cheap-bin inversion FLAG (5–15¢ wins 28% vs 9.7% mid,
  CI excluding; 15–30¢ wins 8.7% vs 21.4%, CI excluding — likely 4–5 tail days;
  flag-grade, not tradable).
- SF: opposite-signed cheap-bin flag (15–30¢ over-performs, noise-grade); on its flat
  2°F ladders favorites barely exist before the afternoon.
- Karachi/Jeddah/SF are 4–5 clusters: accrual-only; any bin CI there is arithmetic, not
  inference (Karachi's 0.30–0.50 "+0.896 significant" is the canonical trap — one
  recurring winner across one afternoon's snapshots).

**Relationship to the post-peak study (§14b):** two independent instruments now agree —
the afternoon favorite residue is ~2–3¢ on thin asks with real (now observed) tail risk,
and London's answer shelf is empty. NOTHING here amends any frozen prereg; the post-peak
ledgers remain the only trade-verdict instruments.

## 14d. THE TRADE-TAPE KILL TESTS (2026-07-13/14 — the S2a schema, both venues)

The distribution-over-verdicts law applied: instead of waiting weeks of forward accrual,
the post-peak question was put to HISTORICAL executed-trade tapes on both venues, each
under its own frozen prereg (hindsight-winner design = an UPPER BOUND built to KILL;
survival is permission to keep testing, NEVER tradability — the asymmetry clause is
stamped on every file).

| Probe | Prereg | Outcome |
|---|---|---|
| Kalshi S2a (KXHIGHTSFO) | kalshi_s2a_kill_test.md | **ACCRUING n=67 < 100 floor** (the public API retains only ~67 days — discovered, pinned, and the erosion arrested by the S2b logger's preserve duty). Facts: kill-rate 0.0% vs the ≥80% bar; gaps 1.0/1.2/2.9/10.3/23.0¢ deciles, mean 8.8¢; 67/67 traded afternoons; CLI mismatches 0/67. Re-scores automatically when the cache reaches ~100 (re-run the untouched probe). |
| Polymarket v1 (5 cities) | polymarket_tape_kill_test.md | **ABORT unscored by its own gate** — bare title-slugs resolve to wrong events (prior-year °F Londons); universe was over-broad (June Tokyo/HK/Chicago days). Zero gap numbers computed; design spent; diagnosis stamped. |
| Polymarket v2 (verified resolution) | polymarket_tape_kill_test_v2.md | **SURVIVES at n=50: killable 26% vs the ≥80% kill bar.** Gaps 0.3/0.8/3.4/9.8/24.8¢ deciles, mean 8.2¢, halves 7.7/8.7¢. Per city: SG 6.7¢ (n21), London 10.7¢ (n20), KHI 7.1¢ (n4), JED 5.6¢ (n4), SF 3.4¢ (n1). |

**The two findings that outrank the verdicts:**
1. **Two venues, one shape** — Kalshi and Polymarket afternoon-winner gap distributions
   are nearly identical (medians 2.9 vs 3.4¢, means 8.8 vs 8.2¢). The residue is a
   property of afternoon weather-market pricing, not one venue's microstructure.
2. **Quotable ≠ traded, demonstrated** — London's ask shelf is structurally EMPTY (the
   ask-fill preregs' finding) yet its EXECUTED record carries the largest gaps (10.7¢
   mean): trades print via arriving orders without standing asks. The tape tests and the
   ask-fill preregs answer DIFFERENT questions; neither substitutes for the other.

**Standing rules from this section:** the five ask-fill post-peak preregs remain the
SOLE tradability instruments (still ACCRUING, untouched); never quote a tape-test
survival as an edge (upper bound by construction); v1's lesson is procedural — event
resolution requires VERIFICATION (end-date window + bucket-unit grain), never bare
slugs; all three caches are committed point-in-time data (the Kalshi one is
irreplaceable). New host data-api.polymarket.com allowlisted (user-approved 07-14),
same read-only posture as every market host.

## 14e. THE HEALTHCHECK ADJUDICATION PRECEDENT (2026-07-14 — how a DRIFT report is handled)

The nightly healthcheck flagged seven issues; "fix all" was executed as the laws
prescribe — each item got its NAMED mechanism, one got a gated probe, and the probe's
death was recorded. Full document: reports/healthcheck_adjudication_2026-07-14.md.
This section is the PRECEDENT for every future DRIFT report — diff against it, don't
re-argue.

| Flag class | Prescribed response (applied 07-14) |
|---|---|
| MAE drift vs pinned baseline | Adjudicate vs the ~0.1°C live-feed noise floor. +0.0513 = under it → flag STANDS, no tune, NO baseline re-pin (re-pinning to silence = the forbidden move). Act only if it persists and grows. |
| Interval coverage / under-dispersion | The one ledger-permitted fix (cand-50 scalar variance-match) was pre-registered and probed ONCE → **FAILED → D26** (Manila H1/H2 + SG H2 improved, SG H1 already-calibrated didn't move; all-four-cells bar). Autopsy: the candidate is inert where no defect exists — a future CONDITIONAL design needs its own registration + driver clause. The flag now stands as a measured limitation with a DEAD challenger — do not re-probe the scalar form. |
| PIT warm tilt (mean bias) | Its legal challenger (recency bias) is re-adjudicated NIGHTLY by the healthcheck and keeps losing. No hand-recentering, ever. |
| Rank-histogram U (raw panel) | Known; already served with its explanation (the council serves the residual cloud, not member spread). Never a defect to fix. |
| Monitoring-coverage gaps (DISP tiers) | Note for human review; never tuned on one run. |
| Boilerplate/city-list drift | Check WHERE the stale text lives first: the repo's healthcheck boilerplate was already accurate; the stale "8-city/London+HK" text is in the SCHEDULED TASK's prompt (user-side edit). |
| C7 UNVALIDATED / market beats council | The standing law, gate holding by design. Nothing to fix. |

Probe-craft lessons banked in the prereg (dispersion_inflation.md): the "n=222" in
healthcheck reports is 2 attrs × 111 days (per-city day floors must be calibrated to
THAT); the backtest residual streams now come from _walk_forward's ADDITIVE `resid`
return (monitoring-only); loaders are IO-repairable pre-scoring, floors are not
lowerable post-scoring (v1→v2 required zero candidate numbers read, and had it).

## 14f. THE BAND-VS-MARKET-MODAL FIX (2026-07-14 SF — commit c3b88f4)

The specimen: SF 07-14 served day-ahead band 28–30°C (82%) while the cross-check line
directly below it said "the independent signals agree on 26°C; the COUNCIL (29) is the
OUTLIER" — and WU settled 79°F = 26°C (model_prob 3.9%, market 86.5%). Diagnosis: the
07-02 directive "widen the band toward divergent signals" existed as PROSE (the
cross-check line) but not as MECHANISM (the band is pmf-top-k-to-80% only,
run.py `_bucket_call`). Class: a shipped directive not wired into the surface it governs.

What shipped, and what deliberately did NOT:
- **Shipped gateless (rule 2, labeling only):** `_band_market_flag` — when the market's
  modal bucket sits outside the served band, the band line flags it explicitly and quotes
  the MEASURED band coverage (73–74.5%) beside the pmf-self-assessed %. Plus
  `_market_modal_c` (the °F→°C market-modal conversion extracted from `_cross_check_lines`
  — one source of truth for both call sites). KAT: tests/test_band_market_flag.py.
- **NOT shipped — gated:** actually EXTENDING the band to cover the market modal is a
  served-number change. Frozen prereg: ledger/preregistered/band_cover_market_modal.md
  (driver: market aggregates beyond the panel, measured 43% vs 40%; kill-on-driver: the
  market must beat the pmf where they disagree, ≥2×; floor n≥15 conditioned days). The
  cheapest-decisive-test law was run FIRST: 46 historical day-ahead settled snapshots
  contain only **3 conditioned days** — no verdict is reachable, so it is an ACCRUING
  forward clock (~6.5% condition rate, months out). **D27 reserved on FAIL.**
- The analyzer matched D14 on "band" — adjudicated in the prereg: D14 killed a band
  NARROWING design; this candidate never touches the modal or the pmf. The
  consensus-override anti-directive holds: no market blending, display band only.

The wider lesson this section pins: when a directive lives only in prose next to the
number it should govern, the number will contradict it on exactly the day it matters —
grep the render path for the directive's actual mechanism before trusting the line.

## 14g. THE 00Z FINE-GRAIN READ + PATTERN LAYER (2026-07-16 — tools/finegrain_read.py)

**The specimen.** KXHIGHTSFO-26JUL16, a 69-vs-70 °F boundary: obs peaked 69°F, the
CLI-catch question (does Kalshi's record read above the obs max?) was worth ~50¢ of
book. The instrument that resolved it FIVE HOURS before settlement, with zero new
hosts: the NWS CLI settles the sensor's CONTINUOUS max, which lives in two METAR
fields the whole-°F obs record hides —
- **T-groups** (`T02060183` → 20.6°C, tenths precision, hourly), and
- **6-hourly max groups** (`1 0206` at 00/06/12/18Z — the max over the whole prior
  6h INCLUDING between-obs minutes; the 00Z ob ≈16:56 PDT covers the SF afternoon peak).
07-16's print: 6h-max == T-group == 20.6°C = 69.1°F → CLI 69 → 68-69 won; Kalshi went
0.99 within minutes. 07-15's mirror: a 2:18 PM between-obs peak the obs never showed
printed CLI 74 vs obs 73.

**The tool:** `PYTHONPATH=. python3 tools/finegrain_read.py --station KSFO
--date <D> --tz America/Los_Angeles --pattern-hour 14` (KAT:
tests/test_finegrain_read.py). Run it at EVERY °F boundary day; the frozen decision
form is "≤ X.4°F → CLI X; ≥ X.6°F → CLI X+1" stated BEFORE the 00Z ob lands.

**The driver lesson (this closes the offset-series trap).** The CLI-over-WU
divergence series read +2,+3,+2,+1 (n=4, "always positive") — then 07-16 printed the
first 0. The offset is NOT a constant: it is "CLI catches a between-obs spike WHEN
ONE EXISTS." Spike-existence is the driver condition, and it is regime-dependent
(heat-event days spike; flat marine days don't). The n=182 archive query measured it
directly: of days with running max ~69°F at 14:00, only 43% ever touched 69.5°F.
That instrument outranked the n=4 series, flipped the live favorite correctly, and
is now standing kit:

**OPERATOR DIRECTIVE (2026-07-16, standing): every full-stack verdict and intraday
validation ALSO quotes the pattern layer** — (a) the archive pattern: the
`pattern_rate` conditional (days matching today's running-max-at-hour → fraction
reaching the next threshold, n quoted); (b) the recent-days specimens (this week's
settles and how today's shape compares — e.g. 07-14 late re-heat / 07-15 hold /
07-16 flat-top). Historical pattern + previous days ride alongside the pmf and the
book in every read; none of the three is served alone. (D18 boundary: the pattern
layer INFORMS the read, it never moves a served number — SF regime-lean as a lever
is dead, D18.)

# PART IV — tv_trading_agent (commits b14f423, da0cbbc)

## 15. Funding carry (grade A−)
- Driver: leverage demand paying shorts via PUBLISHED 8h funding. The cond arm already
  trades the driver (sits out when trailing-30d ≤ 0 — currently sitting out).
- **ADDED — DRIVER COMPRESSION watch** in funding_forward.py: rolling-90d funding
  annualized vs the annualized cost floor (the FX-carry slow-death mode, distinct from
  the fast 30d flip). **First live read: +0.7%/yr vs 1.2%/yr floor → COMPRESSED** — a
  watch, not a verdict (one FLAT quarter can't split structural from cyclical). Check it
  on every weekly run.
- Kill conditions: flip (trailing ≤0), compression (90d ≤ cost floor). NOT in the driver
  series and never modelled away: venue/FTX, liquidation mechanics. UNLEVERABLE stands
  (~1–2× only).

## 16. VRP (grade B: driver real, extraction FAILED — verdict stands)
- The hardened-gate FAIL (0/11 OOS, margin unmodelled) is UNCHANGED. Do not re-claim.
- **ADDED — driver fields at open** in vrp_forward.py (additive keys; settlement math and
  old rows untouched): `trailing_rv` (30d, 365-cal), `ex_ante_spread` = IV − RV30 (the
  driver itself), `regime` (BULL/FLAT/BEAR, 90d ±10% rule). Every settled window is now
  ATTRIBUTABLE: thin-spread loss = the driver said no ex-ante; thick-spread loss = tail
  realization. Driver-death signal: ex-ante spread persistently ≤ friction floor.

## 17. Consensus protocol (instrument A, edge F — measured)
- **ADDED — DRIVER-FIRST TIER MIX line** in the render (labels only): "13 PATTERN + 2
  STATISTICAL voters, 0 DRIVER-backed" + the replay verdict (PF 0.51 → abstention is the
  measured correct behavior). "15 mechanisms agree" is one tape read 15 pattern-shaped
  ways; the gates and the off-switch carry the epistemics, not the vote count.
- No conviction math, thresholds, or inputs changed. Forward issuance log deliberately
  skipped (0.5% issuance ≈ years to n). The repo's driver edges (VRP, funding) stay
  OUTSIDE the aggregator by design.

---

# PART V — LIVE STATE, PROCEDURE, PROHIBITIONS, FILES

## 18. Live state at writing (updated 2026-07-13)
| Item | State |
|---|---|
| 07-12 books CLOSED | London settled **28** (82°F final — the sustained 29-lead NEVER banked; the machine's refusal to pick was correct; market's 91% vindicated). Jeddah settled **36** (97°F — H4 CLOSED, the corrected 36 lean was right). Karachi 07-12 settled 33 (the original miss's day). Every 07-12 city resolved. |
| Tape | Plist LOADED + firing on schedule; lead-bank ledger live in renders ("0/2" quoted honestly — SG/London-only composition caveat applies) |
| Post-peak lag study | Five markets ACCRUING (§14b) — 15/15 predicate accuracy, 8/15 untradeable, 6/6 fills won but one correlated session (07-09) dominates; frozen, re-scores at n≥20 |
| TWC clock | ~27/40 (gate frozen + thrice-revised, §12; fires within days) |
| p2b | 8/60 · PoP 4/15 · London lock accruing (first rows 07-12/13) · Singapore lock bins ACCRUING |
| Member-break watch | 0 cells, arming (~3 weeks to first pins) |
| Duty 2 | RED on WSSS@14:00 — PRE-EXISTING, left standing; adjudicate only if persistent |
| Healthcheck 07-14 DRIFT report | Fully adjudicated (§14e; reports/healthcheck_adjudication_2026-07-14.md): all flags stand-or-dispositioned, dispersion challenger DEAD (D26), zero hand-tunes |
| Funding driver | COMPRESSED (90d +0.7% vs 1.2%/yr floor); cond arm sitting out; VRP window open (settles 07-24) |
| Open follow-ups | TWC gate at n=40 (the next real decision); post-peak ledgers accrue unattended; **Kalshi expansion: S0+S1 EXECUTED** — hosts allowlisted (user-approved); pilot CONFIRMED SF/KXHIGHTSFO (station=KSFO pinned 3 ways; book 34k vol, 6/6 two-sided; cross-venue truth split banked: CLI 76 vs WU 74, same station same day). tape kill tests DONE (§14d): Kalshi ACCRUING n=67 (0.0% kill-rate; API retention pinned), Polymarket v1 ABORT→v2 SURVIVES n=50 (26% vs 80% bar) — upper bounds only, ask-fill preregs stay the tradability instruments; S2b logger BUILT+wired; kalshi plist load still pending (user — verify with: launchctl list | grep kalshi) |

## 19. Daily procedure (in order; the automation does most of it)
1. Anything to adjudicate? Read `reports/tape.launchd.out.log`, accumulate log tails,
   Duty 2/3 verdicts, member_break output, DRIVER COMPRESSION line (weekly, trading).
2. Verdicts: quote the machine (Part II §5). Never override; never re-pick a coin-flip.
3. Before ANY "improve/optimize/add X": analyzer --propose with X's OWN keywords + dead-
   ledger grep + name driver/kill/regime or STOP (Part I §2).
4. Any change: labeling/instrumentation ships with KATs + `make check` (weather) /
   `verify.py` (trading); anything touching a served probability goes prereg → frozen
   probe → both-halves sign-stability → one attempt.
5. Commit flow: branch → commit (pre-commit runs the gate) → `merge --ff-only` → push.
   Never bundle structural with docs/prompt changes. Do NOT commit automation-churn
   ledger files with code unless they're part of the change.
6. When a clock fills (TWC first): run its gate EXACTLY as frozen; ship or dead-letter;
   stamp the prereg; update this manual's live-state table.

## 20. NEVER-DO (the complete refusal list as of tonight)
Day-ahead accuracy levers (0/19, σ-ceiling) · relitigate D01–D19 (grep first!) · AIFS or
any driverless NWP member (D02 class) · naive °F headline for SF (D19) · London lock
settle on IEM (superseded 07-07) · Manila model/feed changes (user directive) · re-pin
WSSS Duty-2 to silence the red · move a written member-break or crossover pin in code ·
call a lead "banked" or say "locked" when the grade doesn't print it · pick a side on a
machine-labeled coin-flip · claim "improved" from a ship (only n at the frozen bar) ·
compare live runs as A/B (frozen data only) · LLM-as-signal, latency arb, Windy vision,
paid-API-for-accuracy · leverage funding carry ≥3× or re-claim VRP "clears" · remove the
protocol's off-switch or its tier-mix line · let the learning loop self-promote (L2 is
permanently human-gated) · launchctl from the agent shell (user-side only) · re-tune any
post-peak-lag predicate/parameter after seeing data (five frozen preregs, D20–D24
reserved; one-attempt binds the DESIGN, waiting for n is allowed) · quote the study's
pooled 6/6 fills as evidence (one correlated session, 07-09, dominates) · trade any of it
live (a PASS goes to a forward paper ledger first, and no PASS exists).

## 21. Complete file map of the day
```
NEW  weather_council/intraday_tape.py + tests/test_intraday_tape.py
NEW  weather_council/intraday_grade.py + tests/test_intraday_grade.py (unittest — the
     first pytest-style version ran ZERO tests under the gate; that bug is the reason
     the header warns about it)
NEW  weather_council/member_break.py + tools/member_break_watch.py + tests/test_member_break.py
NEW  tools/tape_logger.py + tools/com.weatherverdict.tape.plist + tests/test_tape_logger.py
NEW  reports/backtest_sf_native_f.py (D19 probe — do not re-run as an attempt)
NEW  reports/backtest_postpeak_lag.py (Singapore, FROZEN post-scoring — never edit)
NEW  reports/backtest_postpeak_lag_v2.py (London/Jeddah + additive Karachi CFG + additive
     SF grain-F branch; °C default path regression-checked unchanged)
NEW  ledger/preregistered/postpeak_lag_trade{,_ldn_jed,_khi,_sf}.md (all with INTERIM stamps)
NEW  reports/weather_market_calibration.py (win-rate-by-price on OUR ladders, city-slice
     arg — findings, guards, and the per-city traps digested in §14c; DESCRIPTIVE ONLY)
NEW  ledger/preregistered/{twc_member_gate,sf_native_f_headline,member_bias_break_watch}.md
NEW  docs/{INTRADAY_PROTOCOL,NWP_LITERATURE_MAP,DRIVER_AUDIT,OPUS_ADAPTATION_MANUAL}.md
MOD  weather_council/intraday_ceiling.py (endpoint n, v3 stamp, peak_close_hour,
     peak_close_hour_from_history)
MOD  run.py (_grade_for; grade-driven _bucket_call_lines; grade-certifying JSON export)
MOD  tools/lock_logger.py (per-city) + tests/test_lock_logger.py + tests/test_live_floor.py
MOD  tools/accumulate.py (two-city crossover; member-break step) · reports/crossover_baseline.json (+EGLC)
MOD  tools/lessons.py (driver-adjudication docstring) · tools/twc_forecast_logger.py (oracle correction)
MOD  ledger/preregistered/{london_lock_instrumentation (EXECUTED), p2b_1200_forward,
     p3_dayahead_model} (adjudication additions) · CLAUDE.md (driver-first rule; pointers)
tv_trading_agent:
NEW  DRIVER_AUDIT.md
MOD  vrp_forward.py (driver fields at open) · funding_forward.py (DRIVER COMPRESSION watch)
MOD  protocol.py (tier-mix line)
```

**Adapt by doing, in order:** read Part I; run one lead-0 verdict and hold your own output
against §5's three rules; check §18's open follow-ups (Jeddah settle, London 21:45);
check whether the TWC clock has hit 40 — if yes, that gate is your session's real work,
and every rule for it is already written.
