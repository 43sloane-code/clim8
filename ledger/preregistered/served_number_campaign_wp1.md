# WP-1 addendum — `market.py fetch_resolution` wrong-event fallback (Class D + retro-audit)

*Served-number remediation campaign, WP-1 (HIGHEST priority — settlement identity is the root of the
contamination chain). This addendum freezes the FIX; the RETRO-AUDIT (re-fetch every historical
`pm_resolved_label`) is HELD by operator directive 2026-07-11 and runs later as its own artifact.*

## The defect (exhibit)
`market.py:596-598`: `fetch_resolution` does
`next((e for e in raw if e.slug == slug), raw[0] if isinstance(raw[0], dict) else None)` — on NO exact
slug match it falls back to **`raw[0]`, a DIFFERENT event**, and builds a Resolution from it. If that
wrong event is `closed` with a YES-priced winner, `backfill_pm_resolutions` writes its label into
`pm_resolved_label` — settling a city/day against another city/day's contract. `fetch_market_by_slug`
(line 559) already implements the correct exact-match-or-None contract; the fix's shape is known.

## Fix design (frozen — Class D: make the code do what the contract says; cert = exhibit-the-delta + KAT)
1. **One shared slug-match helper** `_match_event_by_slug(raw, slug) -> dict | None` (exact match, NO
   fallback), used by BOTH `fetch_resolution` and `fetch_market_by_slug`.
2. **Fail-closed on no match.** `Resolution` gains `no_match: bool = False` and `near_miss_slugs:
   tuple = ()`. When `raw` is non-empty but no slug matches exactly, `fetch_resolution` returns a
   NO_MATCH sentinel `Resolution(resolved=False, no_match=True, near_miss_slugs=<top-3 candidate
   slugs>)` — never a resolution built from the wrong event. Empty/failed `raw` stays `None` (transient
   — retry), DISTINCT from NO_MATCH (a slug/schema-drift alarm). A missing settlement is recoverable; a
   wrong one is poison.
3. **Caller records it.** `backfill_pm_resolutions` on `no_match` writes NO `pm_resolved_label` (already
   guarded by `resolved=False`) and emits a near-miss report line listing the top-3 candidate slugs, so
   a human can repair a genuine slug drift. (Stamping `integrity_flags` on such rows is the retro-audit's
   job; the live path just refuses to mis-settle and surfaces the near-miss.)

## KATs (this WP)
- **KAT-F1a** exact-match happy path → resolves the RIGHT event, `no_match=False`.
- **KAT-F1b** no exact match, a resolved WRONG-city event present → `no_match=True`, `winning_label
  IS None`, `resolved=False`, near-miss lists the wrong slug. (RED on pre-fix code — it returns the
  wrong event's label.)
- **KAT-F1c** legacy-mislabel regression pin: a resolved WRONG-DAY event, requested slug absent →
  new code returns `no_match` and NOT the wrong day's label. (RED on pre-fix code.)
- **empty-raw** stays `None` (transient), not NO_MATCH.
- **backfill NO_MATCH** → `pm_resolved_label` stays NULL + a near-miss line is emitted (fail-closed).
- **KAT-F1d resumability** — DEFERRED with the held retro-audit.

*Repo gate note: the pre-commit hook requires a green suite, so the KATs are confirmed RED on the
pre-fix code (exhibited in the WP work), then committed together with the fix in a green state — the
"failing-KATs-first" intent is honored in the workflow, not by pushing a red commit through the gate.*

## HELD — retro-audit (operator directive; runs later)
Re-fetch every `pm_resolved_label` under exact-match → {CONFIRMED / CORRECTED(`pm_resolved_label_v2` +
`F1_RESOLUTION_CORRECTED`) / ORPHANED(`F1_RESOLUTION_SUSPECT`, excluded)}; quantify per city/month via
verify_skill; re-derive/demote affected FINDINGS entries; resumable checkpointed job (API budget,
state in ledger); KAT-F1d resumability. Downstream measurements (`live_bucket_scorecard`, p̂_corr,
xref calibration) then gate on `storage.passes_integrity`. Until this runs, historical
`pm_resolved_label` values remain UN-audited — the fix stops NEW contamination; it does not clean the
past.
