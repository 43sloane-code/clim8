"""Realized PAPER P&L — the money question edge.py (Brier skill) doesn't answer.

For each SETTLED day (the contract's own pm_resolved_label) that also carried a
live market, simulate a flat-stake value bet on the model's modal bucket at the
market's price, and report realized P&L — net of the price paid, with a LIQUIDITY
FLOOR, because the model's favoured bucket is frequently priced ~0 (no two-sided
quote) and is therefore NOT actually tradeable. Also reports who names the settled
bucket more often (model vs market) and the Brier on the contract's settled bucket.

HONEST SCOPE (read before trusting a number):
  * Prices are the DE-VIGGED market_prob stored at snapshot time, so real fills
    (which pay the vig and walk a thin book) are WORSE than shown — this is an
    UPPER bound on profitability.
  * A win at a 5-15c price is the system's own "stale placeholder" warning made
    real; the --robust view drops those so you can see P&L without them.
  * n is tiny. This measures whether a tradeable edge EXISTS, not a deployed
    strategy's track record.

Unit: 1 unit RISKED per bet (buy 1/q shares at price q) -> win returns (1-q)/q,
loss returns -1. So one win at a 8c price books +11.5 — which is exactly why the
liquidity floor and the --robust view matter.

Usage:  PYTHONPATH=. python3 tools/paper_pnl.py [--floor 0.05] [--robust 0.15] [--selftest]
"""
from __future__ import annotations
import argparse
import json

LIQ_FLOOR = 0.05      # below this market price -> no live quote -> UNTRADEABLE
ROBUST_FLOOR = 0.15   # a stricter "is there real liquidity" view


def _modal(buckets: dict, which: int) -> str | None:
    """Label of the max-probability bucket. which=0 model, 1 market."""
    if not buckets:
        return None
    return max(buckets, key=lambda lbl: buckets[lbl][which])


def _brier(buckets: dict, which: int, settled: str) -> float:
    """Brier over the bucket ladder for one forecaster vs the settled bucket."""
    return sum((p[which] - (1.0 if lbl == settled else 0.0)) ** 2
               for lbl, p in buckets.items())


def simulate(days: list[dict], floor: float = LIQ_FLOOR) -> dict:
    """days: [{place, date, settled, buckets:{label:(model_p, market_p)}}].
    Pure + deterministic so it can be unit-tested. Returns the P&L summary."""
    model_pnl = 0.0
    bets = 0
    skipped_no_liq = 0
    model_hits = market_hits = scored = 0
    model_brier = market_brier = 0.0
    rows = []
    for d in days:
        bk, settled = d["buckets"], d["settled"]
        if not bk or settled is None:
            continue
        scored += 1
        mm, km = _modal(bk, 0), _modal(bk, 1)         # model / market modal
        model_hits += (mm == settled)
        market_hits += (km == settled)
        model_brier += _brier(bk, 0, settled)
        market_brier += _brier(bk, 1, settled)
        # value bet: model's modal bucket, only if a tradeable price AND model sees value
        q = bk.get(mm, (0.0, 0.0))[1]
        p_model = bk.get(mm, (0.0, 0.0))[0]
        verdict = "no-bet"
        pnl = 0.0
        if q < floor:
            skipped_no_liq += 1
            verdict = f"UNTRADEABLE (q={q:.2f}<{floor:.2f})"
        elif p_model > q:
            bets += 1
            win = (mm == settled)
            pnl = (1.0 - q) / q if win else -1.0
            model_pnl += pnl
            verdict = f"bet {mm}@{q:.2f} -> {'WIN' if win else 'loss'} {pnl:+.2f}"
        else:
            verdict = f"no edge (model {p_model:.2f} <= mkt {q:.2f})"
        rows.append((d.get("place", "?"), d.get("date", "?"), settled, mm, km, verdict))
    return {
        "scored": scored, "bets": bets, "skipped_no_liq": skipped_no_liq,
        "model_pnl": round(model_pnl, 3),
        "model_hit_rate": (model_hits / scored) if scored else None,
        "market_hit_rate": (market_hits / scored) if scored else None,
        "model_brier": (model_brier / scored) if scored else None,
        "market_brier": (market_brier / scored) if scored else None,
        "rows": rows,
    }


