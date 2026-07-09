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

from weather_council.clob_book import fill_buy, parse_book

LIQ_FLOOR = 0.05      # below this market price -> no live quote -> UNTRADEABLE
ROBUST_FLOOR = 0.15   # a stricter "is there real liquidity" view
EXEC_STAKE = 1.0      # dollars RISKED per executable bet (spend $1 walking the asks)


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


def simulate_executable(days: list[dict], stake: float = EXEC_STAKE) -> dict:
    """The EXECUTABLE counterpart to simulate(): instead of pricing the bet at the
    de-vigged mid, WALK the archived order book for the model's modal bucket and pay
    what $`stake` actually buys. You cannot trade at the mid — this is the honest
    price.

    Each day needs an `exec_books` map {bucket_label: clob_book.TokenBook | None} (the
    book captured at snapshot time). For the model's modal bucket:
      * no book archived            -> NO-BOOK   (cannot simulate execution)
      * book present but $stake can't be filled from the asks (empty/one-sided/too
        thin)                       -> UNTRADEABLE-EXEC
      * otherwise spend $stake -> S shares at q_exec = stake/S; bet only when the
        model still sees value at THAT executable price (p_model > q_exec).
    Bet outcome (settles $1/share): win P&L = S - stake, loss P&L = -stake. Pure and
    deterministic. Returns the executable summary (same shape idiom as simulate())."""
    exec_pnl = 0.0
    bets = 0
    no_book = untradeable = 0
    rows = []
    for d in days:
        bk, settled = d["buckets"], d["settled"]
        books = d.get("exec_books") or {}
        if not bk or settled is None:
            continue
        mm = _modal(bk, 0)                              # model modal bucket
        p_model = bk.get(mm, (0.0, 0.0))[0]
        book = books.get(mm)
        verdict = "no-bet"
        if book is None:
            no_book += 1
            verdict = "NO-BOOK (no order book archived for the modal bucket)"
        else:
            fill = fill_buy(book, stake)
            if not fill.filled or fill.shares <= 0.0:
                untradeable += 1
                depth = round(fill.spent, 2)
                verdict = (f"UNTRADEABLE-EXEC (asks hold only ${depth:.2f} of the "
                           f"${stake:.2f} stake)")
            else:
                q_exec = fill.avg_price                 # == stake / shares
                if p_model > q_exec:
                    bets += 1
                    win = (mm == settled)
                    pnl = (fill.shares - stake) if win else -stake
                    exec_pnl += pnl
                    verdict = (f"bet {mm}@q_exec={q_exec:.3f} ({fill.shares:.2f} sh) -> "
                               f"{'WIN' if win else 'loss'} {pnl:+.2f}")
                else:
                    verdict = f"no edge (model {p_model:.2f} <= q_exec {q_exec:.2f})"
        rows.append((d.get("place", "?"), d.get("date", "?"), settled, mm, verdict))
    return {
        "bets": bets, "no_book": no_book, "untradeable_exec": untradeable,
        "exec_pnl": round(exec_pnl, 3), "rows": rows,
        "has_books": any((d.get("exec_books") for d in days)),
    }


def _load_exec_books(conn, place, date, issued_at) -> dict:
    """{bucket_label: TokenBook} for the order books archived at the SAME instant as
    the chosen price snapshot (fetch_ok=1 rows only). Empty when nothing was captured
    — simulate_executable then classifies those buckets NO-BOOK. Read-only."""
    try:
        rows = conn.execute(
            "SELECT bucket_label, book_json FROM book_snapshots "
            "WHERE place=? AND target_date=? AND issued_at=? AND fetch_ok=1",
            (place, date, issued_at)).fetchall()
    except Exception:
        return {}
    out = {}
    for label, book_json in rows:
        if not label or not book_json:
            continue
        try:
            out[label] = parse_book(json.loads(book_json))
        except (ValueError, TypeError):
            continue
    return out


