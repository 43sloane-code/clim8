# weather-verdict

A measured, gate-disciplined **temperature-settlement prediction system**. It forecasts
daily high/low temperatures for a fixed set of cities and prices them against
prediction-market buckets (Kalshi-primary mandate), serving every number from a
ledger — never a blended statistic, never a story, never from memory.

The operating principle ("The One Law"): *serve the number the evidence has earned
for this case, in the vocabulary grade the evidence has earned.* Every accuracy
claim in the repo is backed by a pre-registered, leak-free walk-forward probe;
ideas that fail the gate are killed and recorded in a dead-candidates ledger
(`D01`–`D29` and counting — roughly 4 in 100 candidate edges survive honest testing).

- **Project start:** 2026-06-06 (initial commit `b5c2005`)
- **Active development:** 2026-06-06 → 2026-07-29 — 251 commits across 37 active days
- **Full iteration history:** see [CHANGELOG.md](CHANGELOG.md) (every commit, grouped by date)
- **Full file inventory:** see [TREEVIEW.md](TREEVIEW.md)

## What it does

- **Day-ahead verdicts:** per-city probability mass functions (pmf) over settlement
  buckets, with an honest BAND — the council does *not* beat the market day-ahead
  (measured 40% vs 43%), so day-ahead output is spoken as a coin-flip near boundaries.
- **Intraday lock engine:** the only certified conviction lever. A running-max ratchet
  ("banked ≥ N"), a 2-consecutive-read decline rule, post-sunset physics finality,
  state × season × hour raise-risk (n≥30 cells), and machine-chosen vocabulary
  (`LOCK/final` prints only when the evidence earns it).
- **Settlement truth per city:** each city settles against its own oracle — never assumed:
  - **Singapore (WSSS)** — WU oracle, whole-°F → °C round-half-up. Peak ~13:00 SGT; 16:00 ≈ 97.5% certified.
  - **Manila (RPLL)** — WU oracle (serving only; improvement out of scope by user directive).
  - **London (EGLC)** — SETTLE on Wunderground, BACKTEST on 10y IEM METAR (the two are
    deliberately split; WU catches between-obs peaks the whole-°C METAR rounds away).
  - **San Francisco (KSFO)** — settles on the **NWS CLI product** (Kalshi's oracle);
    IEM METAR is the primary cross-reference (10y archive + 6-hourly T-groups), WU is
    secondary only and reads 1–2°F below CLI (the logged "CLI seam").
  - **Hong Kong (HKO)** — removed from the basket (0.1°C FLOOR grain).
- **The CLI-seam guard:** on CLI-primary stations the obs-scale pmf sits ~1.3°F cold
  vs the settle; near a live Kalshi strike line before the 18–00Z group prints, the
  machine names both sides and serves neither alone (book-aware strike lines from the
  live Kalshi ladder, static 2°F grid as fallback).
- **Daily automation spine:** launchd-driven `tools/accumulate.py` — snapshots, TWC/PoP/
  p2b/lock ledgers, watchdog chain (crossover → truth config → compare), settlement
  audit, db snapshot. Verdict plists fire morning/midday/afternoon/evening.
- **Governance machinery:** pre-registered probes (`ledger/preregistered/*.md`),
  a dead-candidates ledger (`ledger/dead_candidates.jsonl`), an improvement analyzer
  that must be consulted before any "improve/optimize" task, an eval harness that
  ranks the NECESSARY NEXT work, and a 420-test pre-commit gate.

## Quickstart

Everything runs `PYTHONPATH=.` from the repo root, with the system `python3`.

```bash
# Full-stack verdict (compute the CITY-LOCAL date first; --lead is relative to it)
python3 run.py "San Francisco" --lead 0 --intraday --market
python3 run.py "London" --lead 1

# Fine-grain settlement read (00Z / 6-hourly T-groups + pattern floor)
python3 tools/finegrain_read.py --station KSFO --date <sf-local-date> \
    --tz America/Los_Angeles --pattern-hour <H>

# The machine's own status: liveness, vocabulary guard, ranked next work
python3 tools/eval_harness.py

# Before ANY improvement task
python3 tools/improvement_analyzer.py --propose "<the task>"
grep -il "<lever keywords>" ledger/dead_candidates.jsonl   # D01–D30

# Tests (also run by the pre-commit hook)
python3 -m pytest tests/
```

## Repository layout (top level)

| Path | Role |
|------|------|
| `run.py` | Verdict render — settlement reference, holding-cap, banked/final lines, CLI-seam guard |
| `weather_council/` | Core package: sources (WU/NWS-CLI/IEM feeds), council, storage, intraday ceiling/grade/tape, cli_seam |
| `tools/` | Daily spine (`accumulate.py`), watchdog, eval harness, fine-grain read, loggers, backfill/dataset builders, launchd plists |
| `ledger/` | The memory that binds: preregistered probes, dead candidates, intraday tape, Kalshi snapshots, lock/PoP/p2b ledgers |
| `data/` | 10y hourly IEM archives per station + WU-grain series + training tables |
| `reports/` | Backtest scripts, scorecards, stream CSVs (208 entries) |
| `tests/` | 99 test files (~420 tests) including KATs ("keep-alive tests") pinning shipped behavior |
| `docs/` | Operator manuals: OPUS_ADAPTATION_MANUAL, INTRADAY_PROTOCOL, code audit, driver audit |
| `verdicts.db` | SQLite verdict/accuracy store (~14 MB) |
| `server.py` / `index.html` | Local dashboard |
| `CLAUDE.md` | The operator manual (auto-loaded; the runbook, city config, hard rules, open clocks) |
| `SESSION_STATE.md`, `HANDOFF.md`, `ROADMAP.md`, `FINDINGS.md` | Session/handoff state, roadmap, and the findings record |

Full annotated inventory: [TREEVIEW.md](TREEVIEW.md).

## Hard rules (summary — the full law is in CLAUDE.md)

1. No served-number change without a pre-registration frozen **before** scoring →
   leak-free walk-forward → sign-stable on both chronological halves (and both cities
   if shared) → ship + KAT + stamp the prereg CERTIFIED. Fail → dead-ledger entry,
   one attempt. The prereg must name the **driver** (causal mechanism), its kill
   condition, and its regime.
2. Labeling/honesty fixes ship without the gate; anything touching a served
   probability or pick does not.
3. A fix is a hypothesis — "improved" is claimed only from n at the frozen bar,
   never from comparing two live runs.
4. Measured-dead anti-directives (do not spend effort): day-ahead accuracy levers
   (0/17, σ-ceiling physics), consensus overrides, retro-computed lock rows,
   LLM-as-signal, latency arb, Windy-vision, paid-API-for-accuracy.

## Commit flow

`branch → add → commit (pre-commit runs the full test gate) → checkout main →
merge --ff-only → push origin main → branch -d`. Never bundle a structural change
with a prompt/text change.

## Companion copies

`../weather-verdict copy/` is a frozen snapshot of this repo as of 2026-07-19
(227 commits, a strict subset of this history — no unique commits). This directory
is the live, authoritative repo.
