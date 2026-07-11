# Code audit — weather-verdict (2026-07-11)

Six-slice parallel review of the ~25.9k-LOC tree. Each finding carries a **served-number risk**:
- **SAFE** — refactor / labeling / internal; ships without the gate (HARD RULE 2). → fixed in this pass.
- **GATE** — changes a served forecast/probability/bucket/pick/settled value. HARD RULE 1: pre-reg →
  leak-free walk-forward → sign-stable both halves → KAT. **Flagged here, NOT hot-patched.**
- **NOTE** — verify-intent / non-defect / very-low-value. Documented, left as-is unless trivial.

Disposition column: **FIXED** (this pass) · **GATE** (flagged, needs pre-reg) · **NOTE** (documented).

---

## A. Connection leaks on exception paths
Every one: `conn = _connect()` opened, `conn.close()` only on the success line; a raise between them
skips the close. NOTE: in CPython, `conn`'s refcount hits 0 when the function unwinds and
`Connection.__del__` closes it — so most are hygiene, not true leaks. The one where the close is
GENUINELY skippable mid-function (whole body in a try) is fixed; the rest wrapped opportunistically.

| File:line | Fn | Sev | Disp |
|---|---|---|---|
| storage.py:515-528 | `book_snapshot_coverage` (whole body in try → close skipped exactly on failure) | HIGH | **FIXED** (contextlib.closing) |
| tools/accumulate.py:139-143 | `_snapshot_db` (two conns if `backup()` raises) | LOW | **FIXED** (contextlib.closing) |
| storage.py:325-398 | `verify` (unguarded `fetch_archive_series`/`_coord_place`) | MED | DEFERRED-LOW (refcount-mitigated; wrap later) |
| storage.py:603-695 | `settle_market_snapshots` | MED | DEFERRED-LOW |
| storage.py:712-741 | `backfill_pm_resolutions` | MED | DEFERRED-LOW |
| tools/twc_forecast_logger.py:119-128 | council-pairing lookup | LOW | DEFERRED-LOW |
| tools/eval_harness.py:74-84 / 118-124 | two TWC `sqlite3.connect` blocks | LOW | DEFERRED-LOW |
| run.py:816-823 / 918-931 | `_twc_cross_reference` + `_cross_check_lines` TWC lookups | LOW | DEFERRED-LOW (dedup deferred too) |

## B. Cross-module duplication (SAFE — FIXED)
| Finding | Sev | Disp |
|---|---|---|
| `_connect_at` (mutate `storage.DB_PATH` → `_connect` → restore in finally) copied **×4**: postmortem.py:174, lessons.py:199, twc_offset.py:196, twc_independence.py:58 | MED | **FIXED** — hoisted to `storage._connect_at`; four bodies delegate to it |
| Identical TWC `SELECT fc_high ...` lookup duplicated in run.py `_twc_cross_reference` + `_cross_check_lines` | MED | DEFERRED — extract `_twc_raw_high(place,target)` next pass |
| Two-sided binomial sign-test duplicated: twc_offset.py:83 `_binom_two_sided_p` == lessons.py:57 `_binom_p` (both correct, identical) | MED | NOTE — leaf-dependency rationale is documented; left, not silently diverging |
| Open-tail containment logic ×3: storage.py:540 `_bucket_for_reading`, market.py:140/487 `.contains` | LOW | NOTE — behaviourally identical; low value, left |
| `k.startswith("temperature_2m_max/min")` column-filter ×4 in sources.py | LOW | NOTE |
| `ts.get("station")...` anchor-cols idiom ×3 in storage.py (log_verdict/market/tracked) | LOW | NOTE |

## C. Stale docstrings / comments (SAFE — FIXED)
| File:line | Problem | Disp |
|---|---|---|
| tools/accumulate.py:2,13-22 | header says settles "Hong Kong + London / HKO"; actual CITIES = Manila, Singapore, London | FIXED |
| tools/daily_healthcheck.py:13-14, 79-84 | docstring/comment say basket = "London + Hong Kong"; actual BASKET = Manila, Singapore | FIXED |
| weather_council/intraday_ceiling.py:181-184 | comment says "only London / two settlement cities"; dict now holds 6 cities | FIXED |

