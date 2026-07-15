# FULL-REPO CODE AUDIT — 2026-07-15

Seven parallel line-by-line audits (run.py · council.py · sources+security ·
storage/compare/market · intraday/calibration modules · tools/ · reports+tests):
**~130 findings**. Analyzer run first (matched D18 on the keyword "improve" —
adjudicated: D18 is an accuracy lever, this is a code audit; no accuracy lever was
touched). Everything below the FIXED line shipped gateless under rule 2
(crash/robustness/labeling/honesty/dead-code); everything under REQUIRES-PREREG is
**recorded, deliberately NOT fixed** — those change served numbers or certified
mechanisms and must route through HARD RULE 1.

## FIXED (commit refs in git log, full gate green before merge)

**Crashes (all reproduced or line-verified):**
- healthcheck `_walk_forward` sparse-city early return had arity 9 vs 10 — one thin
  city killed the entire nightly monitor (`tools/daily_healthcheck.py:182`).
- intraday tape readers crashed on a JSON-valid non-dict line / dateless row —
  one bad ledger line silently degraded every future run to a tape-less grade
  (`weather_council/intraday_tape.py`).
- TWC amber divergence line formatted a None `mae_council` → TypeError destroyed the
  whole rendered verdict (run.py `_twc_cross_reference_lines`).
- `recency_weighted_bias([])` → StatisticsError; now a clean ValueError.
- kalshi truth duty: CLI "M" sentinel passed is-not-None and TypeError'd forever on
  the same date; now numeric-gated.
- `--market` add-on fetches were UNWRAPPED in main — a market API error aborted the
  run after log_verdict (verdict logged, never printed). Now fail-soft + soft-failure.
- market pagination: transient mid-page error now returns pages parsed (parity with
  sibling fetchers) instead of killing the fetch.

**Data integrity:**
- IEM archive day2-EXCLUSIVITY was compensated in exactly one caller: every other
  METAR fetch truncated its final day, and the obs-cache had a structural ONE-DAY
  HOLE at the cutoff for every overlay city (EGLC/OPKC/OEJN). End-inclusiveness is
  now a property of the raw fetchers.
- The WU settlement spine (daily/hourly/current/v3/daily-max) only TYPE-checked its
  temps, contrary to the module contract — a corrupt 9999 could become the settling
  max or the live-floor cap. All five paths now screen via `_wu_temp_f` (bool-excluded
  + plausibility band), with `qc["rejected"]` finally countable.
- `bool` passed every numeric temp gate (JSON `true` → 1.0 °C / f2c(True) ≈ −17 °C);
  `_clean_temp` and the WU paths now exclude it.
- `live_bucket_scorecard` scored tail/range buckets by EDGE-EQUALITY, not containment
  — a served 79 inside a winning "78-79°F" counted as a MISS. Measurement-honesty fix
  (rule 2): the honest hit-rate consumed by eval_harness/watchdog was systematically
  UNDER-reported. **The number will step up on °F/tail cities — that is the
  correction, not an improvement claim (rule 3).**
- market grain FAIL-OPENED to °C on description drift → now cross-checked against the
  bucket labels' own unit; contradiction → event refused (fail-closed).
- `match_market`: unparseable date label matched ANY date (accepted now only as sole
  city candidate); "July 15, 2026" no longer parses day=2026.
- HKO daily-extract blocks for a different month are skipped (wrong-year keying).
- Meteostat `station.id` sanitized (alnum ≤12) before URL interpolation.
- `_fuse_live_floor` fallback cap now DECLARED (`[DEGRADED_CAP]`) per its own docstring.
- SF settlement block: WU-oracle fetch failure for the target day is now flagged
  (“high above is the IEM cross-ref, NOT the oracle”) instead of silently mislabeled.

**Security (SafeHTTPClient):**
- allowlist matched hostname only → `:port` and `user:pass@` smuggling closed
  (non-443 ports and userinfo refused, initial AND redirect hops).
- corrupt gzip → SecurityError (was raw zlib.error crash); scoped-IPv6 getaddrinfo
  answer → SecurityError (was ValueError crash); HTTPError response closed on retry.

**Ops/idempotency:**
- accumulate's snapshot idempotency key mixed SGT and UTC dates → UTC both sides.
- `crossover_now.json` persisted across runs → a failed emit fed watchdog Duty 2
  yesterday's numbers as "current" (false GREEN). Reset per run; missing city now
  reads RED as designed.
