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
