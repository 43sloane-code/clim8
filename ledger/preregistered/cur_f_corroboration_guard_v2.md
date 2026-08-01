# Pre-registration — cur_f corroboration guard v2 (frozen design + Phase 0 recon)

*2026-07-11. SUPERSEDES `cur_f_corroboration_guard.md` (v1, uncommitted draft). Operator-authored v2
execution plan is the frozen authority; this file records it as the pre-reg and appends the Phase 0
recon outcomes. Served-pick AND served-percentage change → full pre-registration, KAT-certified,
shadow-mode gated. Certification = KAT replay (deterministic feed logic); NO CRPS gate (correct class).*

## Frozen design (per operator v2 plan — binding)
- **Problem (two coupled harms):** an uncorroborated `cur_f` nowcast (1) advanced the banked floor
  above the recorded hourly, and (2) — the crux — stamped observation-grade confidence ("94%") on it.
- **Guard = two coupled gates.** Gate 1 Banking: `cur_f` advances the banked floor above the recorded
  hourly ONLY when `CORROBORATED = fresh ∧ (sustained ∨ converging)`. Gate 2 Confidence-provenance:
  the served % is a pure function of provenance ∈ {RECORDED, CORROBORATED_NOWCAST, UNCORROBORATED_NOWCAST};
  observation-grade %/“banked” vocabulary is reserved for RECORDED obs.
- **Five v1→v2 defect corrections (binding):** D1 no fabricated % at any tier — corroborated tier
  serves the RECORDED-bucket % until a MEASURED confirmation rate clears the §5 promotion gate;
  D2 liveness (corroborating reads differ on ≥1 secondary field, ≥5 min apart) defeats stale-value-
  fresh-timestamp spoofing; D3 converging bounded (|cur_f−recorded|≤2°F AND pre-peak; unavailable
  post-peak); D4 per-city adaptive freshness = clamp(1.5×trailing-median inter-obs, 10, 45 min);
  D5 4 frozen incidents + 5 synthetic adversarial KATs, ground truth verified before freeze.
- **§5 promotion:** corroborated-tier own-% enters FINDINGS.md MEASURED-PENDING at go-live; → SUPPORTED
  only at n≥30/city reconciled, served as the Jeffreys 95% lower bound, disjoint fit/verify, pooling
  compatibility test, regression tripwire. **Fail-closed** everywhere on state fault.
- **9 KATs, exact-match, no partial cert.** **Out of scope:** `cur_f ≤ daily-max` clamp (re-breaks
  Jeddah), CRPS gating, new data sources, changing the RECORDED-floor lock %.

## Phase 0 recon outcomes (2026-07-11)

### Ground truth — VERIFIED against the final WU records (fixture headers freeze these)
| Incident | Settles | Evidence (WU record, retrieved 2026-07-11) | Guard expectation |
|---|---|---|---|
| **K1 Jeddah 07-11** | **37°C** | hourly recorded **99°F (37.2°C) at 16:00** — a real re-heat; daily-max endpoint 99°F/n=20. `cur_f` 98°F sustained at 16:06 & 16:16 LED the lagging hourly (which still showed 15:00/95°F then). | CORROBORATED (sustained ∧ converging: record rose 95→99) → **banks 37** |
| **K2 London 07-11** | **27°C** | hourly plateaued **81°F (27.2°C) 13:20→17:20, NEVER re-heated** (daily-max 81°F/n=35); `cur_f` a persistent +1–2°F over-read (83°F frozen at one valid_local; still 82°F at 18:16). | UNCORROBORATED (single stale ts, record flat) → **27; % on 27**; 28 = unconfirmed annotation, no % |
| **K5 Jeddah 07-09** | **38°C** | hourly peak **100°F at 10:00 (MORNING)**; daily-max 100°F/n=24. The 102°F phantom was the max24 REGISTER, post-peak while declining. | no bank: register cap (existing 2eafce1) → 100; `cur_f`≈99<100 doesn't bank; converging unavailable post-peak → **38** |
| K3 Singapore 07-04 / K4 London 07-07 | 32 (07-07) | register-LEAD cases (max24, not cur_f); carried from existing frozen KATs (a42ffa2/6533fca). Re-verify vs WU if still in window at fixture freeze. | register still fuses (unchanged path) |

**The two live incidents are verified-OPPOSITE** — Jeddah's `cur_f` led a real re-heat the record confirmed; London's was a pure over-read the record never posted. This is exactly the discrimination the corroboration engine must make, and both are now data-anchored, not asserted.

### Open questions (operator) — answered where recon could
1. **Secondary fields per city (Q1):** ✅ all 6 active cities (EGLC/OEJN/WSSS/RPLL/OPKC/KSFO) expose
   **5 usable secondaries** (temperatureDewPoint, windSpeed, pressureMeanSeaLevel, relativeHumidity,
   windDirection). The Phase 0 liveness contingency is **NOT triggered** anywhere.
