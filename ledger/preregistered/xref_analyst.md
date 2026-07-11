# Pre-registration — xref-analyst: Historical×Live cross-reference, chart analysis & pattern recognition

*2026-07-11. Operator-authored execution plan frozen as the design (its §§0–8 are the binding
authority). This file records that design + the Phase 0 source-audit / grid-spec / reconciliation
receipts the plan mandates. Composes with `cur_f_corroboration_guard_v2` (consumes its read log) and
`watchdog_core.py` (new duties); replaces nothing. stdlib-only, deterministic, KAT-certified. NO
served-number / settlement / bucket / serving-% changes — this layer produces EVIDENCE + CHARTS only;
any detector output that would change a served number needs its OWN pre-reg.*

## Frozen design (binding — from the operator plan §§0–8)
- **§1.1 load-bearing decision: charts are OUTPUT, never INPUT.** No OCR, no image pipeline, no
  headless browser. The machine ingests SERIES, analyzes series, RENDERS its own annotated SVG. A
  source exposing only a rendered chart (no series endpoint) is UNUSABLE-FOR-MACHINE-INPUT — flagged,
  never pixel-scraped (METHOD-DEFECTIVE by construction). [[project_windy_vision_rejected]] already
  settled this for Windy.
- **Pure-core / impure-shell**, same contract as the corroboration guard: comparator, detectors, and
  chart code are PURE and deterministic; ALL wall-clock + I/O injected as args. `datetime.now()` in a
  pure path is a certification-breaking defect (the determinism KAT catches it).
- **§2 point-in-time store (the anti-look-ahead spine):** per-ICAO SQLite `ledger/history/{ICAO}.sqlite`,
  WAL. `obs_asof` (append-only, never UPDATEd — what was known WHEN, keyed incl. `fetched_utc`);
  `obs_final` / `daily_final` (settlement-grade, written only after the MEASURED revision window).
  Live-day reads `obs_asof` filtered `fetched_utc ≤ now_utc_injected`; analogs/climatology read
  `obs_final`/`daily_final` for days STRICTLY before the live day. KAT-L outranks every other KAT.
- **§4 comparator core — three mandatory axes:** A) today vs climatological SHAPE envelope
  (per-hour P10–P90 of anomaly-from-day-mean, `statistics.quantiles`); B) today vs k analogs
  (recency-weighted anomaly-MAE distance → empirical eventual-max distribution, a CROSS-CHECK never a
  forecast, with MANDATORY persistence + climatology baselines in every report); C) source vs source
  (pairwise Δ, persistence, lag/repeater signature). §4.4 regime stratification
  `(wind_octant, precip_flag)`, degrade to UNSTRATIFIED loudly when THIN (<40 candidates).
- **§5 detector registry — two families, two-tier authority:** Family F (feed pathologies:
  F-FLATLINE, F-STALE-ECHO, F-SPIKE-REVERT, F-UNIT-SLIP, F-CLOCK-SKEW, F-GAP, F-DIVERGENCE) ground-
  truthed on frozen incidents + synthetics; Family M (meteorology: M-SEABREEZE-KINK, M-PRECIP-COLLAPSE,
  M-RAPID-RAMP, M-ENVELOPE-BREAK, M-ANALOG-DIVERGENCE, M-MULTIDAY-EXTREME-SETUP) ground-truthed on
  next-hours `obs_final`. **Everything launches ADVISORY** (gates nothing). GATING graduation requires
  ≥20 TP firings, FPR ≤ 1/30 station-days Wilson-bounded, a verify_skill artifact, and a per-detector
  addendum pre-reg. Family M expected to stay ADVISORY (atmosphere ≠ deterministic). Auto-demote on
  2× FPR breach. Thresholds live in `config/detectors.json`, never inline.
- **§5.4 nuance ledger (must be in code, not just comments):** °C-native METAR at 0.5–1.0°C res
  (1°F≈0.56°C flicker can be pure conversion granularity → detector tolerances specified in
  °C-equivalent); settlement rounds half-up on the displayed °F integer → `boundary_proximity_f` field,
  sub-0.5°F-at-boundary evidence is NON-evidence; SPECIs inform detectors but never enter the grid;
  analogs per-ICAO only; DST/local-day keying reuses the guard's `zoneinfo`.
- **§6 chart layer:** deterministic SVG (`overlay.svg`, `analogs.svg`, `sources.svg`) to
  `ledger/charts/{ICAO}/{local_day}/`, byte-hash-stable given inputs (KAT-C golden hashes), provenance
  footer (commit hash, assembled_at, row-counts). NO code path reads a chart.
