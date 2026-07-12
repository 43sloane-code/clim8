# WP-4 addendum — `_day_state` cadence — STOP-AND-REPORT → FREEZE-AND-DOCUMENT

*Served-number campaign, WP-4 (F4). The frozen campaign classed this Class R (convert the 2-read
"declining" rule to a time-based T=120min rule). On implementation, BOTH premises the finding rests on
are falsified by the code and the data. Per the failure-mode checklist ("frozen designs amendable only
by stop-and-report") this is that report: WP-4 is RECLASSIFIED to FREEZE-AND-DOCUMENT (like F7). No
`_day_state` change ships.*

## Why the premises don't hold
1. **"A single half-hourly tick can flip served state" — FALSE.** `_day_state`
   (`intraday_ceiling.py:116-117`) is `below = [c < rm-delta for _,c in prior[-2:]]; return "declining"
   if len(below)==2 and all(below) else "holding"`. It already requires **TWO** reads both below —
   a single tick-down yields `holding`. This is exactly the certified 2-consecutive rule
   (`persistent_decline_lock.md`, 2026-07-06) and CLAUDE.md's documented lock semantics ("One tick-down
   = still HOLDING = the trap").
2. **"Certified on whole-hour METAR, runs on ~30-min WU obs" — FALSE for the certified cities.** The
   lock was certified on EGLC + WSSS, whose IEM archives are **:20/:50 and :00/:30 — ~30-min cadence**,
   the SAME cadence as the live WU feed. There is no cert-vs-live "halving": both are ~30-min, so the
   2-read rule spans the same ~30 min in cert and in production.

## Why a time-based fix would HARM, not help
Converting to a fixed T=120min would change the served `_day_state` on the certified ~30-min cadence
(it would demand ~4 reads / 2 hours instead of the certified 2 reads / ~30 min), breaking the WP-4
cert's own requirement that "parity-on-cert-cadence == identity." That is a served-pick change to a
CERTIFIED mechanism (HARD RULE 6 frozen artifact) for ZERO demonstrated benefit at full HARD-RULE-1
cost — the WP-7 pattern exactly.

## FREEZE actions (SAFE only)
1. This addendum documents the certified 2-read semantics as canonical and WHY they are frozen.
2. FINDINGS/CLAUDE note: the 2-read `_day_state` is certified on ~30-min data ≈ the live cadence; a
   time-based rewrite is GATE with no identified benefit.
3. **Reopening condition (written now):** IF a genuinely FINER feed than the cert cadence is ever
   wired into `_day_state` (e.g. the v3 current ~10-min stream, or a SPECI-dense NON-certified station
   like KSFO), so that "2 reads" could span < ~25 min, THEN a MINIMUM-SPAN guard (require the 2 reads
   to span ≥ a floor that is a NO-OP on the certified 30-min cadence) reopens as a Class-R WP with its
   own parity-on-cert replay. Not now — nothing finer than ~30-min currently feeds `_day_state`.

## Disposition
WP-4 = FREEZE (no code change). The campaign's remaining §F fix-halves are: WP-1/WP-2/WP-5/WP-6
(Class D) + WP-3 (Class R outage) SHIPPED; WP-4 + WP-7 FREEZE-AND-DOCUMENT. Held measurement-halves
(retro-audits / Branch-C replay / historical exhibits) unchanged.
