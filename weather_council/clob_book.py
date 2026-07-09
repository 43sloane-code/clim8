"""Order-book parsing + executable depth-walk (Phase 2).

The Polymarket verdict has always compared the model's bucket probability to the market's
MID-price. But you cannot trade at the mid: you cross the spread and walk the book, so the
price you actually pay depends on how much depth sits at each ask. This module turns a raw CLOB
`/book` payload into a typed book and answers the one question executable P&L needs — "if I spend
$D buying this token, how many shares do I get, and at what average price?" — by walking the ask
ladder level by level.

It is PURE and READ-ONLY: no network, no I/O, no state, stdlib-only. It parses a payload someone
else fetched and does arithmetic on it. Nothing here places an order, sizes a position, or moves
funds — it MEASURES what execution would cost. The depth-walk convention (the summary's "risk 1
unit"):

    spend $D walking asks  ->  acquire S shares  ->  effective price q_exec = D / S
    a YES token settles at $1 if the bucket hits, $0 otherwise, so:
        win  P&L = S - D   (each share pays $1, you spent $D)
        loss P&L = -D
    at D = $1 that is the summary's "win = S-1, loss = -1, q_exec = 1/S".

Polymarket returns prices and sizes as STRINGS, books can be one-sided (empty bids), and level
ordering is not guaranteed — `parse_book` coerces, drops malformed levels, and sorts best-first
so the walk is correct regardless of input order.

Self-test:  python3 -m weather_council.clob_book
"""
from __future__ import annotations

__all__ = ["BookLevel", "TokenBook", "FillResult", "parse_book", "fill_buy", "book_stats"]

from dataclasses import dataclass


@dataclass(frozen=True)
class BookLevel:
    """One price level: `price` is a probability in (0, 1] (dollars per share), `size` is shares
    available at that price. Both strictly positive — malformed/zero levels are dropped on parse."""
    price: float
    size: float

    @property
    def notional(self) -> float:
        """Dollars it costs to take this whole level (price × size)."""
        return self.price * self.size


@dataclass(frozen=True)
class TokenBook:
    """A parsed one-token order book. `bids` are sorted best-first (HIGHEST price first — the most
    you could sell into); `asks` are sorted best-first (LOWEST price first — the cheapest to buy).
    Either side may be empty (a one-sided book). `token_id` is the CLOB asset id; `timestamp` is
    the book's own stamp when the payload carried one (else None)."""
    token_id: str
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    timestamp: str | None = None

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> float | None:
        """The theoretical mid-price — what the legacy comparison used. None unless BOTH sides
        exist (a mid off a one-sided book would be a fabricated number)."""
        if self.bids and self.asks:
            return (self.bids[0].price + self.asks[0].price) / 2.0
        return None

    @property
    def spread(self) -> float | None:
        if self.bids and self.asks:
            return self.asks[0].price - self.bids[0].price
        return None


@dataclass(frozen=True)
class FillResult:
    """The outcome of walking the asks to spend up to a target dollar amount.

    `shares`   — shares acquired.
    `spent`    — dollars actually spent (≤ target; < target only when the book ran out).
    `avg_price`— spent / shares, the effective per-share price q_exec (None if nothing filled).
    `levels`   — how many ask levels were consumed (partially or fully).
    `filled`   — True if the full target was spent; False if the book was exhausted first.
    """
    shares: float
    spent: float
    avg_price: float | None
    levels: int
    filled: bool


def _coerce_levels(raw_levels, *, best_first_descending: bool) -> tuple[BookLevel, ...]:
    """Coerce a raw [{"price": "..", "size": ".."}, ...] list into sorted BookLevels. Prices and
    sizes arrive as strings; anything non-numeric, non-positive, or a price outside (0, 1] is
    DROPPED (a probability token cannot cost >$1, and a 0 price/size is not a real level).
    `best_first_descending` sorts bids high→low, asks low→high, so index 0 is always the best."""
    out: list[BookLevel] = []
    for lvl in raw_levels or ():
        try:
            price = float(lvl["price"])
            size = float(lvl["size"])
        except (TypeError, ValueError, KeyError):
            continue
        if price <= 0.0 or price > 1.0 or size <= 0.0:
            continue
        out.append(BookLevel(price=price, size=size))
    out.sort(key=lambda b: b.price, reverse=best_first_descending)
    return tuple(out)


