# WP-6 addendum — `bucket_contract.compact_buckets` non-contiguous interior drops mass (Class D)

*Served-number campaign, WP-6 (F6). Class-D deterministic correctness; cert = exhibit-the-delta + KAT.
Market/contract-side, touches only `bucket_contract.py` (zero common files with WP-3/WP-4).*

## The defect (exhibit — the audit under-specified it)
CODE_AUDIT §F called F6 an "all-tail double-label" edge case ("pmf still sums to 1"). Reading the code,
the genuine defect is worse: `keys = sorted(k for k,p in probs if p >= tail_floor)` keeps only the
ABOVE-floor interior buckets, then emits a cell per key and folds only `k<lo` / `k>hi` into the tails.
A sub-floor interior bucket **strictly between lo and hi is neither emitted nor folded — its mass is
DROPPED**, so the compacted pmf does NOT sum to 1. Exhibited: `compact_buckets({10:0.5, 11:0.003,
12:0.497})` → `{'10':0.5,'12':0.497}`, sum **0.997** (the `11` mass vanishes). This is a served
contract pmf (`daily_contract` line 215). This addendum records the correction of the frozen §3 WP-6
premise (stop-and-report: the partition invariant the plan wanted is exactly what surfaces/fixes it).

## Fix design (frozen)
1. **Contiguous interior.** Interior = the full integer range `[lo, hi]` (lo/hi = min/max above-floor
   key), emitting `probs.get(k, 0.0)` for every k — so no interior mass is dropped and the cells
   `<=lo-1 | {lo..hi} | >=hi+1` partition the integer line with each integer claimed exactly once.
2. **Degenerate all-tail case explicit.** No above-floor key → keep the mode as the single interior
   cell; the below/above tails then partition the rest. Mass-preserving by construction.
3. **Permanent runtime partition invariant.** `assert sum(out) == sum(probs)` (± eps) — cheap, holds by
   construction (the cells are disjoint by structure: `≤lo-1`, individual `k`, `≥hi+1` cannot overlap),
   guards every future edit. (Mass-preservation is the operational form of "each integer claimed once"
   given the non-overlapping cell structure.)

## KATs
- **interior-drop fixed:** `{10:0.5, 11:0.003, 12:0.497}` → includes `"11"`, sums to 1.0. RED pre-fix.
- **degenerate all-tail:** all p < tail_floor → mode kept, cells partition, sum 1.0, no double-label.
- **parity:** a normal unimodal pmf → cells + total unchanged (the fix is a no-op where the interior is
  already contiguous).
- **property sweep:** for a battery of pmfs, `sum(compact_buckets(p)) == sum(p)` (mass preserved).

*Repo-gate note: KATs confirmed RED pre-fix, committed GREEN with the fix.*

## HELD — historical exhibit
Replay `compact_buckets` over stored contract pmfs; count how many emitted pmfs change (expected tiny —
only pmfs with a sub-floor interior gap, i.e. dithered/bimodal). Held with the other WP measurement
halves; the fix stops NEW mass-drop now.
