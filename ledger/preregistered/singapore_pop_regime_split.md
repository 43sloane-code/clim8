# Pre-registration — Singapore PoP-conditioned regime-split band (FROZEN 2026-07-02; clock STARTED)

Distinct hypothesis from the static two-band ([D14], DEAD). The static kill-check failed because
Singapore's day-ahead residuals are **bimodal** — a warm body (the council under-calls) + a deep
cold tail on convective squall days (σ 1.36). That **separability** is the textbook motivation for a
regime split, and it is a hypothesis the static backfill *could not* test (no regime signal on disk).
This file freezes the split BEFORE any PoP outcome is seen; the commit hash is the timestamp.

## The four frozen logging decisions (reviewer spec, honored exactly)
1. **POINT-IN-TIME.** PoP for target day T is snapshotted when the day-ahead cycle runs
   (`tools/singapore_pop_logger.py`, daily in `accumulate`), **never retro-fetched**. `issued_ts`
   + `lead_days` logged. A retro-fetch would be look-ahead contamination — the leak class the
   charter exists to kill.
2. **ONE SOURCE, VERSIONED.** `twc-v3-daypart-precipChance` — TWC v3 daily forecast, the **DAYTIME**
   daypart's `precipChance` for T (the diurnal Tmax is daytime), geocode 1.3502,103.994 (WSSS).
   Logged: `{source, issued_ts, target_date, lead_days, pop, qpf, regime}`. A source/methodology
   change is a documented breakpoint.
3. **THRESHOLD FROZEN NOW (before outcome 1):** `PoP ≥ 40% ⇒ CONVECTIVE`, else `DRY`. A second
   candidate `55%` is *also* declared a trial now — both count toward the eventual multiple-testing
   (DSR-style) deflation. No third threshold may be added after seeing data.
4. **SQUALL PROXY logged too:** TWC daytime `qpf` (quantitative precip forecast) as a second column —
   a heavy-qpf day is the monsoon-surge signal PoP alone may miss. One field now; a fallback split.

## The band rule (frozen — direction CORRECTED vs the reviewer's first guess)
The static backfill **falsified the cool skew**: the body leans **warm** (the council under-calls; on
non-tail days the settled bucket sits at/above the point). So the split is warm-body / cold-tail:
- **DRY (PoP < 40%):** band = **[P, P+1]** (warm two-bucket) — expected honest coverage ~80%.
- **CONVECTIVE (PoP ≥ 40%):** band = **[P−1, P+1]** (three-bucket, wide) — accept the cold squall tail.

## Falsifiable gate (Gate 1′, pre-registered)
At **n ≥ 15 DRY days**: the dry-day **[P, P+1]** two-band must cover **≥ 75%** of settled dry days,
AND the PoP-weighted mean band width must beat the blanket three-bucket band (i.e. buy conviction on
dry days without losing net coverage). **If the dry-day two-band < 60%, the regime-split is DEAD
(→ D15)** — PoP does not separate the tail, and the intraday cousin's failure ([D11]) generalises.
Conviction stays **UNLABELED** until n ≥ 20 dry days (Gate 2). Money gate (Gate 4) unchanged and
expected to fail (efficient boring market); a CALIBRATED-BUT-NOT-TRADEABLE result is the honest end.

## Status
**INSTRUMENTATION LIVE — clock started 2026-07-02.** No served number moves until Gate 1′ clears on
frozen data. This is the earned path the [D14] kill deferred; it is opened here with its OWN gate,
not by relitigating the static two-band (which stays dead).
