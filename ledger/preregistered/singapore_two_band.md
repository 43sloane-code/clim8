# Pre-registration — Singapore day-ahead two-bucket band (FROZEN → KILLED)

*This file is the checkable record that the falsifiable criteria below were fixed BEFORE the
backfill was scored, and that the hypothesis was killed on its own pre-registered floor — not by
moving goalposts. The commit hash is the timestamp of the freeze.*

## Hypothesis (reviewer, 2026-07-02)
WSSS (Changi) Tmax is maritime-damped into ~29–33°C; post-MOS residual σ in the 0.7–0.9°C zone
*might* support a **two-bucket band** at an honest 73–85%. Proposed **cool skew `[P−1, P]`** on the
belief the fat tail is **cold** (Sumatra squalls / monsoon-surge rain days delete 1–2°C from Tmax
by capping the diurnal peak hours early).

## Pre-registered gates (fixed BEFORE any outcome was scored)
- **Gate 3 (bias precondition):** `|council Singapore residual mean| ≤ 0.30°C` — else a quantile
  band is mis-centered and its width lies.
- **Gate 1 (containment, falsifiable):** the cool-skewed two-bucket band contains the settled
  bucket on **≥75%** of settled days (~8/10). **Below 60% (6/10) → DEAD at current σ → revert to
  three buckets.** Reviewer's explicit falsifiable prediction: ≥75%.
- **Gate 2 (n floor):** conviction label withheld ("experimental, unlabeled") until ≥20 settled
  Singapore days.
- **Gate 4 (money, separate):** edge = empirical coverage − combined adjacent-bucket ask − cost,
  must clear on ≥20 obs before $1. Expected to fail even if 1–3 pass (efficient boring market).

## RESULT — `tools/two_band_backfill.py` (LOO, read-only)
Scored on all settled Singapore days incl. 06-30/07-01/07-02 (n=12; DB-only subset n=9 agrees):

- **Gate 3: PASS** — residual mean **−0.17°C** (centered). **But σ = 1.36°C — 2× the assumed
  0.7–0.9.** The cold tail doesn't skew σ, it inflates it.
- **Gate 1: FAIL → DEAD.** cool `[P−1,P]` **6/12 = 50%** (below the 60% floor); warm `[P,P+1]`
  8/12 = 67%; **no two-bucket skew clears 75%** — the data-driven 15–85 quantile band reaches 75%
  only at **width ≈ 3.5 buckets** (i.e. three-plus, not two).
- **Premise falsified.** Residuals are **BIMODAL**: a **warm body** (the council under-calls; on the
  non-tail days the settled bucket sits at/above the point → the cool skew *clips* it) **+ a rare,
  DEEP cold tail** (06-22 residual −2.8, 06-30 −3.1 on squall days). A −3°C squall day is
  unreachable by *any* static one-bucket skew.

## VERDICT: DEAD. Served Singapore band stays THREE buckets.
The reviewer's **kill-before-instrument** rule fires: Gate 1 < 6/10 ⇒ **PoP-logging is MOOT.** The
30-day PoP clock is **deliberately NOT started** — instrumenting a hypothesis that failed its own
viability floor is precisely the trap the rule exists to prevent. No `pop_value` column is added.

## The one deferred path (NOT instrumented, on purpose)
The failure is **separable** (≈9 tight days + 2–3 squall bombs), which is the textbook motivation
for a PoP-conditioned regime split. But: (a) it cannot be validly tested on existing data —
defining the regime by the residual *outcome* is circular; (b) it needs **point-in-time** PoP
logged forward (retro-fetch = look-ahead leak); (c) its intraday cousin is already dead (**D11**,
fold-unstable). Per the kill rule it is **DEFERRED, not opened.** A future operator who wants it
must FIRST re-clear a fresh Gate 1 on ≥20 days; this file is the record that today's static
version was refused on its pre-registered floor. Logged as **D14** in `dead_candidates.jsonl`.
