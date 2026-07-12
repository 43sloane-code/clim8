# DRIVER-FIRST AUDIT — weather council mechanisms (2026-07-12)

*Same law, same frame as tv_trading_agent/DRIVER_AUDIT.md: every mechanism graded by
what it rests on — IDENTITY (cannot die while the settlement rule holds) > DRIVER (a
causal mechanism, watchable in its own series) > PATTERN (a coincidence not yet
disproven, dies only after losses) — with the kill condition ON the driver and the
monitor that watches it. Headline finding: this system is built top-of-hierarchy, and
several guards long described as "verification instruments" are, properly named,
DRIVER-HEALTH MONITORS already running. One real coverage gap found; registered, not
hot-patched.*

## Tier 1 — IDENTITIES (die only if the settlement contract itself changes)

| Mechanism | Identity | Watched by |
|---|---|---|
| Running-max ratchet (banked floor) | A daily max is monotone; an observed floor can never un-happen | Settlement KATs; Duty 3a (truth source pinned to Wunderground — a *contract* watch) |
| Post-sunset "final" | Solar forcing ends; for a daily-max market the day is closed | NOAA solar calc validated ±5 min vs certified lock clocks; polar edge cases return None (never claim) |
| Settlement quantizers (round-half-up / floor / grain per city) | The contract's own rule | KATs incl. the London/HK/SF grain suite; D19 is the proof even identity-adjacent grain choices go through the gate when they touch a served distribution |

This tier is why intraday is the only conviction lever: it is built on accounting,
not inference. Nothing here has a "driver series" to watch — only the contract.

## Tier 2 — DRIVERS (physics/mechanism named; kill condition; monitor status)

| Mechanism | Driver | Kill condition on the driver | Monitor (status) |
|---|---|---|---|
| Remaining-rise pmf (intraday climatology by hour) | Diurnal solar forcing makes each station's peaked-by distribution stable | Distribution shift: seasonal transition, station relocation/re-siting | **Watchdog Duty 2** — replay crossover hit-rates vs pinned baseline. LIVE, and currently flagging WSSS@14:00 (−4.3%): the monitor working, adjudication pending |
| day_state (2-consec decline) + state×season raise-risk | Peak-formation persistence physics (certified 2026-07-06) | Feed cadence change (2-consec semantics assume ~half-hourly obs) | Partial: feed-liveness heartbeats (eval_harness); cadence itself unwatched — minor, noted |
| cur_f lead + endpoint corroboration (tape/grade engine) | The WU daily-max endpoint aggregates between-obs specials; v3 is a fresher read of the SAME instrument | WU changes aggregation/latency behavior → leads stop banking | **The tape** (ledger/intraday_tape.jsonl): lead-bank rate IS the driver's expression, accruing (n=3 days — hypothesis-grade per the consistency law); frozen-stamp check kills stale reads at open |
| Council skill (the imported edge) | NWP physics + data assimilation (Bauer 2015) — manufactured upstream, consumed here | Member pipeline upgrades reset a member's bias regime (ECMWF ships new cycles ~2×/yr) | **Duty 3b** pins ECMWF@Changi bias vs a fixed reference, RED on drift — a true per-member break watch, but coverage = 1 member × 1 city (GAP G1 below); recency_bias.py evaluates drift-tracking correction leak-free (recommend-only); PIT/coverage daily check catches consequences downstream |
| Gated bias corrections | Persistent station representativeness offsets (coastal/urban/grid-vs-point) | Same upgrade/re-siting breaks as above | Same trio; every correction fold-gated before serving |
| PoP regime split (accruing, n=4/15) | Cloud albedo + evaporative cooling suppress Tmax on rain days — physics named BEFORE the gate | Fails the frozen fold gate → dead ledger | Driver-first by construction; waiting is the work |
| TWC 9th-member candidate | Shared verification target (audited separately — twc_member_gate.md, the template: two drivers + freshness confound + G3′) | Offset break / error-sd crossover / boundary-stratum loss / correlation rise | twc_forecast_logger daily; gate fires at n=40 (~1 week) |

## Tier 3 — PATTERNS (correctly quarantined: display-only, never served as probability)

- Archive-pattern / chart layer (SF and pattern displays) — informational lens only.
- Data-interpretation lines (climatology position, trend, regime lean) — descriptive,
  explicitly "never blended" (the D18 disposition printed in the render).
- The refused class IS this tier: 0/19 day-ahead conditioners were patterns or
  sub-bucket drivers; the gates killed them before losses could. And the market-edge
  claim has no driver on offer (why would a lagged-data solo consumer beat the
  aggregate? — 44%=44% measured), so C7 correctly stays un-validated and every nudge
  stays annotation.

