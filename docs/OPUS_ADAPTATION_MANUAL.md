# ADAPTATION MANUAL — everything that changed 2026-07-12 (read once, operate simply)

*For the next session (any model). Eight commits, `4c3fc8c → 6d2aec2`. CLAUDE.md is still
the constitution; this manual is the delta — what is NEW, how to RUN it, and what got
KILLED so you never rebuild it. Written to be followed literally.*

---

## 0. THE ONE PENDING ACTION (user-side, blocks two mechanisms)

The tape LaunchAgent is written but **NOT loaded** (verified absent from
`~/Library/LaunchAgents` at writing). Until the user runs this, London gets no
peak-window tape reads AND no in-window lock-certification rows:

```
cp "tools/com.weatherverdict.tape.plist" ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.weatherverdict.tape.plist
```

If a day passes and `ledger/intraday_tape.jsonl` has no London row near 15:30 London
time, remind the user. Do NOT run launchctl yourself (classifier-denied; user-side only).

---

## 1. THE INTRADAY TAPE + GRADE ENGINE (the big change — why and how)

**Why it exists:** the 2026-07-12 Karachi miss ("32 effectively locked", settled 33).
Root cause was NOT the forecast — the machine's lean was right. It was memoryless runs +
judgment vocabulary: nobody tracked that the settlement endpoint rose 90→91°F between
runs, and "locked" had no mechanical gate. Case file: ISSUES_2026-07-12_INTRADAY_ACCURACY.md.

**What exists now (3 pieces):**

| Piece | File | What it does |
|---|---|---|
| Tape | `weather_council/intraday_tape.py` → `ledger/intraday_tape.jsonl` | Every live run appends one read of the settlement surface: daily-max endpoint (value + n_obs) and v3 cur_f (+ its own timestamp). Pure functions over the row SEQUENCE answer: endpoint rising? stable? cur_f sustained? measured lead-bank rate. |
| Grade | `weather_council/intraday_grade.py` | Classifies each read into ONE grade; chooses ALL intraday vocabulary. Real NOAA sunset (lat/lon), archive-derived peak-close hour, grain-aware (SF renders °F). |
| Wiring | `run.py _grade_for` + grade-driven `_bucket_call_lines` | Auto on every `--lead 0` run. JSON export certifies the grade (auditable after the fact). |

**How to operate it — three rules, no judgment:**

1. Run `PYTHONPATH=. python3 run.py "<City>" --lead 0`. **Quote the BUCKET CALL block
   verbatim. Never upgrade a word.** "LOCK/final" prints iff `Grade.may_say_locked` —
   post-sunset, or peak-window-closed + endpoint stable across ≥2 reads + not rising +
   obs declining. If it doesn't print LOCK, the word "locked" does not exist.
2. A cur_f lead renders as a **live coin-flip** — SUSTAINED (held across reads on a
   refreshing v3 stamp = corroborated, often banks) or SINGLE-READ (wait one read).
   Name both buckets. Pick neither. Never dismiss the lead, never call it banked.
3. The **settling surface headlines every block**: the WU daily-max endpoint (value+n).
   The on-hour table and any nowcast are context. The endpoint is what pays.

Full one-pager: `docs/INTRADAY_PROTOCOL.md`. Light reader (no council, no market):
`PYTHONPATH=. python3 tools/tape_logger.py` — Singapore + London only (Manila is
deliberately excluded: no `_LIVE_REGISTER` consult, and adding it touches a served
number, out of scope by user directive).

---

## 2. PER-CITY LOCK LEDGER (London can now certify)

`tools/lock_logger.py` was Singapore-hardwired; now `CITIES` = Singapore + London.
Same file (`ledger/singapore_lock.jsonl` — historical name), rows carry `city`
(legacy rows migrate to "Singapore" on load). What to know:

- **Singapore's frozen bar is byte-unchanged** (hours 12–18, n≥20, −10pp). KAT'd.
- **London**: cert hours 13–18 local, same bar; settles on the **WU EGLC record**
  (whole-°F max → whole-°C round-half-up). The prereg's old "IEM" line was SUPERSEDED
  by the 2026-07-07 "wunderground only" directive — documented in
  `ledger/preregistered/london_lock_instrumentation.md`. Never revert London settle to IEM.
- London's only in-window scheduled runner is the tape job (15:30) — see §0.
- Watchdog Duty 2 now guards BOTH cities' crossover (baseline has WSSS + EGLC rows;
  a missing EGLC fold is a RED by design).
- **Known pre-existing RED, deliberately not silenced:** Duty 2 was already red on
  WSSS@14:00 (replay 75.0% vs pinned 79.3%) BEFORE this work. Do not re-pin to make it
  green; if it persists across runs it needs its own adjudication (window-roll noise vs
  real regression).

