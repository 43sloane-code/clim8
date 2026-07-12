# REGISTERED — London lock-certification instrumentation gap + crossover guard

*2026-07-06 defect sweep. Two instrumentation gaps from London's basket promotion, registered
for proper execution rather than hot-patched into a certification instrument at turn end.*

## EXECUTED 2026-07-12 (both items; gate green; no served number touched)

1. **lock_logger per-city — DONE.** `CITIES` config (Singapore + London), city field with
   migration default "Singapore", city-scoped settle/coverage/report (London rows can never
   pollute Singapore's FROZEN table — KAT'd), parameterised log_now(Place/TZ). **One
   registered instruction SUPERSEDED, documented:** item 1's "London = IEM-EGLC" line
   predates the 2026-07-07 user directive ("wunderground only"), which explicitly routes the
   LIVE LOCK through the WU EGLC record — London settlement here reads WU (whole-°F daily max
   → whole-°C round-half-up), matching test_london_settlement_is_wunderground_backtest_is_iem.
   London cert hours (13,14,15,16,17,18) local, same frozen n≥20 / −10pp bar. Scheduling gap
   closed via tools/tape_logger.py (its 15:30 London-local firing is the only runner inside
   London's cert window). First London row: 2026-07-12 14:00 modal 29 @ 0.73.
   KATs: tests/test_lock_logger.py::TestPerCityLedger.
2. **Crossover guard — DONE.** accumulate now emits BOTH cities (merge-by-ICAO) into
   crossover_now.json; crossover_baseline.json re-pinned as a DOCUMENTED BREAKPOINT: WSSS
   values byte-identical, EGLC added from a clean 2026-07-12 emit
   (13:00 .420 / 14:00 .655 / 15:00 .832 / 16:00 .933 — window-rolled from the registered
   44.5/69.7/86.6/95.0). Duty 2 now REDs on a missing EGLC fold — the intended guard.
   **Pre-existing finding surfaced (NOT silenced):** Duty 2 was ALREADY red on WSSS@14:00
   (replay 75.0% vs pinned 79.3%, beyond the determinism band) before this work; left
   un-re-pinned deliberately — adjudicating that drift is watchdog's job, not this refactor's.

1. **lock_logger is Singapore-only** while London serves a daily lock. London's live lock
   conviction can therefore never certify (the exact class the eval harness ranks #1). Fix =
   a real refactor: per-city rows (city field + migration default "Singapore"), parameterised
   log_now (Place/TZ), per-city settlement (London = IEM-EGLC whole-°C, NOT WU/Changi),
   per-city certification tables; Singapore's frozen bars unchanged. Recommend-only
   instrumentation — no served numbers — but it writes the certification ledger, so it ships
   with its own KATs and a schema note, not as a quick patch.
2. **crossover_baseline.json is WSSS-only**: watchdog Duty 2 regression-guards Singapore's
   certified instrument but not London's (13-16h certified 44.5/69.7/86.6/95.0). Fix = emit
   London crossover in the accumulate watchdog chain, merge with WSSS into crossover_now.json,
   re-pin the baseline with London rows as a DOCUMENTED BREAKPOINT (like the 07-04 re-pin).

Neither touches served numbers. Both are instrumentation-first work for the next session.