def parse_book(raw: dict) -> TokenBook:
    """Parse a raw CLOB `/book` payload into a TokenBook. Tolerant by construction: missing sides
    become empty, malformed levels are dropped, string numerics are coerced, and levels are sorted
    best-first. Never raises on a merely-degenerate payload (returns an empty book instead)."""
    if not isinstance(raw, dict):
        return TokenBook(token_id="", bids=(), asks=(), timestamp=None)
    token_id = str(raw.get("asset_id") or raw.get("token_id") or "")
    ts = raw.get("timestamp")
    ts = str(ts) if ts is not None else None
    bids = _coerce_levels(raw.get("bids"), best_first_descending=True)    # highest bid first
    asks = _coerce_levels(raw.get("asks"), best_first_descending=False)   # lowest ask first
    return TokenBook(token_id=token_id, bids=bids, asks=asks, timestamp=ts)


def fill_buy(book: TokenBook, dollars: float) -> FillResult:
    """Walk the ask ladder spending UP TO `dollars`, taking the cheapest shares first. Returns a
    FillResult. `filled` is False when the book runs out of asks before the full amount is spent —
    the caller must treat that as UNTRADEABLE-at-size, not as a cheap fill. A non-positive target
    or an empty ask side yields an all-zero, unfilled result (never raises, never divides by zero)."""
    if dollars <= 0.0 or not book.asks:
        return FillResult(shares=0.0, spent=0.0, avg_price=None, levels=0, filled=False)
    remaining = dollars
    shares = 0.0
    spent = 0.0
    levels = 0
    for lvl in book.asks:
        if remaining <= 0.0:
            break
        level_cost = lvl.notional                    # cost to take this whole level
        if level_cost <= remaining:                  # consume the level fully
            shares += lvl.size
            spent += level_cost
            remaining -= level_cost
            levels += 1
        else:                                        # partial fill: spend what's left here
            take_shares = remaining / lvl.price
            shares += take_shares
            spent += remaining
            remaining = 0.0
            levels += 1
            break
    filled = remaining <= 1e-9
    avg = (spent / shares) if shares > 0 else None
    return FillResult(shares=shares, spent=spent, avg_price=avg, levels=levels, filled=filled)


def book_stats(book: TokenBook) -> dict:
    """A flat, JSON-friendly summary of the book: best bid/ask, mid, spread, per-side depth (total
    dollars resting) and level counts. Depth is the notional (Σ price×size) a side could absorb —
    the number that says whether a $1 fill even scratches the surface. None where a side is absent."""
    return {
        "token_id": book.token_id,
        "timestamp": book.timestamp,
        "best_bid": book.best_bid,
        "best_ask": book.best_ask,
        "mid": book.mid,
        "spread": book.spread,
        "bid_depth_usd": round(sum(b.notional for b in book.bids), 6),
        "ask_depth_usd": round(sum(a.notional for a in book.asks), 6),
        "n_bid_levels": len(book.bids),
        "n_ask_levels": len(book.asks),
    }