- lock ledger: atomic tmp+rename writes + flock (three unsynchronized schedulers);
  per-line-tolerant reader. p2b: atomic rewrites of the LEDGER **and the 10-year
  WSSS archive** (IO only; the frozen design untouched), tolerant readers.
- kalshi_logger: trade `n` falls back to integer `count` before defaulting (seam
  rule 5 / probe parity — count_fp absence was banking n=0 tapes and pushing the
  frozen S2a test toward its illiquidity ABORT); probe flag-only cache rows no
  longer block banking; >3-page tapes marked `"truncated"`. +2 KATs.
- book_logger CLI target date now city-local (`place_today`), not host-SGT.
- Config staleness: improvement_analyzer CITIES +London; verify.py fallback basket
  → Manila/Singapore/London (was "London, Hong Kong"); watchdog default cities
  +EGLC; eval_harness hardcoded "0/14"/coverage stats → ledger-derived / stamped.
- Makefile gate: analog_shrink self-test (479 orphaned lines) + p2b selftest wired in.
- eval_harness/pop/dead-ledger JSONL readers: per-line tolerant.

**Labeling/honesty (rule 2) + dead code:**
- SF intraday: °C hardcodes over °F buckets fixed (fallback line, actionable range,
  header rule); the °C-vs-°F "day-ahead OVERRIDES" comparison no longer fires
  cross-unit. "pmf tail" → "non-modal pmf mass" (it includes below-modal mass).
- cross-check signal renamed `twc-oracle` → `twc-forecast` (TWC never settles);
  duplicated tracked_forecasts SQL extracted to `_twc_raw_high` (was already
  drifting), conn closed on all paths, soft-failure on DB error.
- "UNMEASURED (n=0)" no longer claimed when the offset READ failed.
- source check for WU-truth cities was a feed agreeing with ITSELF — now marked
  "same-feed, tautology, no independent cross-check" (council + render).
- seasonal-analog thin-archive failure now disclosed on truth_source.
- WU throttle (RateLimitError) no longer silently re-anchors Manila/Singapore/SF onto
  a lagged Meteostat station — re-raised per the STRICT-anchor contract.
- Dead: run.py `to_json` (unused, drift-prone duplicate), `fetch_hko_daily_max`
  (zero callers), unused °F tuple element in `fetch_metar_daily`, dead `pass`,
  `--json --market` low-fetch (result discarded); stale KSFO-overlay comments fixed
  (truth: _IEM_OVERLAY_TZ = EGLC/OPKC/OEJN, KSFO deliberately not overlaid);
  bucket_contract mass invariant assert → raise.
- settle_market_snapshots: one WU fetch per (station, day) instead of per row;
  corrupt buckets_json skips the row, not the batch; verify/settle fetch failures
  now soft-failure-recorded.

## REQUIRES-PREREG — recorded, NOT fixed (HARD RULE 1 routes; do not hot-patch)
1. **Seasonal-analog served ≠ validated incoherence (the audit's biggest structural
   finding):** out-of-season the SERVED blend uses analog-learned bias but
   `_validate` always scores the plain/recency path — the residual cloud, hit-rate
   tier, CRPS and bucket sim measure a method that is NOT the one served
   out-of-season (recency solved this with served_hl coherence; analog has no
   equivalent; HK compounds it). Any fix changes the served cloud → prereg + frozen A/B.
2. **Missing-component confidence inflation:** the σ quadrature drops None
   components, so a swallowed ensemble/representativeness outage SHRINKS effective
   uncertainty and can lift the served tier. Fix = floor/downgrade → prereg.
3. **`_day_state` IEM hour-binning:** ties on integer hours re-order by TEMPERATURE,
   so "last two reads" can substitute warmest-of-hour (reproduced; conservative
   direction — flips declining→holding only) — but it alters the certified 2-consec
   mechanism → prereg. Same class: `peak_close_hour_from_history` fires up to ~59min
   early on IEM cities; live-vs-backtest mid-hour info mismatch in remaining-rise.
4. **PIT tie handling** (`x <= y` counts ties as covered) biases the histogram on
   quantized data and feeds calibration_gate → randomized/mid-PIT is a gate-input
   change → prereg. Same class: rank-histogram tie bias (diagnostic-only).
5. **Round-half-up on negatives rounds toward +inf** (−17.5→−17): matches the code
   comment but UNVERIFIED against WU/contract behavior on winter boundaries.
6. **Register margin gate vacuous when floor_c is None** (pre-dawn + v3 outage):
   re-opens the carryover class in a degraded regime (phantom cap still bounds it).
