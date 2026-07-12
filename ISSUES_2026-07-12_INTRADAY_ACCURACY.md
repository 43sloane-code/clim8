# Intraday Accuracy & Legitimacy Issues — 2026-07-12

Exhaustive, itemized record of every issue surfaced during the 2026-07-12 intraday
re-run session (Singapore, Karachi, Jeddah). Nothing omitted. Each entry states what
happened, the root cause, the evidence, the correct behavior, and the failure class.

The framing that governs all of it (per the operator's directive, and CLAUDE.md's ONE LAW):
**this is about accuracy and legitimacy, not P&L.** Legitimacy = the vocabulary grade of
every statement never exceeds the grade of the evidence behind it. A coin-flip honestly
labeled stays legitimate even when it lands the other way; a "locked" that lands right is
still illegitimate if the evidence never earned "locked." Outcome does not launder an
unearned grade.

---

## A. SINGAPORE (WSSS, settles whole-°F→°C round-half-up)

### A1 — Asserted a "32 lean, upgraded" from a MODEL headline without checking the record
- **What happened:** Morning call was 30 (cool-side). At 11:00 the intraday-ceiling lever
  showed final-max pmf 32°C 48% / 33°C 33% / 31°C 13% / 30°C 5%. I presented this as
  "the honest live lean is **32°C**, band 32–33, **upgraded** from this morning."
- **Root cause:** I took the machine's remaining-rise pmf headline and dressed it as a live
  read, without pulling the actual hourly tape first. The lever's input was "running max 30
  by 11:00"; it extrapolated ~+2°F on average. I treated an average extrapolation as an
  observation.
- **Evidence it was wrong:** The actual tape peaked at **86°F (30°C) at 10:00–11:00 then
  DROPPED to 82°F at 11:30** (convective cap). 31°C needs 87°F, 32°C needs 89°F — **neither
  was ever reached.** The 32 lean was contradicted by the live curve.
- **Operator caught it:** "it has not been recorded as 31 though."
- **Correct behavior:** 30 banked; 31 the live upside *only if* it recovers; 32 off the
  table. Look at the shape of the curve, not just the model's headline number.
- **Class:** model-grade number served as if observation-grade; failure to inspect the
  settling data before asserting.

### A2 — Used "upgraded" — a directional-confidence word — on an unformed peak
- **What happened:** Called the 32 lean an "upgrade" over the morning 30.
- **Root cause:** Manufactured a narrative of improving conviction where the record showed
  a *falling* temperature.
- **Correct behavior:** No conviction word on a pre-peak read that the tape doesn't support.
- **Class:** vocabulary above evidence grade.

### A3 — (handled correctly, logged for completeness) Second re-run reported "no change"
- On the next "re run," the ASOS feed had not advanced (stamp still 10:00) and I reported
  no change rather than inventing motion. This was the correct posture and is recorded as
  the counter-example of what A1 should have been.

---

## B. KARACHI (OPKC, settles whole-°C round-half-up) — the primary miss: called 32, SETTLED 33

### B1 — Called "32°C effectively LOCKED" on coin-flip evidence — the core legitimacy breach
- **What happened:** At 13:59 PKT I wrote "**32 locks … effectively locked**."
- **Root cause:** Applied an observation/physics-grade finality word to a boundary read that
  was still live. The peak was not provably in and the settling endpoint was still moving.
- **Evidence it was wrong:** It **settled 33.** The `wunderground_daily_max` endpoint went
  **90°F/32 (n=27) at 13:59 → 91°F/33 (n=34) by 16:40** — it was still growing when I said
  "locked."
- **Correct behavior:** "32 banked, 33 live via the sustained cur_f — NOT locked; the peak
  window is still open." Never say "locked" until the peak has passed AND the endpoint has
  stopped moving.
- **Class:** vocabulary above evidence grade (the intolerable one). A single false "locked"
  makes every legitimate "locked" untrustworthy.

### B2 — Trusted the on-hour hourly TABLE over the settling daily-max ENDPOINT
- **What happened:** I read `_wu_hourly_raw` (flat 90°F all afternoon: 12:00–15:30 every ob
  90°F) and the operator's "daily chart shows 32," and concluded the settling value was 32.
- **Root cause:** The market settles on `wunderground_daily_max`, which **aggregates
  between-obs / special obs** and can sit a full °F above the tidy on-hour table. The table
  never displayed the 91°F between-obs spike; the endpoint captured it.
