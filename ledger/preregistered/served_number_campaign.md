# Pre-registration — Served-Number Remediation Campaign (CODE_AUDIT.md §F)

*2026-07-11. Umbrella pre-reg (§§0–3 frozen); per-finding addenda land as `served_number_campaign_wp{N}.md`.
Remediates the seven §F findings — served/settled-value changes correctly NOT hot-patched in the audit
pass. §A–E FIXED items are shipped and out of scope; §A/§D/§G DEFERRED are Phase-7 backlog. stdlib-only,
deterministic, KAT-certified. Commit discipline: pre-reg alone & first per WP; no fix before its KATs
exist and fail; frozen §3 designs amendable only by stop-and-report.*

## §0 Mission (frozen)
Remediate the seven §F findings under HARD RULE 1, ordered by *what poisons what* — with retrospective
contamination audits where a defect has been writing wrong values into the ledger, not just prospective
fixes. **Non-goals:** no hot patches to any §F item (the audit already ruled); no behavior change for F7
(FREEZE-AND-DOCUMENT); no new features; no reordering the two standing plans' internals — but this
campaign DOES impose sequencing constraints on them (§2) because two §F findings sit upstream of their
inputs.

## §1.1 Certification-class taxonomy (refines HARD RULE 1 — applying it literally to all seven is a category error)
- **Class D (DETERMINISTIC-CORRECTNESS):** the fix makes the code do what the contract already says
  (settle the RIGHT event; exclude an unparseable bucket from a denominator; not double-label a pmf
  cell). "Sign-stable improvement in both halves" is meaningless — you don't keep settling wrong events
  because fixing it dented a scorecard. Cert = pre-reg → **exhibit-the-delta replay** (enumerate every
  historical record whose value changes, justify each vs ground truth) → KAT. The gate is *accountability
  of the delta*, not the direction of a skill metric.
- **Class R (REPLAY-GATED / SKILL-BEARING):** the fix changes a statistical behavior (fused-floor
  construction, state-machine cadence). Full HARD RULE 1: pre-reg → leak-free walk-forward on held-out
  history → sign-stable both halves → KAT. If the corrected behavior *worsens* the metric, that's a
  finding to explain in FINDINGS.md (the metric may have been eating the bug), NOT an auto-rollback — but
  it blocks shipping until explained.
- **Class FREEZE:** documented canonical behavior; changing it is GATE cost for no benefit → freeze + fix
  the comment + guard the latent hazard.

| Finding | Class | Why |
|---|---|---|
| F1 `fetch_resolution` wrong-event fallback | **D** + retro-audit | settlement identity is a correctness contract |
| F2 `wunderground_daily_max` local-day regroup | **D** + retro-audit + R-style impact replay | LOCAL day is contract-defined; but it moves the fused floor, so impact must be quantified |
| F3 phantom cap silently two-sided on outage | **R** | changes fused-floor behavior in a failure regime |
| F4 `_day_state` 2-reads rule on 30-min cadence | **R** | recert of a state rule on a cadence it was never certified for |
| F5 `(None,None)` bucket → ladder index 0 | **D** | de-vig denominator integrity |
| F6 `compact_buckets` all-tail double-label | **D** | pmf cell labeling integrity |
| F7 `_resolve_truth` window+1 | **FREEZE** | consistent across all truth paths; system's certified behavior IS window+1 |

## §1.2 Contamination model (priority is DEPENDENCY-sorted, not severity-sorted)
```
F1 (settlement identity) ─poisons─► scorecards, p̂_corr dataset, market-calibration ledger
F2 (truth daily max)     ─poisons─► fused-floor history, guard recorded_max_f, xref daily_final
F3 (cap on outage)       ─poisons─► fused floor on outage days only (bounded)
F4 (state cadence)       ─poisons─► state_late_risk labels on ~30-min-cadence days
F5/F6 (pmf integrity)    ─poisons─► implied_probabilities snapshots (bounded, market-side)
```
**Campaign order: F1 → F2 → F3 → F4 → F5 → F6 → F7(freeze).** Truth-side before floor-side before
label-side before market-side.

