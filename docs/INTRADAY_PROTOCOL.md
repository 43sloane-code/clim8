# INTRADAY PROTOCOL — run it, quote it, add nothing

One page. Follow it literally. Every rule below is enforced in code; your job is to not
override the code with a story. (Shipped 2026-07-12 after the Karachi 32/33 miss —
ISSUES_2026-07-12_INTRADAY_ACCURACY.md is the case file.)

## The one command

```
PYTHONPATH=. python3 run.py "<City>" --lead 0
```

Compute the CITY-LOCAL date first. The `BUCKET CALL` block is the entire intraday read.
**Quote it verbatim. The vocabulary is machine-chosen — never upgrade a word.**

## The three surfaces (only one pays)

| Surface | What it is | How to treat it |
|---|---|---|
| `wunderground_daily_max` endpoint | The settlement record. Aggregates between-obs peaks the table never shows. | **The truth.** Headlined as "settling surface" with its value + n. |
| on-hour hourly table / daily chart | Lagging display. Sat at 90°F all afternoon while the endpoint banked 91°F. | Context only. Never rules. |
| v3 `cur_f` nowcast | Freshest read, ~10 min. Can LEAD the endpoint — or be stale. | A live signal, resolved by the tape (below). Never "banked", never dismissed. |

## The grades (what the machine may say, and why)

| Printed grade | Meaning | You may say |
|---|---|---|
| `INTRADAY LOCK (final)` | Post-sunset (real solar calc), OR peak window closed + endpoint stable across ≥2 reads + not rising + obs declining. | "locked / final". Only here. |
| `banked · LEADING — live coin-flip` | A cur_f sits above the settlement record. SUSTAINED (held across reads, refreshing v3 stamp) = corroborated; SINGLE-READ = wait one read. | Name BOTH buckets. Pick neither. Hold it until the endpoint resolves — no flip-flopping. |
| `FLOOR — PROVISIONAL (declining)` | Peak looks passed on the obs, endpoint not yet proven stable. | The floor + the backtest % as climatology. Not "locked". |
| `FLOOR — PROVISIONAL (holding)` | Day still at its max. Holding days climb (July ~37% London @16:00). | The floor. Nothing confident. |
| `remaining-rise modal: N° at P%` | A model extrapolation above the banked floor. | "model-grade extrapolation" — never "the live lean", never "upgraded". |

The machine's memory is `ledger/intraday_tape.jsonl` — every live run appends the
endpoint (value + n) and cur_f (+ its own timestamp). Endpoint motion ("still rising"
blocks any lock), lead sustainment (rule G4), and the measured lead-bank rate all come
from that tape, not from anyone's recollection of the last run.

## The five standing rules (each one is a 07-11/07-12 scar)

1. **The endpoint settles.** Quote it (value + n) before anything else. The table/chart lags.
2. **"Locked" is mechanical.** If the block doesn't print `LOCK (final)`, the word does not exist.
   A rising endpoint hard-blocks it — Karachi rose 90→91°F *after* the obs looked done.
3. **Boundary = both buckets, no conviction number.** A sustained lead often banks
   (Karachi 07-12→33, Jeddah 07-11→37, Jeddah 07-12 95→97°F); a frozen-stamp lead was the
   London 07-11 over-read. The tape tells you which one you have.
4. **Never override the machine toward the lagging feed, never flip-flop.** State the
   coin-flip once and hold it to resolution. The machine's cur_f-aware lean beat every
   hand-correction on 07-12.
5. **Vocabulary never outruns evidence.** Legitimacy is grade-match, not outcome. A right
   "locked" on coin-flip evidence is still a breach.

## What is DEAD — do not rebuild (ledger/dead_candidates.jsonl)

Conditioning the remaining-rise pmf on curve features is measured dead: dew-point (D07),
midday slope (D08), convective-cap/cloud (D11), running-max analogs (D13), state×cloud
terciles (D15). Day-ahead accuracy levers: 0/17, σ-ceiling physics. "Reading the chart's
shape" to sharpen the pmf is these, relitigated. The chart-reading that WORKS is the tape:
endpoint motion, lead sustainment, peak-close quantile, state×season raise-risk — all
shipped, all mechanical.

## Where things live

`weather_council/intraday_ceiling.py` (pmf + peak-close + banked/led split) ·
`intraday_grade.py` (grade + sunset + render) · `intraday_tape.py` (the memory) ·
`run.py _grade_for` (live wiring) · KATs: `tests/test_intraday_{grade,tape,ceiling}.py`,
`tests/test_live_floor.py`.
