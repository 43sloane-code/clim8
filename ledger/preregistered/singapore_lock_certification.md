# Pre-registration — LIVE certification of the Singapore intraday lock (FROZEN 2026-07-03)

*The intraday lock is the system's flagship claim — "the pinpoint locks at ~95% by 15:00 SGT,
~98–99% later" — and it is the ONLY load-bearing claim in the stack with no live, logged, scored
record: its numbers are backtests, its live wins are chat anecdotes and unscored `.txt` files. This
file freezes the certification bar BEFORE outcome one. The commit hash is the timestamp.*

## What is logged (point-in-time, never retro-computed)
`tools/lock_logger.py`, run by the existing automation (daily_verdict 09:00 + 15:00 SGT;
accumulate ~16:00 SGT), appends one row per (target_date, hour) to `ledger/singapore_lock.jsonl`:

    {target_date, issued_ts, hour, kind, running_max_c, n_rise,
     modal_bucket, modal_prob (the STATED conviction), pmf_top4, source}

- The row is written at the moment the lever runs — the same leak-free point-in-time rule as the
  PoP and TWC ledgers. A row may also be SEEDED from an existing dated `reports/verdict-*.txt`
  file (those files are themselves point-in-time artifacts, timestamped in the filename at write
  time) — seeding parses, it never re-computes.
- `kind != "sharpened"` rows (no lock available) are logged as abstentions: counted for an
  availability rate, excluded from coverage.

## Settlement
Nightly, rows for past city-local days are settled against the WU/Changi record (whole-°F → the
round-half-up °C settlement bucket — the market's own rule). `hit = (modal_bucket == settled)`.

## The frozen certification bar (ONE rule; no alternates → no trial inflation)
Certification hours: **{12, 13, 14, 15, 16, 18}** (other hours are context). Per hour, at
**n ≥ 20 settled sharpened rows**:

    CERTIFIED      iff  empirical hit-rate ≥ mean(stated modal_prob) − 10pp
    OVERCONFIDENT  otherwise → the served conviction LABEL for that hour is DOWNGRADED to the
                   empirical rate (the "95%"/"99%" language may no longer be served for that hour)
    ACCRUING       while n < 20 (no certified language may be added on partial data)

Reference backtest claims being certified (WU-native Singapore): 12:00 ≈51%, 13:00 ≈53%,
14:00 ≈80%, 15:00 ≈95%, 16:00 ≈98%, 18:00 ≈99%.

## Pre-registered distinction: DOWNGRADE, not kill
This gate certifies the lock's **live label**, it does not kill the lock: the mechanism (running
max + leak-free remaining-rise) is already backtest-proven sign-stable. If an hour reads
OVERCONFIDENT, the served claim for that hour becomes the empirical number — nothing else changes.
Conversely, nothing about "the lock is 3/3 live this week" may be cited as evidence anywhere until
it appears in this ledger's coverage table.

## Status
INSTRUMENTATION LIVE — clock started 2026-07-03. Recommend-only; no served text changes until an
hour reaches n ≥ 20. Related: [D14]/PoP pre-registrations (same freeze discipline),
`verify_skill.py` (the coverage-vs-stated-conviction adjudicator this bar copies).

## Documented feed breakpoint — 2026-07-04 (rows carry `feed`)
From 2026-07-04 evening, LIVE lock rows fuse the oracle's v3 current-conditions read +
24h register into the running max (floor-raise-only, attribution-gated vs yesterday's max —
`sources._fuse_live_floor`). Replays/backtests (explicit `now_hour`) remain v1-history-only,
so the backtest reference numbers are unchanged. Rows record `feed: "v1" | "wu+live"`; if the
certification table is ever split by feed era, this is the boundary. The bar itself (n>=20,
-10pp) is UNCHANGED. Companion guard: `lock_logger.settle_cross_check` warns when a settled
bucket is lower than the day's banked register floor (the 92-vs-91 class) — warn-and-stamp,
never silent rewrite.
