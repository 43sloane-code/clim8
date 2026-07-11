# WP-5 addendum — `market.py` unparseable bucket dilutes de-vig (Class D)

*Served-number campaign, WP-5 (F5). Class-D deterministic correctness; cert = exhibit-the-delta + KAT.
Market-side, independent of the truth-side WPs (touches only `market.py` — verified zero common files
with WP-3/WP-4, so §4 permits it in parallel).*

## The defect (exhibit)
`_parse_bucket` returns a `MarketBucket` even when `_bucket_edges(label)` yields `(None, None)` (a
non-empty but un-parseable outcome label). That bucket survives into `parsed` (`_parse_event` line 405),
`_order` maps it to `float("-inf")` so it sorts to LADDER INDEX 0, and — the real harm —
`WeatherMarket.implied_probabilities` sums `yes_price` over ALL `self.buckets`, so the junk bucket's
price enters the **de-vig denominator** `Σ yes`, distorting EVERY real bucket's implied probability.

## Fix design (frozen)
1. **Quarantine.** A bucket is ladder-valid only if `lo is not None OR hi is not None`. A `(None,None)`
   bucket is EXCLUDED from `self.buckets` (so it never enters the ladder nor the de-vig denominator).
2. **Record it.** `WeatherMarket` gains `unparsed_outcomes: tuple = ()` — the raw labels of the
   quarantined buckets, so schema drift is surfaced (evidence), not silently dropped.
3. Never a synthetic `(None,None) → -inf` sentinel in a probability computation (the quarantine happens
   before the sort, so `_order`'s `-inf` branch is now unreachable — left as a defensive fallback).
4. **Watchdog AMBER (deferred, documented):** the plan calls for watchdog AMBER when unparsed>0 on an
   active market. Wiring that touches `watchdog_core.py`, which WP-3's AMBER path also owns — to keep
   ONE owner of that file (§2.3) the AMBER duty is deferred to the watchdog-owning WP; WP-5 makes the
   evidence available (`unparsed_outcomes`). This is the one honest scope-trim, recorded here.

## KATs
- **one-malformed:** an event with a junk-label market → it is NOT in `buckets`, IS in
  `unparsed_outcomes`, and `implied_probabilities` renormalizes over the REAL buckets only (the junk
  price is not in the denominator). RED pre-fix (it was in the denominator + no field).
- **parity:** an all-parseable event → `buckets` and `implied_probabilities` byte-identical to before,
  `unparsed_outcomes == ()`.

*Repo-gate note: KATs confirmed RED on pre-fix code, committed GREEN with the fix.*