- **Evidence:** Final daily-max = 91.0°F (32.78°C) → 33, n=34, while the on-hour obs I kept
  pulling were all 90°F. The endpoint and the table disagreed by 1°F — and the endpoint is
  what pays.
- **Correct behavior:** Read `wunderground_daily_max` as the settling surface; treat the
  on-hour table as a lagging, incomplete view.
- **Class:** mechanism read backwards (this is literally the documented phantom-cap
  aggregation, misinterpreted).

### B3 — Dismissed a SUSTAINED, MARKET-CONFIRMED cur_f as a "non-settling artifact"
- **What happened:** v3 cur_f held **91°F across 13:17 → 13:30 → 13:42 → 13:55 → 16:37**, and
  the market modal was 33. I labeled the 91°F a "non-settling artifact" and a "phantom."
- **Root cause:** I demanded the on-hour table confirm the lead before trusting it. But a
  sustained + market-agreeing cur_f is exactly the corroboration threshold; the table lags
  the endpoint and is the wrong confirmer.
- **Evidence:** The 91°F was real — the endpoint banked it and it settled 33. cur_f was
  leading the whole time.
- **Correct behavior:** Sustained cur_f + market agreement = the higher bucket is LIVE; do
  not down-call to the lagging table. (This is the Jeddah 07-11 lesson repeated — cur_f 98°F
  led correctly there too.)
- **Class:** dismissing a corroborated divergent signal.

### B4 — Reactive flip-flopping across the Karachi thread (≥3 reversals)
- **Sequence:** "33 banked" (machine) → "32 real floor, 33 uncorroborated" → "**flipping my
  lean to 33**" (on the v3 91°F + market) → "**back to 32**" (on the operator's daily-chart
  32) → "**32 locks**." Final truth: 33.
- **Root cause:** Swinging on every feed twitch instead of picking one disciplined framing
  (boundary coin-flip, both buckets, no conviction) and holding it.
- **Correct behavior:** State it once as a 32/33 coin-flip with the sustained-cur_f lead
  noted, and hold that until the endpoint provably resolves.
- **Class:** "stop swinging reactively" — the exact failure this system's memory already
  names.

### B5 — Told the operator "the daily chart wins" over the v3 feed
- **What happened:** When the operator said "it shows 32 still on the live daily chart," I
  asserted the daily chart is the settling surface and "wins" over the v3 91°F.
- **Root cause:** The daily *chart* the operator saw was the lagging on-hour display, not the
  daily-max endpoint. I conflated the two and gave a confident wrong ruling.
- **Evidence:** The endpoint later banked 91°F/33; the chart the operator saw was simply
  behind.
- **Correct behavior:** "The on-hour chart shows 32, but the settling daily-max endpoint can
  still catch a between-obs 91°F this afternoon — it's not resolved."
- **Class:** confident assertion on a misidentified surface.

### B6 — Assumed the ~13:00 climatological peak center meant "peak is passing"
- **What happened:** At 13:59 I reasoned "peak ~13:00 ±0.8h, so it's passing; 33 needs an
  against-the-cycle climb."
- **Root cause:** Treated the climatological peak-hour center as the actual peak for today.
  Karachi's real peak came mid/late-afternoon and hit 91°F after I stopped watching.
- **Correct behavior:** The peak is "in" only when the endpoint stops rising, not when the
  clock passes the climatological center.
- **Class:** premature peak-is-in assumption feeding a premature lock.

---

## C. JEDDAH (OEJN, settles whole-°C round-half-up) — error propagated live from Karachi

### C1 — Over-rode a correct 36 lean DOWN to "edge to 35" using the wrong Karachi conclusion
- **What happened:** At 13:30 I leaned 36 (matching machine 77%, market 36). At 14:31, with
  the record flat at 95°F/35 and cur_f still 96°F, I **reversed to "edge to 35,"** explicitly
  invoking "the Karachi pattern (cur_f is a non-settling artifact)."
