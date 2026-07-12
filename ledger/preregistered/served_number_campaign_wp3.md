# WP-3 addendum — phantom cap silently drops to two-sided on outage (Class R)

*Served-number campaign, WP-3 (F3). Class R (changes fused-floor behavior in a failure regime). The
FIX + KATs are offline; the full CERTIFICATION is the Gate-0-B **Branch C** synthetic-episode replay,
which is inherently the HELD half (no genuine outage episodes exist — see Gate 0-B). Touches
`_fuse_live_floor` — §2.3 one-owner: the guard is at Phase 0 (not integrated), so WP-3 lands first.*

## The defect (exhibit)
The phantom cap `max24_f = min(max24_f, ceiling)` fires only when `wu_record_max_f` is a number. On a
daily-max endpoint OUTAGE (`wu_record_max_f is None`) the cap is SKIPPED entirely — the register is
bounded only by the attribution-margin gate (~3°F of `floor_c`), so the 07-09-class phantom (register
102°F within margin of a 100°F floor → a served 39 vs settled 38) re-opens in the failure regime,
SILENTLY. Silence is the defect; the cap logic is secondary.

## Fix design (frozen)
1. **Declared degraded cap, never silent.** `_fuse_live_floor` gains `cap_fallback_f`. The cap reference
   is the daily-max endpoint when present, ELSE the caller-supplied recent daily max (`cap_fallback_f`,
   e.g. yesterday's peak — a widened stale cap). `cur_f` may only RAISE the ceiling (it never caps the
   register, which legitimately leads `cur_f` — the 07-04 lesson).
2. **`ABSENT_OUTAGE` when there is no cap at all.** With neither endpoint nor fallback, the register
   stays uncapped (we have no reference to cap it), BUT the note carries `[ABSENT_OUTAGE: ...]` when an
   uncapped register raised the floor — a watchdog-visible alarm, so a possible outage-phantom is
   DECLARED, not silent. Caller: `intraday_ceiling` passes yesterday's peak (°F) as the fallback.
3. **Watchdog AMBER** on `ABSENT_OUTAGE` deferred to the watchdog-owning work (keeps `watchdog_core`
   single-owner, §2.3); the note-marker makes the state available now.

## KATs (this WP)
- outage + fallback: `wu_record=None, cap_fallback=100`, register 102 → capped to 100 → **38** mid-outage
  (not 39). No `ABSENT_OUTAGE` (it was capped).
- outage + no fallback: register fuses uncapped but the note DECLARES `ABSENT_OUTAGE`.
- healthy parity: endpoint present → byte-identical phantom-cap behavior, no marker.
All existing `test_live_floor` KATs stay green (the `wu_record=None` register cases now carry the
declared marker; none assert its absence).

## HELD — Branch-C synthetic-episode replay (the Class-R certification)
Per Gate 0-B (Branch C: 0 genuine outage episodes recoverable), certify on 20 seeded synthetic episodes
(duration grid {1,3,6,12,24 h} × stratified start times, seed `GATE0B_SEED=20260711`), masking at
replay time on WP-2-corrected daily maxes; metric: fused-floor bucket error vs settled truth during
outages, old vs new, sign-stable both halves; SYNTHETIC-labeled; MEASURED-PENDING until a live outage
is reconciled. Held with the other WP measurement halves.