## D. Genuine safe bugs (SAFE — FIXED)
| File:line | Problem | Sev | Disp |
|---|---|---|---|
| tools/twc_independence.py:119 | member sort uses `-1` None-sentinel inside `-(abs(r))`, so `r=None` sorts to the TOP (opposite of intended "unmeasurable last") | LOW | FIXED |
| tools/lessons.py:181 | `K_candidates_ever = len(queue)+len(emitted)+len(deferred)+1` double-counts (appended cands are already in `queue`) | MED | FIXED → `len(queue)+1` |
| weather_council/postmortem.py:84,92 | `settlement_divergence` emitted twice (in `comps` and top-level) → persisted in two places | LOW | FIXED (drop from `comps`) |
| tools/accumulate.py:248-251 | `resolve_truth_sources.py` return code ignored → empty stdout writes `"[]"` to truth_config → watchdog Duty 3 false-GREEN on the drift sentinel | MED | FIXED (guard rc, preserve prior on failure) |
| tools/accumulate.py:104-123,256 | `_tail_status` fed stdout only, but subprocess errors go to stderr → the "make failures visible" mechanism is defeated | MED | FIXED (merge stderr) |
| tools/eval_harness.py:52,104 | `load_rows()` called twice per pass (re-reads the lock ledger) | LOW | **FIXED** (reuse) |
| tools/watchdog_core.py:64 | `_wilson` computes a `center` midpoint that is never returned/used | LOW | DEFERRED |

## E. No-op / dead code (SAFE — FIXED where confirmed unused)
| File:line | Problem | Disp |
|---|---|---|
| weather_council/sources.py:186 | `floor_c = runmax_c if runmax_c is not None else None` — both branches equal `runmax_c` | **FIXED** |
| run.py:202 | `[d for d in recent[-4:]]` no-op comprehension | **FIXED** → `list(...)` |
| weather_council/sources.py:981-996 | `london_eglc_truth_series` flagged as dead | **NOT DEAD** — called by tools/timescale_sweep.py + tools/quantum_backtest.py. NOTE, kept. |
| weather_council/sources.py:959-965 | `is_london_eglc` flagged as unused | **NOT DEAD** — called by council.py:928 + tested in test_council.py. NOTE, kept. |
| tools/lessons.py:269 | `_selftest` builds `det_budget3` and never uses it | DEFERRED (test-only scaffolding) |

## F. SERVED-NUMBER — GATE REQUIRED (flagged, NOT patched — needs pre-registration)
These change a served/settled value; hot-patching them violates HARD RULE 1. Each needs its own
pre-reg + KAT before any code change.

| File:line | Problem | Why it's served |
|---|---|---|
| **market.py:596-598** | `fetch_resolution` falls back to `raw[0]` without an exact-slug match — can settle `pm_resolved_label` against the WRONG event (different city/day), unlike `fetch_market_by_slug` which requires an exact match. **HIGHEST-priority data-integrity risk.** | writes the authoritative settlement bucket scorecards trust |
| sources.py:1044-1068 | `wunderground_daily_max` reads `startDate` only and `max()`es over all returned obs WITHOUT regrouping onto the station's LOCAL calendar day — for off-UTC stations rows can straddle two local days, and this value is the **phantom-cap ceiling** feeding `_fuse_live_floor` | changes the fused intraday floor / served bucket |
| sources.py:206-210 | phantom cap on `max24` fires only when `wu_record_max_f` is a number; on a daily-max endpoint outage the register is uncapped (the documented THREE-sided bound silently drops to two) | changes the fused floor on an outage |
| intraday_ceiling.py:103-117,295 | `_day_state`'s 2-consecutive-reads "declining" rule was certified on whole-hour METAR but runs on ~30-min WU fractional obs → can flip state on a single half-hourly tick | selects the served `state_late_risk` cell + NOT-FINAL labeling |
| market.py:410-415 | unparseable `(None,None)` bucket survives `_parse_bucket` and sorts to ladder index 0 (`-inf`), injecting a never-matching bucket into the de-vig denominator | affects `implied_probabilities` |
| bucket_contract.py:118-125 | `compact_buckets` all-tail degenerate case can double-label adjacent integer cells | emitted served pmf cells |
| council.py:963 | `_resolve_truth` keeps `window+1` days (comment says `window`); **consistent across all truth paths** so not a live bug, but the comment is wrong and a non-positive `window` would keep the whole series | the backtest window learns served bias/skill — semantics change is GATE; the comment fix is SAFE |