2. **07-09 local time (Q2):** ✅ peak **10:00 local (morning)**; the phantom was **post-peak** → K5
   killed by converging-unavailable (+ register cap), not proximity.
3. **Jeddah activation (Q3):** ⚠️ operator input needed — Jeddah is currently **on-demand**, not in
   the auto-basket (Manila/Singapore/London). Shadow-run roster (§7 Phase 6) must match the live roster.
4. **State-conditional lock % location (Q4):** ✅ `weather_council/intraday_ceiling.py` —
   `state_late_risk()` (+ the Ceiling dataclass `prob`/`tier`/`state_late_risk`), rendered in
   `run.py` (INTRADAY FLOOR/LOCK lines ~1000/1057). ServingTable **calls** these, never reimplements.

### Repo reconciliation (§1.3 layout deltas — plan assumed `clim8/…`, pytest)
- No `clim8/guard/` or `config/` dir existed. Adopt: **`weather_council/guard/`** package (obslog,
  corroboration, banking, serving, provenance, reconcile) + **`config/guard_cities.json`** (created
  this phase) + KATs under **`tests/`** (flat, `test_*` unittest style — repo uses **`unittest`, not
  pytest**; verify via `PYTHONPATH=. python3 -m unittest`, gated by the existing pre-commit hook).
- The existing live-floor logic is `weather_council/sources.py:_fuse_live_floor` (KAT
  `tests/test_live_floor.py`); the guard wraps/feeds it, it is not deleted.

### MATERIAL DISCLOSURE — fixtures are RECONSTRUCTED, not extracted from logs (checkpoint #1)
The system does **not** currently persist v3 read-sequences (cur_f + valid_local + secondaries over
time). The existing KATs are single-snapshot `_fuse_live_floor(...)` calls. Therefore:
- Fixture **OUTCOMES (settlements) are data-VERIFIED** against the final WU records (table above).
- Fixture **read-SEQUENCES + secondary fields are RECONSTRUCTED**: K1/K2 from the values captured live
  in-session today (Jeddah 98°F@16:06&16:16 + record 99°F@16:00; London 83°F frozen + record 81°F flat)
  with **plausible synthesized secondaries** (real dewpoint/wind/pressure for those exact reads were
  never logged); K3–K5 fully synthesized from the verified settlement + incident narrative.
- **The guard's REAL corroboration data begins accumulating at Phase 1** (ObsLog go-live). The incident
  KATs are faithful reconstructions with verified outcomes, not replays of stored feeds. This is a
  known, disclosed limitation — the fixtures test the LOGIC against verified outcomes; live evidence
  (§5) accrues forward.

## STOP — Human review checkpoint #1 (fixture-expectations sign-off)
Phase 0 deliverables complete: repo reconciled, Q1/Q2/Q4 answered, K1/K2/K5 settlements verified,
`config/guard_cities.json` written, disclosure recorded. **No serving-path code written.** Awaiting
operator sign-off on (a) the reconstructed-fixture disclosure, (b) Q3 Jeddah roster, (c) the peak
windows, before Phase 1 (ObsLog) begins.

## EXECUTED INCIDENT — K6 San Francisco KSFO 2026-07-31 (appended 2026-07-31, PENDING-VERIFICATION)
| Incident | Settles | Evidence | Guard expectation |
|---|---|---|---|
| **K6 SF KSFO 07-31** | **PENDING-VERIFICATION** (NWS CLISFO not yet published at append time) | WU record (hourly obs, METARs, daily-max endpoint) **never exceeded 72°F all day**. v3 `cur_f` printed **74°F ~12:00–14:00 PDT on a frozen `valid_local`**; session-captured run outputs 13:52 & 14:54 PDT show the 74 lead appear (pmf floored at 74, modal 74°F 32%) then vanish when cur_f refreshed down. Pre-guard the label said "UNCORROBORATED" but the served percentages moved — the guard-v2 crux harm. Same class as K2 London 07-11. | UNCORROBORATED: pre-peak and \|74−72\|=2°F meets the ≤2 converging bound BUT liveness fails (single stale read — identical payload re-served) and freshness fails (frozen stamp) → floor/pmf base **stay 72**; 74 = annotation, **no %** |

Context (not a specimen — its read-sequence was not captured): 2026-07-30 had the same shape
(cur_f 70 lead over banked 69). K6 is certified as KAT `test_k6_sanfrancisco_2026_07_31_...`
in `tests/test_cur_f_guard.py`; its fixture read-sequence is RECONSTRUCTED per the MATERIAL
DISCLOSURE above (the live reads were session-captured, not machine-persisted — ObsLog go-live
is what makes the next K6 replayable). The CLI settlement will be marked verified/rejected here
once the CLISFO prints.