def _load_from_db() -> list[dict]:
    from weather_council import storage
    conn = storage._connect()
    raw = conn.execute(
        "SELECT place,target_date,pm_resolved_label,buckets_json,issued_at "
        "FROM market_snapshots WHERE pm_resolved_label IS NOT NULL "
        "ORDER BY place,target_date,issued_at").fetchall()
    seen, out = set(), []
    for place, date, settled, bj, issued_at in raw:
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
            # Attach the executable order books captured at THIS snapshot's instant.
            # Legacy simulate() ignores exec_books; simulate_executable() reads it.
            out.append({"place": place, "date": date, "settled": settled,
                        "buckets": buckets,
                        "exec_books": _load_exec_books(conn, place, date, issued_at)})
    conn.close()
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
    # EXECUTABLE view (Phase 5): walk the archived order book instead of the mid.
    ex = simulate_executable(days)
    print("-" * 66)
    if not ex["has_books"]:
        print("  EXECUTABLE P&L: no order books archived yet — run book_logger / accumulate")
        print("  to capture depth, then this line reports fills at real prices, not the mid.")
    else:
        # Compare mid vs executable on the SAME days that have a book, so the gap is
        # purely the cost of crossing the spread / walking depth.
        book_days = [d for d in days if d.get("exec_books")]
        mid_on_book = simulate(book_days, floor)
        slip = mid_on_book["model_pnl"] - ex["exec_pnl"]
        print(f"  EXECUTABLE P&L (walks the book, ${EXEC_STAKE:.0f}/bet — real fills, not the mid)")
        print(f"    executable value-bet P&L : {ex['exec_pnl']:+.2f} u over {ex['bets']} bet(s)")
        print(f"    vs mid-view (same days)  : {mid_on_book['model_pnl']:+.2f} u  "
              f"-> execution gap {slip:+.2f} u from crossing the spread")
        print(f"    classification           : {ex['bets']} tradeable, "
              f"{ex['untradeable_exec']} untradeable-exec, {ex['no_book']} no-book")
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

    # Executable P&L: walk real books. A deep ask book, a too-thin book (UNTRADEABLE-
    # EXEC), and a missing book (NO-BOOK) — one of each.
    deep = parse_book({"asset_id": "A", "bids": [{"price": "0.30", "size": "100"}],
                       "asks": [{"price": "0.50", "size": "100"}]})   # $1 buys 2 sh @ 0.50
    thin = parse_book({"asset_id": "B", "bids": [{"price": "0.30", "size": "100"}],
                       "asks": [{"price": "0.50", "size": "1"}]})     # only $0.50 of depth
    exd = [
        # win: model 0.7 > q_exec 0.50, buys 2 shares, settles the modal bucket -> +1.0
        {"place": "T", "date": "e1", "settled": "31C",
         "buckets": {"31C": (0.7, 0.40), "32C": (0.3, 0.55)},
         "exec_books": {"31C": deep}},
        # untradeable-exec: book too thin to fill the $1 stake
        {"place": "T", "date": "e2", "settled": "32C",
         "buckets": {"32C": (0.7, 0.40), "33C": (0.3, 0.55)},
         "exec_books": {"32C": thin}},
        # no-book: modal bucket has no archived book
        {"place": "T", "date": "e3", "settled": "34C",
         "buckets": {"34C": (0.7, 0.40)}, "exec_books": {}},
    ]
    ex = simulate_executable(exd, stake=1.0)
    assert ex["bets"] == 1 and ex["untradeable_exec"] == 1 and ex["no_book"] == 1, ex
    assert abs(ex["exec_pnl"] - 1.0) < 1e-9, ex["exec_pnl"]        # 2 shares - $1 stake
    assert ex["has_books"] is True
    # a loss at the executable price: same deep book, but settles elsewhere -> -1.0
    exd_loss = [{"place": "T", "date": "e4", "settled": "99C",
                 "buckets": {"31C": (0.7, 0.40)}, "exec_books": {"31C": deep}}]
    assert abs(simulate_executable(exd_loss)["exec_pnl"] + 1.0) < 1e-9

    print("paper_pnl self-test PASSED (mid value-bet P&L exact; floor gates untradeable + "
          "thin-price wins; executable walk gives win/loss/UNTRADEABLE-EXEC/NO-BOOK)")
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