## §1.3 Contamination is a first-class ledger fact
`integrity_flags` (JSON list) on rows bearing served/settled values (primary target: `market_snapshots`,
which holds `pm_resolved_label`; also `verdicts`). Rules: (a) originals NEVER overwritten — corrected
values land in NEW fields (`pm_resolved_label_v2`, `wu_daily_max_local_v2`), append-only like `obs_asof`;
(b) every downstream measurement job (scorecards, p̂_corr, xref calibration) MUST filter on flags — a job
that silently ingests SUSPECT rows is METHOD-DEFECTIVE; (c) FINDINGS.md entries whose evidence overlapped
contaminated rows are re-derived or demoted to MEASURED-PENDING. Nothing stays CERTIFIED on poisoned
evidence by inertia. **Phase-0 ship:** the field + a `_passes_integrity(row)` filter helper that DEFAULTS
include-all until flags exist (provably inert at ship time; KAT'd).

## §2 Cross-plan sequencing constraints (binding on the two standing plans)
1. **WP-2 (F2) before `cur_f_corroboration_guard` Phase-3 sign-off** — the guard's predicates consume
   `recorded_max_f`; all three active cities are off-UTC for settlement (Manila/Singapore UTC+8, London
   BST), so certifying against a straddle-prone daily max certifies a defective input. Guard KAT fixtures
   built from F2-corrected values; re-cut + document if already frozen.
2. **WP-1 (F1) retro-audit before** (a) the guard's Phase-6 `p̂_corr` measurement opens, and (b)
   `xref-analyst` Phase-3 calibration/skill replay — both consume settlement outcomes. Their clocks start
   after WP-1's audit artifact lands.
3. **WP-3 (F3)** touches `_fuse_live_floor` — same neighborhood the guard integrates. Exactly one owner of
   that diff at a time: WP-3 certifies before the guard's Phase-4 call-site integration, or rebases onto it
   and re-runs the guard KAT suite green as part of WP-3 cert.

## §3 Work packages (frozen designs; concise — full text in the source plan)
- **WP-1 `fetch_resolution` (D + retro-audit, HIGHEST):** require exact-slug match (share one helper with
  `fetch_market_by_slug`); no match → `NO_MATCH` sentinel, caller sets `settlement_status=UNRESOLVED_NO_MATCH`,
  row excluded from measurement (fail-closed: a missing settlement is recoverable, a wrong one is poison);
  near-miss logs top-3 candidate slugs. Retro-audit: re-fetch every `pm_resolved_label` under exact match →
  {CONFIRMED / CORRECTED(`_v2`) / ORPHANED(SUSPECT, excluded)}; quantify contamination per city/month via
  verify_skill; re-derive/demote affected FINDINGS entries; resumable checkpointed job (API budget, state in
  ledger). KATs: F1a happy, F1b near-miss→NO_MATCH+top3, F1c legacy-mislabel regression pin, F1d resumability.
- **WP-2 `wunderground_daily_max` (D + retro-audit + impact replay):** regroup obs onto station-local civil
  day via `zoneinfo`; obs belongs to the local day of its `valid_local`; widen fetch to ±1 UTC day then
  filter (never trust endpoint day semantics). Retro-audit: recompute over history, diff vs served, each
  diff day an exhibit; replay the fused floor over diff days → **count of served buckets that would differ**
  is the artifact headline. `daily_final` built from corrected values only. KATs: F2a UTC+8 00:30-local
  straddle, F2b London BST transition, F2c DST fall-back fold, F2d UTC-station parity (byte-identical no-op).
- **WP-3 phantom cap on outage (R):** don't silently uncap — declared degraded mode: substitute the most
  recent F2-corrected daily max ≤ N h stale (N from WP-2's measured latency, not invented) as a widened cap;
  else `cap_status=ABSENT_OUTAGE` + watchdog AMBER. Cert on the **Gate 0-B** declared branch (below). KATs:
  outage-day (degraded cap engages), stale-beyond-N (ABSENT_OUTAGE+AMBER), healthy-day parity.
- **WP-4 `_day_state` cadence (R):** convert read-count → **time-based**: declining ⇔ net decline over ≥ T
  min with ≥ 2 obs, T=120 (config) = restores the certified 2×hourly semantics under ANY cadence; a single
  sub-hourly tick can never flip state. Cert: walk-forward at both cadences; parity-on-hourly must be ≈
  identity (fix must NOT change what was certified); flip-rate reduction on mixed days, sign-stable both
  halves. KATs: half-hourly single-tick (no flip), genuine 2h decline (flips), hourly parity, mixture.
- **WP-5 `(None,None)` bucket (D):** `_parse_bucket` failure → quarantine (excluded from ladder AND de-vig
  denominator), recorded as `unparsed_outcomes`, watchdog AMBER if >0 on an active market. Never a
  `(None,None)→-inf` sentinel in a probability. Cert: exhibit-the-delta over snapshots. KATs: one-malformed
  (excluded+renormalized), all-parseable parity.
- **WP-6 `compact_buckets` all-tail (D):** make the degenerate case explicit; add a permanent runtime
  **partition invariant** (each integer cell claimed exactly once). Cert: exhibit-the-delta (expected tiny).
  KATs: degenerate fixture, property-sweep partition invariant, parity.
- **WP-7 `_resolve_truth` window+1 (FREEZE):** off-by-one is consistent across ALL truth paths → system's
  certified behavior IS window+1; "fixing" changes the served bias/skill learning window for zero benefit at
  full GATE cost. SAFE actions only: (1) fix the comment (state window+1 + why frozen), (2) reject
  non-positive `window` with a KAT (the genuine latent hazard: whole-series retention on window≤0), (3)
  FINDINGS entry "truth window = N+1, frozen canonical 2026-07; change is GATE with no identified benefit".
  A future measurement showing the +1 matters reopens this as a Class-R WP — the reopening condition is
  written now.

## §4 Phased execution
- **Phase 0 (this):** umbrella pre-reg (§§0–3) alone; ship `integrity_flags` field + filter (inert-proven,
  KAT'd); execute **Gate 0-B** (outage-episode inventory → branch declaration, amended into this pre-reg).
- **Phases 1–6 = WP-1 → WP-6, strictly serialized** (contamination model is a dependency chain). Each WP:
  addendum pre-reg → failing KATs → fix → retro-audit/replay artifact → cert commit. Exception: WP-5/WP-6
  (market-side) MAY parallel WP-3/WP-4 iff zero common files (verify with diff-stat, don't assume).
- **Phase 7:** WP-7 freeze + the §A/D/G DEFERRED hygiene backlog (6 conn wraps, `_twc_raw_high` dedup,
  `_wilson` center, compare.py recompute, shadow σ floor, `det_budget3`) — all SAFE; suite + campaign KATs
  green is the gate. Closure artifact: per-WP status, contamination totals, FINDINGS deltas, the two
  standing plans' unblocked milestones signed off.

## §5 Failure-mode checklist (read before each WP)
1. Hot-patch a §F one-liner because "obviously right"? → audit ruled; pre-reg first.
2. Overwrite a historical ledger value with its correction? → append `_v2`, flag it; originals are evidence.
3. Run a measurement that doesn't filter integrity flags? → METHOD-DEFECTIVE, stop.
4. Demand sign-stable improvement from a Class-D correctness fix? → category error; gate is the exhibited delta.
5. Class-R replay shows the fix worsens the metric? → don't ship, don't rollback-and-forget; explain first.
6. Change F7's window semantics while "in the neighborhood"? → FREEZE means freeze.
7. Two WPs on one file concurrently? → serialize; one owner per diff.
8. Retro-audit re-fetch failing intermittently? → checkpointed resumable job; partial audits certify nothing.
9. WP-3 cert without a Gate-0-B branch declaration, or switching branches because the declared one is
   inconvenient? → branch is a Phase-0 fact; changing it is a stop-and-report amendment, never a mid-WP swap.
10. Branch B/C artifact reporting pooled results without SYNTHETIC labels or the genuine-only subset? → an
    unlabeled synthetic is a fabricated genuine; METHOD-DEFECTIVE.

---
## Gate 0-B — outage-episode inventory & branch declaration (executed Phase 0 2026-07-11)

**Episode definition (frozen):** an outage episode = ≥2 consecutive failed/empty fetches of the
daily-max endpoint for one station, boundaries closed by ≥3 h healthy on both sides. Single-fetch blips
are logged, not episodes.

**Inventory — mining sources, in order (receipts):**
| Source | Evidence of daily-max endpoint outage |
|---|---|
| (1) `soft_failures` table | **0 rows** — no swallowed-failure records of ANY tag exist |
| (2) `logs/accumulate.log` (`_tail_status` archive) | 454 lines total; **0** daily-max/endpoint/timeout/429/outage lines |
| (3) watchdog duty history | not persisted to a queryable store (printed to logs); the daily-max endpoint is not a watchdog duty target |
| (4) HTTP-error rows in the ledger | no such persistence exists |

**Genuine episodes found: 0** (< 3).

**BRANCH DECLARED: C — SYNTHETIC-ONLY.** WP-3 certifies on **20 synthetic episodes** from the frozen
duration grid {1 h, 3 h, 6 h, 12 h, 24 h} × random start times **stratified by station** (RPLL, WSSS,
EGLC + OEJN, OPKC, KSFO) **and local time-of-day**, seed **`GATE0B_SEED = 20260711`** (deterministic
regeneration). Masking is applied at REPLAY time only — the store is never mutated — and every masked
replay runs on WP-2-corrected daily maxes (an outage replay against uncorrected values stacks two
defects). Every episode is labeled SYNTHETIC in the WP-3 artifact.

**HEADLINE (stated plainly, per checklist item 10):** NO genuine-outage validation exists. WP-3's
real-world outage behavior is **MEASURED-PENDING** until the first live daily-max outage is observed and
reconciled. The "0 genuine episodes" reflects SHALLOW evidence, not proven zero-outage history:
`soft_failures` is empty and `accumulate.log` holds only ~recent lines, and pre-fix `_tail_status`
under-reported stderr failures (the stderr-merge fix shipped 2026-07-11 in commit aa1ca2a; historical
entries predate it). This is not "the endpoint never failed" — it is "no failure is recoverable from the
retained logs."

**Free side-product (per §4):** this inventory is also the first measured estimate of historical
fetch-log retention — `soft_failures` = 0 rows, `accumulate.log` = 454 lines. That shallow-retention fact
directly informs WP-1's resumable-audit horizon and the standing plans' revision-window measurements
(don't assume logs reach further back than they do).
