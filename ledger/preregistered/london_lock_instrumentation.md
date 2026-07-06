# REGISTERED — London lock-certification instrumentation gap + crossover guard

*2026-07-06 defect sweep. Two instrumentation gaps from London's basket promotion, registered
for proper execution rather than hot-patched into a certification instrument at turn end.*

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