## Renamed, now correctly: the driver-health monitors already running

Duty 2 (climatology drift) · Duty 3a (settlement-contract drift) · Duty 3b (flagship
member-bias break) · PIT/rank-histogram daily (residual-cloud stationarity) ·
spread–skill check (ensemble honesty) · the tape (lead-mechanism expression) ·
crossover re-emitted every accumulate run. The audit's main output is this relabeling:
the system was driver-first in structure before it had the word.

## The own-forecast program (P1–P3) — audited phase by phase (2026-07-12 extension)

The program's existential driver question: why should a stdlib model trained on station
history add anything to 8 NWP systems plus gated bias correction? PLAN_OWN_FORECAST.md
answered it honestly BEFORE the doctrine existed: the council is already EMOS-class (the
raw→EMOS −30% CRPS step is banked), so the only driver on offer is **the predictor step**
(Taillardat: the gain is the predictors, not the learner) — worth a few bucket points at
best, near the noise floor, "the likely honest outcome is closure." That is a driver
named at its true strength, with expectations priced in advance.

| Phase | Driver named | Fate | Driver-first reading |
|---|---|---|---|
| P1 data layer (13y tables, °F-grain-aware) | none needed — infrastructure | DONE | Tier-neutral enabler; grade A |
| P2 state×cloud conditioning (12–14h) | morning cloud → afternoon rise physics | **D15** | The audit's centerpiece — see below |
| P2b 12:00-only forward carve-out | same physics, at the one hour BEFORE absorption | ACCRUING 8/60 | Driver-first compliant by construction: frozen terciles, DEPLOYABLE live feed (D11's reanalysis lesson encoded), BOTH arms logged so failure is attributable |
| P3 k-analog day-ahead (Taillardat set) | predictors the residual cloud ignores | **D17**; Stage A CLEARED, Stage B re-gate 6/40 | Predictor driver PROVEN real vs naive baselines (CRPS −6.4% vs climatology, sign-stable, first-ever full stage clear); unproven vs the council — the redundancy confound is the whole remaining question |
| Informational own-model line | display only | SERVED (never blended) | Correctly quarantined pending Stage B |

**The centerpiece: D15 is the hierarchy explaining itself.** The probe measured the cloud
driver as REAL at 12:00 (+5.9pt, fold-stable) and DEAD by 13:00 — and its autopsy names
the mechanism: *"morning-cloud information is absorbed into the running-max ratchet by
~13:00."* An IDENTITY out-competed a DRIVER: the ratchet consumes the same information
faster than any conditioner can exploit it. This is why the entire intraday conditioning
family (D07/D08/D11/D13/D15) kept dying — not because the drivers were fake, but because
they were racing an identity and losing. The one surviving slice (P2b) is exactly the
hour before the identity has eaten the information. The doctrine's hierarchy is not a
taxonomy here; it is the causal explanation of five dead candidates.

**The redundancy confound (P3 Stage B's real question):** the own model sees TODAY's
local state; the council's members embed TOMORROW's assimilated physics. Any Stage-B gain
must therefore live in the thin slice where next-day structure is predictable from local
today-observables BEYOND what NWP assimilated. Both open preregs now carry pre-completion
ADJUDICATION notes (criteria untouched, clocks at 8/60 and 6/40) requiring gain
attribution before any promotion — the TWC convention, applied here.

## Gaps (honest, dispositioned)

- **G1 — member-bias break coverage.** Duty 3b watches ECMWF@Changi only; the other
  7 members × basket cities break silently until PIT/coverage catches the downstream
  effect. Fix is cheap and recommend-only (per-member rolling bias vs pinned reference,
  alert-only, no served numbers) but it is NEW instrumentation → registered for proper
  execution with KATs, not hot-patched: `ledger/preregistered/member_bias_break_watch.md`.
- **G2 — tape/lead driver at n=3 days.** Not a design gap; an accrual gap. Time fixes
  it; the consistency law forbids claiming it before n does.
- **G3 — day_state cadence assumption.** Unwatched directly; consequence-watched via
  Duty 2 + heartbeats. Recorded, not acted on (sub-minor).

## Scope guards

No served number touched. No dead lever relitigated (day-ahead 0/19 stands). The one
action item (G1) is registered, needs its own KATs, and adds an alert — never a
correction — because a break watch that silently "fixes" biases would be a served-number
change wearing a monitor's name.