## G. Verify-intent / low-value (NOTE — documented, left as-is)
- council.py:260 `_doy_gap` uses `% 365` — 1-day leap-year error at the seasonal extreme; cannot flip the `> 31`-day downgrade boundary, never moves the number. LOW.
- council.py:1464 `outliers_set_aside` counts per-attribute outlier notes (can double-count a member flagged on both high+low). Display-only. LOW.
- council.py:1954 `mean`/`rmse` as lambdas (E731). Style. LOW.
- shadow_score.py:131 σ floored twice; stored `day["sigma"]` can differ from the σ actually scored (both ≥ FLOOR). Provenance-consistency nit. LOW — DEFERRED.
- shadow_score.py:283 DEFERRED-BUDGET candidates never promoted when a slot frees → can EXPIRE un-scored. **Design decision**, not a clear bug — flag for the Phase-6 loop-driver design; left.
- twc_offset.py:45-129 `mean_offset`/`n_ties` computed but never read by any consumer. LOW — kept as persisted diagnostic.
- twc_offset.py:153 `_grain_for` SF→"F" branch unreachable until KSFO joins the tracked basket. Forward-looking; kept.
- bucket_contract.py:248 `_BANNED` referenced only in `_self_test`. LOW.
- compare.py:299-333 `market_modal` re-scan + `bucket_for_high` called twice — redundant recomputation. LOW — DEFERRED.
- provenance.py:33 `pipeline_version` spawns a `git rev-parse` per verdict. Best-effort by design. NOTE.

---

## Summary
- **FIXED & SHIPPED this pass (all SAFE, 587 tests green):**
  1. `_connect_at` ×4 → one shared `storage._connect_at` (postmortem/lessons/twc_offset/twc_independence delegate).
  2. `book_snapshot_coverage` + `_snapshot_db` connection leaks → `contextlib.closing`.
  3. `twc_independence` member sort — None-`r` sentinel inverted (sorted to top) → sorts last.
  4. `lessons` `K_candidates_ever` double-count → `len(queue)+1`.
  5. `postmortem` `settlement_divergence` de-duplicated (was in `comps` AND top-level).
  6. `accumulate` truth-config false-GREEN — never clobber a good config with `"[]"` on resolve
     failure; log emit-crossover rc; merge stderr into `_tail_status` (failures were stderr-hidden).
  7. `eval_harness` double `load_rows()` → reuse.
  8. No-ops: `sources.py:186`, `run.py:202`. Stale docstrings ×3 (accumulate, daily_healthcheck,
     intraday_ceiling) now name the real basket (Manila/Singapore/London, not HK).
- **DEFERRED (SAFE, low value):** the 6 refcount-mitigated conn-leak wraps, the run.py TWC-lookup
  dedup, `_wilson` dead `center`, compare.py recomputation, shadow σ double-floor, `det_budget3`.
- **FLAGGED for the gate (§F, NOT patched):** 7 served-number issues — **`market.py fetch_resolution`
  can settle against the WRONG event is the highest-priority data-integrity risk**; each needs a
  pre-reg + KAT.
- **Verified correct (no change):** two "dead" functions actually have callers (kept); statistical
  conventions match `edge.py` (bootstrap indexing, `_logloss`/`_brier`, seed/samples); the postmortem
  telescoping identity and the Pearson zero-variance guard are correct.