7. **`implied_probabilities` de-vigs over one-sided/placeholder quotes** — filtering
   to two-sided changes every served market_prob → prereg (quote_quality already
   surfaces the caveat).
8. **Day-ahead °C headline for SF** (D19 stands); **hardcoded 73–74.5% coverage
   prose** in the band flag (owned by the band_cover clock); **hardcoded "~56%
   day-ahead"** in the HIGH-CONVICTION line (London-specific stat served everywhere).
9. **calib_pairs same-day cross-attribute leak** into conditional_spread_eval
   (recommend-only; also preserved D26/D28 probe comparability — fixing the live
   monitor's stream should note the break).
10. **Validation-vs-live blend floor mismatch** (≥5 pairs vs MIN_SAMPLES=10) and
    `Mechanism.n` overstatement / climatology estimator-error-bar mismatch in the
    recommend-only convergence layer.
11. **`passes_integrity` is wired to NOTHING** — wire the filter into the three
    measurement readers BEFORE WP-1 writes its first `*_SUSPECT` flag.
12. **market_snapshots has no high/low kind column** — the registered low-snapshot
    follow-up would overwrite/mis-settle high rows; add `kind` to the PK first.
13. **WU chunk transport failure still caches a holed month** (sources `_wu_*_raw`
    swallow → partial → 7-day cache TTL persists the hole). Needs a
    fetched-empty-vs-transport-failed distinction — design change on the truth
    path, do deliberately, not hot-patched.

## RECORD-ONLY (spent-probe evidence — scripts are artifacts, never edit)
- Spent probes are re-runnable into different verdicts (D26/D28 loaders use
  `date.today()`; D19 reads a growing archive; polymarket v2 universe grows): the
  EXECUTED prereg outcomes are the binding record; script re-runs are void.
- `_p2_probe` cloud_8_13 carried ~1h look-ahead at H=12 — inflates the candidate arm,
  and the candidate STILL failed → D15 kill is conservative/safe. Future P2-style
  registrations use cloud_8_(H−1).
- postpeak probes hardcode `target_date <= 2026-07-12` (the as-of freeze): the
  registered re-score at n≥20 requires a documented cap-advance IO repair in the
  prereg, done once, criteria untouched.
- polymarket v2 `for…else` clobbered rejection notes to "no_event" (flag arithmetic
  unaffected); s2a era-halves diagnostic sorted alphabetically not chronologically
  (frozen criteria order-independent; verdicts safe).

## DEFERRED CLEANUPS (real but not load-bearing; batch when touching those files)
DRY: bucket-containment ×4 (storage/market×2/scorecard) · settlement bucket math ×5
across tools · station coords ×7 · `_pearson` ×3 · type-7 quantile ×3 (+1 deliberate
divergence in peak_close_hour — now documented here) · half-integer edge-distance ×3
· paired-z gate ×3 · truth-resolution ladder ×3 (verify/settle/_anchored_actual,
already drifting — verify still shares one Sources budget) · `_wu_obs_iter`
consolidation ×4 · run.py °C→°F display ×4 · city-containment matcher ×4 in council.
PERF: `_validate` O(n²·members) (fine at 60–365d; matters at 10y) · obs-cache key
embeds sliding dates (daily full refetch; unbounded growth) · `_connect()` replays
DDL per open · backfill_pm_resolutions retries NO-MATCH pairs forever, uncounted.
Suspected-dead tools (archive candidates): analog_drift_diag, bucket_confidence_backtest,
calibration_gate_run, conditional_bucket_backtest, lineage_blend_run, live_nwp_point,
residual_kalman_run, stop_rule_run, quantum_backtest, timescale_sweep,
hko_intraday_accumulate, ensemble_accumulate (NOT scheduled — if its clock is
supposed to run, it isn't). Keep two_band_backfill (D14 evidence).
TEST-GAPS: watchdog_core 0/297 lines · accumulate 0 direct · analog_shrink now
gate-wired · kalshi_logger network duties (2 KATs added; more possible) · duty-loop
`skipTest` skips later STREAMS (use subTest).

## Verification
Full unittest gate green after every batch (672 → 674 tests, 0 failures);
module selftests green (incl. newly wired analog_shrink, p2b); zero-test-file check
PASS (79/79 unittest-based); all walk-forward loops in live modules verified
leak-free by direct read (score-then-append order confirmed in council._validate,
calibration, bucket_verdict, recency_bias, intraday_ceiling, all live probes).
