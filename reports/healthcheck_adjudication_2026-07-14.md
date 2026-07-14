# Adjudication of the 2026-07-14 healthcheck DRIFT-FLAGGED report (per-item, per the laws)

1. MAE regression +0.0513°C vs baseline: barely over REGRESSION_TOL (0.05) and HALF the
   measured live-feed noise floor (~0.1 — feedback_backtest_ab_needs_frozen_data). Flag
   STANDS, no tuning, no baseline re-pin (silencing move). Re-adjudicate only if it
   persists and grows across runs.
2. 80% coverage 74.5% (OVER-CONFIDENT): real (binomial CI excludes 80). The one permitted
   fix (cand-50's scalar variance-match inflation) was pre-registered, probed once, and
   FAILED its frozen bar -> D26. The flag stands as a known measured limitation; any
   future conditional design needs its own registration.
3. PIT WARM TILT (z=4.5): real; its legal challenger (recency-weighted bias) is
   re-adjudicated NIGHTLY by the healthcheck itself and lost again today (0.7031 vs
   0.7121). No hand-recentering. Stands as measured limitation with a standing challenger.
4. Rank histogram U-shape (raw panel): known and already served with its explanation —
   the council serves the wider held-out residual cloud, not the member spread. No action.
5. DISP tiers zero held-out days: monitoring-coverage note for the human as the report
   itself said; tier windows are healthcheck-internal and were NOT tuned on one run.
6. Basket note: tools/daily_healthcheck.py's own boilerplate is ALREADY accurate
   (Manila+Singapore, 2-city power caveat, re-widen = human change). The stale
   "8-city / London+HK" text lives in the SCHEDULED TASK's prompt — update that prompt
   (user-side / task settings), not the repo.
7. C7 UNVALIDATED, market beats council: the standing measured law (no live edge); the
   gate is holding exactly as designed. No action, by design.
Probe artifacts this adjudication produced: additive `resid` return from
_walk_forward (monitoring-only), reports/backtest_dispersion.py, D26.