- **§7 integration:** guard shim (post-graduation F-GATING hits → `suspect_sources`; ADVISORY →
  `FloorDecision.evidence` context); two watchdog duties + canaries (`xref_store_health`,
  `xref_analysis_health`); `xref_report_ref` ledger field on each served pick.
- **§8 KATs (write FIRST, all failing):** KAT-L (look-ahead, highest rank), KAT-F1..7, KAT-INC1
  (London — F-FLATLINE/STALE-ECHO name the 34-obs hold), KAT-INC2 (Jeddah — NO F hit, M-RAPID-RAMP
  fires; the INC1/INC2 pair certifies pathology-vs-meteorology discrimination), KAT-M1..6, KAT-A
  (leave-one-out analog exclusion + loose containment), KAT-C (chart determinism), KAT-D (global
  determinism), KAT-T (DST), KAT-B (boundary granularity).
- **Non-goals (frozen):** no chart pixel-parsing; no ARIMA/SARIMA/GARCH in v1; no trading-signal
  generation; no settlement/bucket/serving-% changes; no numpy/pandas/matplotlib.

## Phase 0 recon — RECEIPTS (fetched/measured 2026-07-11, not assumed)

### Source registry — VERIFIED endpoint capabilities (corrects the plan's assumed source list)
The plan's "Open-Meteo vs Visual Crossing vs Weatherbit vs settlement-source" is WRONG for intraday
**observations**. Actual contracted sources in `weather_council/sources.py`:

*Wunderground / The Weather Company / TWC are ONE corporate entity, all on `api.weather.com` — the WU
observation oracle, the WU v1 history, and the TWC daily FORECAST are three products of the same
company. That shared lineage is a first-class caveat (below), not a footnote.*

| Source | Endpoint | Kind | Intraday OBS? | xref role |
|---|---|---|---|---|
| **WU v3 current** (Wunderground/TWC) | api.weather.com/v3/wx/observations/current | live obs (~10min) | **YES** | Axis-C obs feed #1 (the SETTLEMENT source); its cur_f-vs-record gap IS the London over-read signature |
| **IEM METAR** | mesonet.agron.iastate.edu ASOS | hourly obs + archive (~10y) | **YES** | Axis-C obs feed #2 + the deep historical spine (obs_final/daily_final backfill) |
| WU v1 history (Wunderground/TWC) | api.weather.com/v1/.../historical | recorded daily/hourly (lags 1–2h) | partial | settlement-record cross-ref |
| **TWC daily forecast** (The Weather Company) | api.weather.com/v3/wx/forecast/daily/5day | **FORECAST** | NO | **FORECAST cross-reference** — already measured as a signed offset in Plan 4 (`weather_council/twc_offset.py`, recommend-only, 3-gate certified). xref surfaces it as a forecast-vs-analog/served cross-check (a distinct axis from the obs Axis C) |
| Open-Meteo | api.open-meteo.com (forecast/archive/ensemble) | FORECAST + reanalysis archive | NO (forecast) | NOT an obs feed; archive can backfill history |
| Meteostat | bulk.meteostat.net daily | daily bulk (lags ~months) | NO | daily_final backfill only |
| Weatherbit | api.weatherbit.io/v2.0/forecast/daily | **FORECAST only** | NO | not an obs source (plan mis-cast it) |
| ~~Visual Crossing~~ | — | **DOES NOT EXIST in repo** | — | remove from the design |
| HKO | data.weather.gov.hk | obs (HK) | (HK removed from basket) | n/a |

**HONEST CONSTRAINTS (state loudly):**
1. For intraday OBSERVATIONS there are effectively **2 feeds — WU-v3 and IEM-METAR** — partly sharing
   underlying METAR. So Axis C (source-vs-source, obs) is WEAKER than the plan implies. Its
   highest-value real check is exactly **WU-v3-current vs IEM-recorded** divergence (London 07-11:
   v3 cur 83°F vs IEM 81°F — the over-read), which is the one that matters.