def _selftest() -> None:
    # 1. parse: strings coerced, sides sorted best-first, timestamp carried.
    raw = {
        "asset_id": "TKN1", "timestamp": "2026-07-10T12:00:00Z",
        "bids": [{"price": "0.40", "size": "100"}, {"price": "0.45", "size": "50"}],
        "asks": [{"price": "0.55", "size": "200"}, {"price": "0.52", "size": "100"}],
    }
    b = parse_book(raw)
    assert b.token_id == "TKN1" and b.timestamp == "2026-07-10T12:00:00Z"
    assert b.best_bid == 0.45 and b.best_ask == 0.52          # sorted best-first
    assert abs(b.mid - 0.485) < 1e-12
    assert abs(b.spread - 0.07) < 1e-12

    # 2. parse drops malformed / out-of-range / non-positive levels.
    dirty = parse_book({
        "asset_id": "TKN2",
        "bids": [{"price": "x", "size": "10"}, {"price": "0.3", "size": "0"},
                 {"price": "0.5"}, {"price": "0.6", "size": "5"}],
        "asks": [{"price": "1.5", "size": "10"}, {"price": "0", "size": "10"},
                 {"price": "0.7", "size": "8"}],
    })
    assert len(dirty.bids) == 1 and dirty.bids[0].price == 0.6   # only the clean bid survives
    assert len(dirty.asks) == 1 and dirty.asks[0].price == 0.7   # price>1 and price=0 dropped

    # 3. empty / one-sided / non-dict payloads degrade safely (no mid off one side).
    assert parse_book({"asset_id": "E"}).mid is None
    one_sided = parse_book({"asset_id": "O", "asks": [{"price": "0.5", "size": "10"}]})
    assert one_sided.best_bid is None and one_sided.mid is None and one_sided.spread is None
    assert parse_book("not a dict").token_id == ""              # type: ignore[arg-type]

    # 4. fill within the first level: $1 at 0.52 -> 1/0.52 shares, one level, fully filled.
    f1 = fill_buy(b, 1.0)
    assert f1.filled and f1.levels == 1
    assert abs(f1.shares - 1.0 / 0.52) < 1e-9
    assert abs(f1.spent - 1.0) < 1e-12
    assert abs(f1.avg_price - 0.52) < 1e-12                     # q_exec == best ask here

    # 5. fill that crosses into a second level pays a WORSE average than the best ask.
    #    Best level: 100 sh @ 0.52 = $52 notional. Spend $60 -> take all of L1 ($52, 100 sh)
    #    then $8 of L2 @ 0.55 -> 8/0.55 sh. avg > 0.52.
    f2 = fill_buy(b, 60.0)
    assert f2.levels == 2 and f2.filled
    exp_shares = 100.0 + 8.0 / 0.55
    assert abs(f2.shares - exp_shares) < 1e-9
    assert abs(f2.spent - 60.0) < 1e-12
    assert f2.avg_price > 0.52                                  # slippage: worse than top of book

    # 6. book exhausted before the target is spent -> filled=False (UNTRADEABLE at size).
    #    Total ask notional = 100*0.52 + 200*0.55 = 52 + 110 = 162. Ask $1000.
    f3 = fill_buy(b, 1000.0)
    assert not f3.filled and f3.levels == 2
    assert abs(f3.spent - 162.0) < 1e-9
    assert abs(f3.shares - 300.0) < 1e-9                        # 100 + 200 shares, all of it

    # 7. non-positive target and empty-ask book both give an all-zero, unfilled result.
    for res in (fill_buy(b, 0.0), fill_buy(b, -5.0), fill_buy(one_sided_no_ask(), 1.0)):
        assert res.shares == 0.0 and res.spent == 0.0 and res.avg_price is None
        assert res.levels == 0 and res.filled is False

    # 8. exact-notional fill consumes exactly one level and reports filled.
    f4 = fill_buy(b, 52.0)                                      # exactly all of L1
    assert f4.filled and f4.levels == 1
    assert abs(f4.shares - 100.0) < 1e-9 and abs(f4.spent - 52.0) < 1e-12

    # 9. book_stats reports depth, spread and counts; None-safe on one-sided.
    st = book_stats(b)
    assert st["best_ask"] == 0.52 and st["n_ask_levels"] == 2
    assert abs(st["ask_depth_usd"] - 162.0) < 1e-6
    assert abs(st["bid_depth_usd"] - (0.45 * 50 + 0.40 * 100)) < 1e-6
    st1 = book_stats(one_sided)
    assert st1["mid"] is None and st1["spread"] is None and st1["n_bid_levels"] == 0

    # 10. depth-walk P&L identity: at $1, win P&L = shares-1, loss = -1, q_exec = 1/shares.
    f = fill_buy(b, 1.0)
    win_pnl, loss_pnl = f.shares - 1.0, -1.0
    assert abs((1.0 / f.shares) - f.avg_price) < 1e-9          # q_exec = 1/S
    assert win_pnl > 0 and loss_pnl == -1.0

    print("clob_book selftest PASSED (parse coerce/drop/sort/one-sided; fill within/cross/exhaust/"
          "exact/nonpositive; stats depth+None-safe; $1 depth-walk P&L identity)")


def one_sided_no_ask() -> TokenBook:
    """A bids-only book (no asks) — used by the self-test to prove fill_buy handles an empty ask
    side. Kept module-level so the frozen-dataclass construction is exercised, not faked."""
    return parse_book({"asset_id": "BIDSONLY", "bids": [{"price": "0.4", "size": "10"}]})


if __name__ == "__main__":
    _selftest()