def _load_from_db() -> list[dict]:
    from weather_council import storage
    conn = storage._connect()
    raw = conn.execute(
        "SELECT place,target_date,pm_resolved_label,buckets_json,issued_at "
        "FROM market_snapshots WHERE pm_resolved_label IS NOT NULL "
        "ORDER BY place,target_date,issued_at").fetchall()
    conn.close()
    seen, out = set(), []
    for place, date, settled, bj, _ in raw:
        if (place, date) in seen:        # first-issued = most day-ahead, least leaking
            continue
        seen.add((place, date))
        try:
            bks = json.loads(bj) if bj else []
        except ValueError:
            bks = []
        buckets = {b.get("label"): (float(b.get("model_prob") or 0.0),
                                    float(b.get("market_prob") or 0.0))
                   for b in bks if b.get("label")}
        if buckets:
            out.append({"place": place, "date": date, "settled": settled, "buckets": buckets})
    return out


def _report(days, floor, robust) -> int:
    if not days:
        print("no settled market-priced days yet — run accumulate.py daily to bank them.")
        return 0
    base = simulate(days, floor)
    rob = simulate(days, robust)
    print(f"\nPAPER P&L — {base['scored']} settled market-priced day(s)")
    print("=" * 66)
    for place, date, settled, mm, km, verdict in base["rows"]:
        print(f"  {place[:18]:18} {date}  settled {settled:>5} | model {str(mm):>5} "
              f"mkt {str(km):>5} | {verdict}")
    print("-" * 66)
    mh, kh = base["model_hit_rate"], base["market_hit_rate"]
    print(f"  names the settled bucket : model {mh*100:4.0f}%  vs  market {kh*100:4.0f}%  (n={base['scored']})")
    print(f"  Brier (lower=better)     : model {base['model_brier']:.3f}  vs  market {base['market_brier']:.3f}")
    print(f"  value-bet P&L  @floor {floor:.2f} : {base['model_pnl']:+.2f} u over {base['bets']} bet(s), "
          f"{base['skipped_no_liq']} untradeable")
    print(f"  value-bet P&L  @floor {robust:.2f} : {rob['model_pnl']:+.2f} u over {rob['bets']} bet(s)  "
          f"(drops thin-price 'wins')")
    print("-" * 66)
    print("  NOTE: prices are de-vigged (real fills are worse); a win at a sub-15c")
    print("  price is the 'stale placeholder' warning made real. Tiny n — this says")
    print("  whether an edge EXISTS, not that a strategy works.")
    return 0


def _selftest() -> int:
    days = [
        # d1: thin-price WIN — model 0.6 > mkt 0.08 on 31C, settles 31C
        {"place": "T", "date": "d1", "settled": "31C",
         "buckets": {"31C": (0.6, 0.08), "32C": (0.4, 0.85)}},
        # d2: normal-price LOSS — model bets 31C @0.40, settles 32C
        {"place": "T", "date": "d2", "settled": "32C",
         "buckets": {"31C": (0.6, 0.40), "32C": (0.4, 0.50)}},
        # d3: UNTRADEABLE — model's modal priced 0.00 (no quote)
        {"place": "T", "date": "d3", "settled": "33C",
         "buckets": {"33C": (0.7, 0.00), "34C": (0.3, 0.95)}},
    ]
    r = simulate(days, floor=0.05)
    assert r["scored"] == 3 and r["bets"] == 2 and r["skipped_no_liq"] == 1, r
    # d1 win (1-.08)/.08 = +11.5 ; d2 loss -1 ; net +10.5
    assert abs(r["model_pnl"] - 10.5) < 1e-9, r["model_pnl"]
    # robust floor 0.15 makes the 8c bet untradeable -> drops the thin win, only d2 loss remains
    rob = simulate(days, floor=0.15)
    assert rob["bets"] == 1 and abs(rob["model_pnl"] + 1.0) < 1e-9, rob
    assert abs(r["model_hit_rate"] - 2 / 3) < 1e-9 and abs(r["market_hit_rate"] - 1 / 3) < 1e-9, r
    print("paper_pnl self-test PASSED (value-bet P&L exact; floor gates untradeable + thin-price wins)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Realized paper P&L vs the market.")
    ap.add_argument("--floor", type=float, default=LIQ_FLOOR)
    ap.add_argument("--robust", type=float, default=ROBUST_FLOOR)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    return _report(_load_from_db(), args.floor, args.robust)


if __name__ == "__main__":
    raise SystemExit(main())