2. **FORECAST cross-reference axis (new — Wunderground/TWC):** the TWC daily forecast is a *forecast*,
   not an obs, so it does NOT enter the obs grid or Axis C. It enters as its own cross-check — TWC's
   offset-adjusted call vs the analog projection (Axis B) and vs the served pick — reusing the Plan-4
   `twc_offset` machinery. **SAME-COMPANY CIRCULARITY CAVEAT:** TWC and the WU observation oracle are
   the SAME company (WU's displayed forecasts are TWC-powered), so TWC is NOT an independent check on
   the WU record — it corroborates the WU forecast family, not an outside opinion. Recommend-only;
   any promotion beyond display routes through the Plan-5 independence audit (`twc_independence.py`)
   and the Plan-3 gate, never this layer. (IEM and Open-Meteo/Meteostat ARE outside The Weather
   Company — they are the genuinely independent cross-references.)
3. NO contracted source is chart-only → §1.1's "unusable" branch is trivially empty (Windy rejected).

### Historical depth — EXCEEDS target
~10 years of hourly IEM METAR per active city (all far above the plan's ≥3yr / ≥5yr target):
EGLC 3649d (2016-07→2026-07) · WSSS 3657d · OEJN 3643d · OPKC 3647d · KSFO 3649d. Backfill primary =
the IEM archive (`data/{icao}_hourly_iem.jsonl`), not Open-Meteo. Per-city coverage (missing-hour
rate, longest gap) still to be reported via `verify_skill.py` in Phase 1 before any analog claim.

### Per-station METAR grid cadence — DETECTED (heterogeneous; the plan's ":50/:00" is wrong)
| ICAO | obs/day | minute-of-hour | grid note |
|---|---|---|---|
| EGLC | 48 | :20, :50 | half-hourly |
| WSSS | 48 | :00, :30 | half-hourly |
| OEJN | 24 | :00 | **hourly only — fewest intraday grid points** |
| OPKC | ~30 | :00, :25, :55 | irregular ~half-hourly |
| KSFO | ~26 | :11, :14, :56 | SPECI-mixed — routine + specials interleaved |
Grid spec MUST be per-station (config-pinned), ±10min nearest-neighbor, no interp across gaps >90min.
OEJN's hourly cadence means its Axis-A/B traces are coarser; note it.

### Repo reconciliations (plan assumptions vs reality)
- **config:** peak windows already live in `config/guard_cities.json` (`peak_window_local`, from the
  guard Phase 0). REUSE it — do NOT create `config/peak_windows.json`. Sunrise-proxy / peak-onset
  extend that file.
- **Gate taxonomy:** FINDINGS.md uses `MEASURED-PENDING` + `CERTIFIED` + dead-ledger `DEAD`; it does
  NOT use `SUPPORTED` / `METHOD-DEFECTIVE` as vocabulary. Map: plan-`SUPPORTED` → repo-`CERTIFIED`;
  plan-`METHOD-DEFECTIVE` → a dead_candidates.jsonl entry. Keep `MEASURED-PENDING`.
- **verify_skill.py** ✓ exists (repo root) — required-artifact assumption holds.
- **DEPENDENCY / SEQUENCING (flag):** this plan "consumes the corroboration guard's read log", but
  that log (the guard's ObsLog of v3 reads) does NOT exist yet — the guard is itself only at Phase 0
  (`cur_f_corroboration_guard_v2.md`), ObsLog is its Phase 1. So xref Phases 3+ (frame assembly
  sharing the guard's read cycle; F-STALE-ECHO reusing predicate S4) are BLOCKED on the guard's
  Phase 1 landing first. **Recommend: build the guard through Phase 1 (ObsLog) before xref Phase 1**,
  so the two share one read/store path instead of forking it.
- **Layout:** no `guard/` or `xref/` package yet; adopt `weather_council/xref/` + `tests/` (flat,
  unittest — repo uses `unittest`, NOT pytest; verify via `PYTHONPATH=. python3 -m unittest`).

### Scope note (honest)
This is a LARGE 7-phase build (per-station SQLite stores, ingest/backfill/finalization jobs, a pure
comparator + detector registry, a chart layer, watchdog duties) landing ALONGSIDE the corroboration
guard which is also only at Phase 0. Two big builds at Phase 0. Both are recommend-only/evidence-only
by design (change no served number), but the combined surface is substantial — worth confirming the
sequencing (guard-first) and appetite before Phase 1 code.

## STOP — Phase 0 exit (pre-reg + source registry + grid spec)
Pre-reg written; source registry with fetch receipts (Visual Crossing removed, Weatherbit re-cast,
2-obs-feed constraint stated); per-station grid cadence detected; repo deltas + the guard-log
sequencing dependency flagged. **No code in the analysis path.** Awaiting operator sign-off on
(a) the corrected 2-feed Axis-C scope, (b) guard-first sequencing, (c) build appetite, before
Phase 1 (historical store + backfill + revision-window measurement).