- **Root cause:** I built the Jeddah down-call on the Karachi conclusion that was itself
  wrong (B2/B3). The "Karachi pattern" I cited — cur_f doesn't bank — is the opposite of what
  actually happened (Karachi's cur_f DID bank to 33).
- **Evidence:** Jeddah's setup at 14:40 was identical to Karachi's live state — endpoint
  95°F/35, sustained cur_f 96°F (36), afternoon peak-tail (15–16h) still ahead — i.e. exactly
  the condition under which Karachi's endpoint later banked the higher bucket.
- **Correct behavior:** Hold 36 (sustained cur_f + market + the machine's cur_f-aware lean);
  35 is the floor, not the lean.
- **Class:** propagating a wrong conclusion; overriding the machine toward the lagging table.

### C2 — Flip-flopped Jeddah 36 → 35 → 36
- **What happened:** Leaned 36, reversed to 35, then reversed back to 36 after Karachi settled.
- **Root cause:** Same reactive-swinging failure as B4, one city over.
- **Correct behavior:** One disciplined framing held across the reads.
- **Class:** reactive flip-flopping.

### C3 — Floated 37 as an "upside" partly off max24=98°F without first flagging the carryover
- **What happened:** Cited v3 max24 = 98°F as evidence for a 37 upside; only afterward noted
  it may be a 24h-rolling carryover of yesterday's 37.
- **Root cause:** Nearly over-read a rolling register again (the same register-over-read
  class that produced phantom 39 historically).
- **Correct behavior:** max24 is a 24h rolling max that can carry yesterday; 37 needs today's
  obs to reach 98°F on the tail. Caveat the register before citing it.
- **Class:** register over-read (caught, but late).

---

## D. CROSS-CUTTING / META FAILURES (the class beneath the specifics)

### D1 — Manufacturing confidence on sub-°F boundary reads
- Repeatedly stated single-bucket picks with conviction (leans dressed as calls, "locked")
  where the evidence was a genuine boundary coin-flip. Boundary reads are feed-limited, not
  forecast-limited; conviction there is unearned.

### D2 — Reading headlines/tables instead of the settling surface
- A1 (Singapore lever headline), B2/B5 (on-hour table), C1 (invoking the wrong pattern) all
  share one root: I did not go to the `wunderground_daily_max` endpoint — the thing that
  actually pays — before speaking.

### D3 — Overriding the machine's cur_f-aware lean toward the lagging feed
- On Karachi the machine's fused lean (which included cur_f 91°F) was closer to the 33
  outcome than my hand-correction to 32. My manual down-calls to the lagging table are a
  primary error source.

### D4 — Reactive flip-flopping instead of one held framing
- B4 and C2. Every feed twitch produced a reversal. The correct posture is: state the
  boundary as a coin-flip with the lead noted, once, and hold it until the endpoint resolves.

### D5 — Letting a labeling fix become a license to DISMISS the lead
- The mid-session labeling fix (E1) correctly stops calling a cur_f lead "banked." But I then
  used "not banked" to treat the lead as an *artifact to ignore* — the opposite error. "LEADING,
  not banked" means "live signal, not yet a floor," NOT "false, discard." The fix must not be
  read as a dismissal license.

---

## E. VOCABULARY-GRADE BREACHES (itemized — the legitimacy core)

Every word must match the evidence grade. Breaches this session:

- **E-a — "effectively locked" (Karachi 32):** finality grade on coin-flip evidence. (B1)
- **E-b — "upgraded / the honest live lean is 32" (Singapore):** conviction grade on an
  extrapolation the tape contradicted. (A1/A2)
- **E-c — "flipping my lean to 33" then "back to 32" (Karachi):** stated as decisions, not as
  what they were — an unresolved coin-flip. (B4)
- **E-d — "non-settling artifact / phantom" (Karachi cur_f 91°F):** a dismissal grade on a
  signal that was in fact leading the settlement. (B3)
- **E-e — "the daily chart wins" (Karachi):** a ruling grade on a misidentified surface. (B5)
- **E-f — "edge to 35" (Jeddah):** a lean grade derived from the wrong Karachi conclusion. (C1)

**Grade definitions that must be honored going forward:**
- **banked** = the settling endpoint (`wunderground_daily_max`) has RECORDED it. Nothing less.
- **locked / final** = peak provably passed AND endpoint stopped moving (n stable across
  reads) AND declining/post-sunset. Nothing less.
- **coin-flip** = at a boundary, name BOTH buckets, no conviction number — even with a lean.
- The label never borrows a grade from hope, the machine's headline, or a wish to sound decisive.

---

## F. CODE / MECHANISM ISSUES

### F1 — `_fuse_live_floor` / run.py fused v3 cur_f into a "banked" floor (the original bug)
- The machine headlined "33°C banked" (Karachi) and "36°C banked"-style off a live cur_f that
  the settling record had not confirmed — dressing a lead as observation-grade.
- **Status:** FIX LANDED mid-session — `weather_council/intraday_ceiling.py:81`
  ("BANKED-vs-LEADING split (2026-07-12 Karachi vocabulary fix)") and `run.py:1053`. It now
  emits `banked_bucket` vs `led_bucket` with an `uncorroborated_lead` flag, and renders a
  separate "LEADING (uncorroborated live read) — NOT yet banked" line (verified live on the
  Jeddah 13:27 run: "35°C banked / 36°C LEADING").
- **Chip:** spawn_task "Fix intraday 'banked' label over-reading uncorroborated cur_f"
  (task_b342858a).

### F2 — The chip/fix framing carries a DISMISSAL bias that Karachi disproved
- The chip described the cur_f lead as "the London 07-11 over-read failure mode" and framed
  it as something to discount. Karachi 07-12 shows the lead is *often correct* (it banked to
  33). The label "LEADING, not banked" is right; framing the lead as a likely over-read is
  wrong. **Open item:** the fix's copy/intent should present LEADING as a *live, unresolved*
  signal — not a probable artifact — so it is neither dressed as banked (F1) nor dismissed (D5).

### F3 — Settling surface vs display surface not distinguished in my workflow
- `wunderground_daily_max` (settles; aggregates between-obs peaks) vs `_wu_hourly_raw` (on-hour
  display; lags, hides spikes) vs `wunderground_current_v3` (cur_f nowcast + max24 rolling,
  ~10min latency). I repeatedly cited the display surface as if it settled. **Open item:** any
  intraday read must quote the daily-max endpoint value + n, and treat the rest as context.

---

## G. STANDING RULES THAT REPLACE THESE FAILURES (the corrective, now in memory)

Encoded in memory `feedback_market_leads_lagging_wu_endpoint.md` (updated this session):

1. **Settlement = the `wunderground_daily_max` ENDPOINT** (aggregates between-obs/special
   obs), never the on-hour table. The endpoint can sit a full °F above the table.
2. **Never say "locked"** until (a) the peak window has fully passed AND (b) the endpoint's
   obs-count has stopped growing across two reads. A holding plateau on the table ≠ peak in.
3. **On a boundary, name BOTH buckets, no conviction number** — even with a lean.
4. **cur_f leads when SUSTAINED + market agrees** — that is enough; do NOT also require the
   lagging table to confirm. Stale/frozen-timestamp cur_f above a moving-but-not-rising record
   = over-read (London 07-11). Sustained-and-refreshing + market = trust the lead
   (Jeddah 07-11 → 37; Karachi 07-12 → 33).
5. **Do not override the machine's cur_f-aware lean toward the lagging table; do not
   flip-flop** — pick the disciplined framing once and hold it to resolution.
6. **The label never outruns the evidence grade** (§E). Legitimacy is measured by grade-match,
   not by outcome.

---

## H. OPEN ITEMS — ALL CLOSED 2026-07-12 pm (gate green, 642 tests; live-verified on Singapore)

- **H1 CLOSED** — `weather_council/intraday_grade.py`: a lead renders as a LIVE COIN-FLIP —
  SUSTAINED (held across reads on a refreshing v3 stamp, rule G4 mechanical via the tape) =
  "corroborated, NOT a probable over-read"; SINGLE-READ = "wait one read, do not dismiss".
  The old dismissal copy ("treat it as a lean", "the London over-read") is deleted from run.py.
- **H2 CLOSED** — `grade_lines` headlines every intraday block with the settling surface:
  "WU daily-max endpoint N°F, n=K obs (this record pays; table/nowcast are context only)".
- **H3 CLOSED** — "locked"/"final" is mechanical: post-sunset (NOAA solar calc from the
  city's own lat/lon — matches the certified ~19:10 SGT clock) OR peak window closed (the
  archive's own leak-free peaked-by-q0.95 hour, computed from the SAME history as the rise
  pmf) AND endpoint stable across ≥2 tape reads AND not rising AND obs declining. A rising
  endpoint hard-blocks it — the exact Karachi state. Cross-run memory:
  `ledger/intraday_tape.jsonl` (weather_council/intraday_tape.py).
- **H4 RESOLVED (partially — day open at read)** — endpoint fetch 2026-07-12 ~12:45Z:
  Jeddah 07-11 settled 99°F → **37** (the sustained cur_f lead was RIGHT again); 07-12 had
  already climbed 95→**97°F = 36.1°C, n=16** — the erroneous "edge to 35" detour is
  falsified on the record; the corrected 36 lean is the banked floor, tail open toward 37.
- **F2 CLOSED** — same shipment as H1: the coin-flip line now quotes the tape's MEASURED
  lead-bank rate when it exists, anecdotes only until then.

Protocol distilled for every future session: docs/INTRADAY_PROTOCOL.md.