---

## 3. WHAT GOT KILLED TODAY (never rebuild; cite the ID and stop)

| Dead | ID | One-line verdict |
|---|---|---|
| AIFS as 10th member (proposed in-conversation) | **D02** (pre-existing) | Already dead: "noise, 3 attempts"; forecast members 0/6. Lesson saved to memory: run `improvement_analyzer --propose "<candidate's own keywords>"` on every NAMED candidate BEFORE recommending it — conversational proposals count. |
| SF native-°F headline pmf | **D19** (new) | Pre-registered, probed once on 10y KSFO (3,628 days): the naive °F pmf LOSES to the served °C pmf read as a °F answer (log score fails both halves; modal hit not sign-stable). At day-ahead σ (~4°F) with n≈160 residuals, °F grain over-fits bin noise; °C bucketing is an accidental regularizer. **The °C headline stands on evidence. For SF quote the SETTLEMENT-section °F figures, never the headline.** A future °F headline = smoothed-density mechanism, own prereg, must beat the °C-split baseline. |

Standing exchange rule (encoded in `docs/NWP_LITERATURE_MAP.md`): a published MAE/CRPS
gain transfers here ONLY if it survives at the settlement-bucket grain under the frozen
gate. 19 dead candidates say the exchange rate is usually zero. Day-ahead levers: spend
nothing (0/19; market ties the council 44%=44%). Accuracy is manufactured by
observations (intraday), imported upstream physics, and correct bookkeeping — everything
else protects it or pretends to it.

---

## 4. DAILY OPERATIONS QUICKREF (unchanged laws, new instruments)

```
Verdict:        PYTHONPATH=. python3 run.py "<City>" --lead 0     # city-LOCAL date
Light tape:     PYTHONPATH=. python3 tools/tape_logger.py         # SG + London
Lock ledger:    PYTHONPATH=. python3 tools/lock_logger.py         # all cities, idempotent
Machine status: PYTHONPATH=. python3 tools/eval_harness.py        # trust its ranking
Before ANY "improve X":  python3 tools/improvement_analyzer.py --propose "<X's own keywords>"
                         + grep -i "<keywords>" ledger/dead_candidates.jsonl
Full gate:      make check          # 650+ tests; pre-commit runs it
Commit flow:    branch → commit → checkout main → merge --ff-only → push (never bundle
                structural with prompt/text changes)
```

**Clocks (waiting IS the work; nothing accelerates them except not missing days):**
TWC 9th-member 25/40 (~1 week) · PoP regime split 4/15 dry days · p2b 8/60 ·
Singapore lock bins accruing · London lock n=1 (first row 2026-07-12) ·
tape lead-bank ledger n=3 days · Jeddah 07-12 settle to confirm (was 97°F/36 banked,
tail open toward 37).

**The vocabulary law (the reason today happened):** the grade is machine-chosen — banked
= on the settlement record; locked = mechanical gate only; boundary = both buckets, no
conviction; a fix is a hypothesis until n at the frozen bar. Legitimacy is grade-match,
not outcome.

---

## 5. FILE MAP OF EVERYTHING NEW TODAY

```
weather_council/intraday_tape.py        the memory (+ KAT tests/test_intraday_tape.py)
weather_council/intraday_grade.py       the vocabulary gate (+ KAT tests/test_intraday_grade.py)
weather_council/intraday_ceiling.py     +endpoint n, v3 stamp, peak-close hour fields
run.py                                  _grade_for + grade-driven BUCKET CALL render
tools/tape_logger.py                    light SG+London reader; also runs lock_logger
tools/com.weatherverdict.tape.plist     15:30 + 21:45 (NOT YET LOADED — §0)
tools/lock_logger.py                    per-city (CITIES config; migration; KATs extended)
tools/accumulate.py                     two-city crossover emit
reports/crossover_baseline.json         +EGLC rows (documented breakpoint)
reports/backtest_sf_native_f.py         the D19 probe (do not re-run as a fresh attempt)
ledger/preregistered/sf_native_f_headline.md      FAILED stamp + numbers
ledger/preregistered/london_lock_instrumentation.md  EXECUTED stamp + supersession
docs/INTRADAY_PROTOCOL.md               the one-page intraday runbook
docs/NWP_LITERATURE_MAP.md              textbook→repo dispositions + citations
docs/OPUS_ADAPTATION_MANUAL.md          this file
```

Adapt by doing, in order: check §0, run one `--lead 0` verdict and read its BUCKET CALL
against §1's three rules, run `eval_harness`, and touch nothing the dead ledger names.
