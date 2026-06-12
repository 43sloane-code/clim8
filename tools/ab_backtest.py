"""Frozen-data A/B harness for council-config changes.

WHY THIS EXISTS
---------------
A single live `run.py` reports held-out CRPS/MAE computed from the Open-Meteo
historical-forecast feed. That feed REVISES the held-out window between calls, so
the same config drifts ~0.1 CRPS run-to-run — about 10x larger than the true effect
of adding/removing one council member. Comparing an "after" run against a "before"
run captured minutes-to-hours earlier therefore measures data drift, not the change.
(That mistake shipped an unjustified ECMWF AIFS member and had to be reverted.)

This harness removes the drift: it RECORDS every upstream HTTP response once into a
cache, then REPLAYS that frozen snapshot for every config. Both arms of the A/B see
byte-identical data, so the difference in held-out CRPS/MAE is attributable to the
config change alone. A determinism self-check (run arm A twice) proves the pipeline
is deterministic under frozen data before any delta is believed.

USAGE
-----
  PYTHONPATH=. python3 tools/ab_backtest.py "Hong Kong" --window 60
  PYTHONPATH=. python3 tools/ab_backtest.py "London" --window 90 --lead 0

The config under test is hard-coded below as `ARM_B = base panel + ECMWF AIFS`;
edit `arm_members()` to A/B any other member/skill change. The cache is keyed by
(method, url, params) and persisted under reports/ so a frozen snapshot can be reused
across invocations for byte-reproducible comparisons.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import pickle
import sys
from pathlib import Path

from weather_council import agents
from weather_council.agents import MemberSpec
from weather_council.council import Council
from weather_council.sources import Sources, place_today

# The candidate change under test: the data-driven ECMWF AIFS model as a 9th member.
AIFS = MemberSpec("aifs", "ecmwf_aifs025_single", "ECMWF AIFS (AI)", "EU")

_BASE_PANEL = list(agents.COUNCIL)  # the live 8-member panel, captured at import


def arm_members(arm: str) -> list[MemberSpec]:
    """Return the council panel for arm 'A' (incumbent) or 'B' (candidate)."""
    if arm == "A":
        return list(_BASE_PANEL)
    return list(_BASE_PANEL) + [AIFS]


def _key(method: str, url: str, params) -> str:
    blob = json.dumps([method, url, params or {}], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


class FrozenHTTP:
    """Record-on-miss / replay-on-hit proxy around a SafeHTTPClient. In 'replay'
    mode a cache miss is a hard error — that is the guarantee both A/B arms saw the
    exact same upstream bytes. Unknown attributes (requests_made, etc.) delegate to
    the wrapped client."""

    def __init__(self, inner, store: dict, mode: str = "record") -> None:
        self.inner, self.store, self.mode = inner, store, mode
        self.hits = self.misses = 0

    def _do(self, method: str, url: str, params):
        k = _key(method, url, params)
        if k in self.store:
            self.hits += 1
            return self.store[k]
        if self.mode == "replay":
            raise RuntimeError(f"FROZEN cache miss in replay: {method} {url} {params}")
        self.misses += 1
        val = getattr(self.inner, method)(url, params)
        self.store[k] = val
        return val

    def get_json(self, base_url, params):
        return self._do("get_json", base_url, params)

    def get_text(self, base_url, params=None):
        return self._do("get_text", base_url, params)

    def get_gzip_text(self, base_url, params=None):
        return self._do("get_gzip_text", base_url, params)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def _validation(sources: Sources, place, target: dt.date, window: int, arm: str):
    """Build the council for `arm` and return its held-out Validation."""
    agents.COUNCIL = arm_members(arm)
    return Council(sources).deliberate(place, target, window).validation


# --- disjoint-fold sign-stability gate --------------------------------------
#
# A single member's true effect (<=0.01 CRPS) sits BELOW the run-to-run noise
# floor, so an aggregate delta can be an artifact of which days fall in the
# window. Splitting the held-out window into >=2 DISJOINT folds and requiring the
# candidate to beat the incumbent ON EVERY FOLD (CRPS and whole-degree bucket
# hit) is the sign-stability test that separates a real edge from a lucky draw.
# (This is what closed candidate 47: AIFS-for-HK helped in aggregate but a w60
# disjoint fold FLIPPED sign.) Uses Validation.wf_crps (per-day CRPS) and
# Validation.wf_high (per-day point/realized).


def _round_half_up(x: float) -> int:
    return math.floor(x + 0.5)


def _fold_dates(dates: list[str], n_folds: int) -> list[set]:
    uniq = sorted(set(dates))
    k = len(uniq)
    return [set(uniq[(i * k) // n_folds:((i + 1) * k) // n_folds])
            for i in range(n_folds)]


def _fold_crps(wf_crps, fold: set) -> float | None:
    vals = [cc for (d, _attr, cc, _cl) in wf_crps if d in fold]
    return sum(vals) / len(vals) if vals else None


def _fold_bucket_hit(wf_high, fold: set) -> float | None:
    hit = tot = 0
    for d, point, realized in wf_high:
        if d not in fold or point is None or realized is None:
            continue
        tot += 1
        hit += 1 if _round_half_up(point) == _round_half_up(realized) else 0
    return (hit / tot) if tot else None


def _print_fold_gate(va, vb, n_folds: int) -> bool:
    """Print the per-fold table; return True iff candidate B is sign-stable
    (CRPS <= A and bucket hit >= A) on every fold."""
    dates = [d for (d, _a, _c, _cl) in va.wf_crps]
    folds = _fold_dates(dates, n_folds)
    print("-" * 70)
    print(f"  DISJOINT-FOLD SIGN-STABILITY GATE ({n_folds} folds)")
    print(f"  {'fold':5s} {'days':>5s} {'A CRPS':>8s} {'B CRPS':>8s} "
          f"{'dCRPS':>8s} {'A hit':>6s} {'B hit':>6s}  verdict")
    all_pass = True
    for i, fold in enumerate(folds):
        ca, cb = _fold_crps(va.wf_crps, fold), _fold_crps(vb.wf_crps, fold)
        ra, rb = _fold_bucket_hit(va.wf_high, fold), _fold_bucket_hit(vb.wf_high, fold)
        crps_ok = (ca is not None and cb is not None and cb <= ca + 1e-12)
        hit_ok = (ra is not None and rb is not None and rb >= ra - 1e-12)
        ok = crps_ok and hit_ok
        all_pass = all_pass and ok
        d = None if (ca is None or cb is None) else cb - ca
        print(f"  {i:<5d} {len(fold):>5d} "
              f"{('None' if ca is None else f'{ca:.4f}'):>8s} "
              f"{('None' if cb is None else f'{cb:.4f}'):>8s} "
              f"{('None' if d is None else f'{d:+.4f}'):>8s} "
              f"{('None' if ra is None else f'{ra:.2f}'):>6s} "
              f"{('None' if rb is None else f'{rb:.2f}'):>6s}  "
              f"{'PASS' if ok else 'FAIL'}")
    print("-" * 70)
    if all_pass:
        print("  -> SIGN-STABLE on every fold: a real per-fold edge, not a window "
              "artifact.")
    else:
        print("  -> a fold FLIPPED sign: the aggregate delta is a window artifact; "
              "CLOSE the candidate (do not relitigate).")
    return all_pass


def _row(v) -> dict:
    return {
        "crps": v.crps_council, "crps_skill": v.crps_skill, "crps_n": v.crps_n,
        "mae_high": v.council_mae_high, "mae_low": v.council_mae_low,
        "mae_sum": (None if v.council_mae_high is None or v.council_mae_low is None
                    else v.council_mae_high + v.council_mae_low),
        "test_days": v.test_days, "hit2c": v.hit_rate_2c,
    }


def _fmt(x, nd=3):
    return "  None" if x is None else f"{x:.{nd}f}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Frozen-data A/B for a council change.")
    ap.add_argument("city")
    ap.add_argument("--window", type=int, default=60)
    ap.add_argument("--lead", type=int, default=0)
    ap.add_argument("--cache", default=None,
                    help="cache file (default reports/ab_cache_<city>_<window>.pkl)")
    ap.add_argument("--refresh", action="store_true",
                    help="ignore any existing cache and re-record the frozen snapshot")
    ap.add_argument("--folds", type=int, default=0,
                    help="also run the disjoint-fold sign-stability gate with this "
                         "many folds (>=2). A below-noise-floor aggregate gain is "
                         "only believed if it holds on EVERY disjoint fold.")
    args = ap.parse_args()

    repo = Path(__file__).resolve().parent.parent
    slug = args.city.lower().replace(" ", "_").replace(",", "")
    cache_path = Path(args.cache) if args.cache else (
        repo / "reports" / f"ab_cache_{slug}_w{args.window}_l{args.lead}.pkl")

    store: dict = {}
    if cache_path.exists() and not args.refresh:
        store = pickle.loads(cache_path.read_bytes())

    sources = Sources()
    frozen = FrozenHTTP(sources.http, store, mode="replay" if store else "record")
    sources.http = frozen

    place = sources.geocode(args.city)
    target = place_today(place) + dt.timedelta(days=args.lead)

    # Warm the frozen snapshot with the SUPERSET arm (B ⊇ A), so replaying either arm
    # is a pure cache hit. Done once; skipped when a snapshot already exists.
    if frozen.mode == "record":
        print(f"recording frozen snapshot for {place.label()} target={target} "
              f"window={args.window} ...", file=sys.stderr)
        _validation(sources, place, target, args.window, "B")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(pickle.dumps(store))
        frozen.mode = "replay"
        print(f"snapshot frozen: {len(store)} responses -> {cache_path.name}",
              file=sys.stderr)

    # Controlled comparison on byte-identical data.
    va = _validation(sources, place, target, args.window, "A")
    vb = _validation(sources, place, target, args.window, "B")
    va2 = _validation(sources, place, target, args.window, "A")
    a, b, a2 = _row(va), _row(vb), _row(va2)

    det_ok = (a["crps"] == a2["crps"] and a["mae_sum"] == a2["mae_sum"])

    print(f"\nFROZEN-DATA A/B  —  {place.label()}  target {target}  "
          f"window {args.window}d  (replay hits={frozen.hits})")
    print("=" * 70)
    print(f"  arm A (incumbent) : {len(arm_members('A'))} members")
    print(f"  arm B (candidate) : {len(arm_members('B'))} members  (+{AIFS.institution})")
    print(f"  determinism check : A re-run identical -> "
          f"{'PASS' if det_ok else 'FAIL (pipeline non-deterministic!)'}")
    print("-" * 70)
    print(f"  {'metric':12s} {'A':>9s} {'B':>9s} {'B - A':>9s}")
    for label, key, nd in [("CRPS", "crps", 3), ("CRPS skill", "crps_skill", 3),
                           ("MAE high", "mae_high", 3), ("MAE low", "mae_low", 3),
                           ("MAE sum", "mae_sum", 3), ("hit ±2C", "hit2c", 3)]:
        av, bv = a[key], b[key]
        d = (None if av is None or bv is None else bv - av)
        sign = "" if d is None else ("  (B worse)" if (key != "crps_skill" and key != "hit2c") and d > 1e-9
                                     else ("  (B better)" if (key != "crps_skill" and key != "hit2c") and d < -1e-9 else ""))
        print(f"  {label:12s} {_fmt(av, nd):>9s} {_fmt(bv, nd):>9s} "
              f"{('  None' if d is None else f'{d:+.{nd}f}'):>9s}{sign}")
    print(f"  scored days       : A n={a['crps_n']} / test={a['test_days']}   "
          f"B n={b['crps_n']} / test={b['test_days']}")
    print("-" * 70)
    if not det_ok:
        print("  ⚠ determinism FAILED — deltas are not trustworthy; fix nondeterminism first.")
    else:
        dc = b["crps"] - a["crps"] if (a["crps"] and b["crps"]) else None
        if dc is None:
            print("  -> insufficient scored days to judge.")
        elif dc < -0.003:
            print(f"  -> candidate IMPROVES CRPS by {-dc:.3f} on frozen data — worth shipping/verifying further.")
        elif dc > 0.003:
            print(f"  -> candidate WORSENS CRPS by {dc:.3f} on frozen data — do NOT ship.")
        else:
            print(f"  -> candidate is within ±0.003 CRPS — no demonstrable edge; abstain.")

    if det_ok and args.folds and args.folds >= 2:
        _print_fold_gate(va, vb, args.folds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
