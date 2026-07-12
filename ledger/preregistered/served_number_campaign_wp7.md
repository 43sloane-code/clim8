# WP-7 addendum — `_resolve_truth` window+1 — FREEZE-AND-DOCUMENT

*Served-number campaign, WP-7 (F7). The audit established the off-by-one is CONSISTENT across all truth
paths — the system's certified behavior IS window+1, and every backtest / bias / skill number was
learned under it. "Fixing" it to `window` changes the learning window of served bias/skill for zero
demonstrated benefit at full HARD RULE 1 cost. The defect is the COMMENT; comments are SAFE. This is
FREEZE-AND-DOCUMENT — no behavior change.*

## SAFE actions (shipped)
1. **Comment fixed** (`council.py:963`): "most-recent window+1 days (canonical N+1 truth window,
   frozen — WP-7)" instead of the wrong "most-recent window days".
2. **Non-positive-window guard** (`council.py`, top of `_resolve_truth`): `window = max(1, int(window))`.
   The genuine latent hazard the audit found: a NON-POSITIVE window makes the WU slice `[-(window+1):]`
   = `[-0:]` retain the ENTIRE series (window=-1). The clamp makes that impossible on every path.
   KAT `test_council.TestWp7WindowGuard`: window=-1 on an 80-day fake WU series → obs clamped (< 80),
   never the whole history.
3. **This entry** records N+1 as canonical, frozen 2026-07.

## Reopening condition (written now)
If a future MEASUREMENT ever shows the +1 materially changes served bias/skill (leak-free, both halves),
THAT measurement reopens this as a Class-R WP with its own pre-reg. Absent it, window+1 stays frozen.
