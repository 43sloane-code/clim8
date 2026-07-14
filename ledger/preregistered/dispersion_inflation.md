# Pre-registration — scalar variance-match dispersion inflation (frozen before scoring)

*2026-07-14. Response to the healthcheck's measured dispersion defect: 80% interval
coverage 74.5% (n≈404; binomial CI [70.3, 78.7] EXCLUDES 80 — real, not noise) and PIT
under-dispersion. LEDGER LINEAGE, checked per the analyzer law: D10 set "coverage recal"
aside as a SCOPING deferral ("single bucket is the point"), not a measured kill; the
cand-50 closure explicitly names "scalar variance-match spread inflation" as the genuine
lead PERMITTED through the frozen gate ("don't hand-inflate on one read"). This is that
gate. ONE attempt; probe `reports/backtest_dispersion.py`; a PASS licenses the serving
implementation (with KATs); a FAIL is a dead-ledger entry and the healthcheck flag stands
as a known limitation.*

## Frozen design
- Data: the settled verdicts stream per city (Manila, Singapore; err_high = served high −
  settled actual; n≈222 each), chronological.
- Walk-forward, warmup 60 days, expanding window. At day t:
  - incumbent cloud = past errors e_1..e_{t−1}; 80% interval = its empirical 10th–90th pct.
  - candidate = the SAME cloud with deviations-from-cloud-mean scaled by
    **s_t = pstdev(last 60 errors) / pstdev(all past errors)**, floored at 1.0 (inflation
    only — cand-50's direction; a deflation lever would be a different candidate).
  - No other parameter. No tuning. The 60-day window mirrors the module's own recency
    convention; it is NOT swept.
- Scored per day: (a) 80%-interval coverage hit; (b) CRPS of the (scaled) empirical cloud
  vs the settled error. NOTE: scaling around the cloud mean leaves the MODAL bucket
  unchanged — bucket-hit is invariant by construction (stated, not tested).

## Frozen pass criteria (ALL required)
1. |coverage − 80| DECREASES vs incumbent on BOTH chronological halves of BOTH cities
   (four cells, all must improve).
2. Pooled CRPS does not worsen by more than 1% vs incumbent.
3. n ≥ 150 scored days per city (else ACCRUING).
FAIL → dead-ledger **D26** (and D10's set-aside becomes a measured kill).
PASS → implement in the served-cloud path with KATs; until implemented, nothing served
changes and the healthcheck flag remains accurate.

## What this does NOT address (dispositioned separately)
The PIT WARM TILT (mean bias, z=4.5): its gated challenger (recency-weighted bias) is
re-adjudicated NIGHTLY by the healthcheck and lost again today (MAE 0.7031 vs 0.7121) —
the tilt stands as a measured limitation whose legal fix keeps losing its hearing. Scaling
is symmetric and cannot fix a tilt; no hand-recentering is permitted.

## OUTCOME 2026-07-14 — ACCRUING/unreachable (data-reality; ZERO candidate numbers read)

Two loader corrections during fetching (documented, criteria untouched): (1) the live
verdicts ledger holds 6 Manila / 131 Singapore settled rows — the stated n≈222 is the
healthcheck's BACKTEST stream; (2) that stream, correctly loaded via the healthcheck's own
_city_votes + _walk_forward (live variant, additive resid return), yields ~51 post-warmup
HIGH-attr days per city — the report's "n=222" is 2 attrs × 111 days. The frozen per-city
floor of 150 was calibrated on that misreading and is unreachable for months on any
loader. Both probe runs exited at the n-gate BEFORE any candidate coverage/CRPS was
computed. Design SPENT; successor v2 (below in this file) pools high+low per city
(n≈102/city) with the floor recalibrated to the data that exists — same candidate, same
criteria shape, no evidence inherited.

# Pre-registration v2 — same candidate, pooled attrs, recalibrated floor (frozen before scoring)

- Data: the SAME healthcheck backtest residual streams, HIGH and LOW pooled in day order
  per city (n ≈ 102/city — matching the healthcheck's own pooled PIT convention, n=404
  basket-wide, where the defect was measured).
- Candidate, scoring, and criteria 1–2 VERBATIM from v1 (variance-match s=pstdev(last60)/
  pstdev(all) floored at 1.0; |coverage−80| must decrease in ALL FOUR city×half cells;
  pooled CRPS ratio ≤ 1.01).
- Criterion 3 recalibrated: n ≥ 80 pooled days per city, else ACCRUING.
- One attempt. FAIL → D26. PASS → implement in the served-cloud path with KATs.

## v2 OUTCOME 2026-07-14 — FAIL → D26 (one attempt spent)

Manila H1 .716→.765 IMPROVED · Manila H2 .716→.778 IMPROVED · Singapore H2 .741→.802
IMPROVED · **Singapore H1 .790→.790 NOT improved** (already ~calibrated; s barely fired,
1.015). Pooled CRPS 0.9990 (within bar). The frozen all-four-cells criterion FAILS →
dead ledger D26, autopsy recorded there (the candidate is inert where no defect exists —
informative for any future CONDITIONAL design, which would need its own registration).
The healthcheck's OVER-CONFIDENT flag stands as a known measured limitation.
