"""Read-only Polymarket market-data layer.

This module ingests *public* Polymarket data so the council's backtested verdict
can be set beside the market's own implied probabilities. It is strictly
read-only: it fetches and parses, nothing more. It never places, cancels, or
prices an order, never touches a wallet, and never moves funds — those are out
of scope for this project by design.

The relevant markets live under the Gamma "Highest temperature" tag. Each
*event* is one city on one day (e.g. "Highest temperature in London on June
4?"); each *market* inside it is one whole-degree bucket ("19°C", "13°C or
below"). A bucket's "Yes" price is the market's implied probability that the
day's high lands in that bucket — the same airport-station, whole-degree record
the council's settlement block already quantizes onto.

Gamma quirk handled here: outcomes, outcomePrices, and clobTokenIds come back as
JSON-encoded *strings*, not arrays, so each is json.loads'd defensively.
"""

from __future__ import annotations

__all__ = [
    'MarketBucket', 'WeatherMarket', 'MarketData', 'Resolution',
    'resolved_event_slug', 'event_slug',
]

import datetime as dt
import json
import math
import re
from dataclasses import dataclass

from .security import SafeHTTPClient, SecurityError
from .sources import _round_half_up  # one shared settlement-rounding convention

EVENTS_URL = "https://gamma-api.polymarket.com/events"
# Polymarket CLOB market-data endpoint for ONE token's live order book. Public,
# keyless, READ-ONLY (market data, not the signed trading surface). Consumed only
# to archive executable depth beside a price snapshot; see clob_book / book_logger.
CLOB_BOOK_URL = "https://clob.polymarket.com/book"
# Gamma tag for the daily city high-temperature markets that settle on a named
# airport station — the markets this project can actually speak to.
HIGHEST_TEMP_TAG = 104596

# Leave headroom under the per-run request budget for the weather pipeline; the
# market layer is a side comparison and must never starve the core verdict.
DEFAULT_MARKET_REQUEST_BUDGET = 4
_PAGE_SIZE = 100

_TITLE_RE = re.compile(
    r"(?P<kind>highest|lowest) temperature in (?P<city>.+?) on (?P<date>.+?)\?*$", re.I)
# Markets phrase the settlement point a few ways: "recorded at the X Station"
# (airport METAR), "recorded by the Hong Kong Observatory", or "recorded by NOAA
# at the X Airport". The non-greedy gap absorbs an intervening agency name.
_STATION_RE = re.compile(
    r"recorded\b.*?\b(?:at|by) the (?P<station>.+?) in degrees (?P<unit>celsius|fahrenheit)",
    re.I,
)
# Settlement precision the rules promise (e.g. HK Observatory reads 0.1 °C).
_DECIMAL_RE = re.compile(r"to one decimal place", re.I)

# Bucket-label edge parsing. Order matters: tails and ranges before the bare
# single-degree pattern (which would otherwise grab the first number of "76-77").
_EDGE_BELOW = re.compile(r"(-?\d+)\s*°[CF]\s*or\s*(?:below|lower)", re.I)
_EDGE_ABOVE = re.compile(r"(-?\d+)\s*°[CF]\s*or\s*(?:higher|above)", re.I)
_EDGE_RANGE = re.compile(r"(-?\d+)\s*-\s*(-?\d+)\s*°[CF]", re.I)
_EDGE_SINGLE = re.compile(r"(-?\d+)\s*°[CF]", re.I)


def _bucket_edges(label: str) -> tuple[int | None, int | None]:
    """Parse a bucket label into inclusive integer edges in the native unit.
    A None edge means open-ended ("or below" / "or higher")."""
    m = _EDGE_BELOW.search(label)
    if m:
        return (None, int(m.group(1)))
    m = _EDGE_ABOVE.search(label)
    if m:
        return (int(m.group(1)), None)
    m = _EDGE_RANGE.search(label)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m = _EDGE_SINGLE.search(label)
    if m:
        n = int(m.group(1))
        return (n, n)
    return (None, None)


def _native_reading_int(value_c: float, grain: str,
                        sub_degree: bool = False) -> int:
    """The whole-degree bucket a record settles into, in the market's native unit.

    Two regimes, both verified against the live Polymarket contracts:

      * **Whole-degree markets** (US °F ASOS, London City Airport whole °C): the
        source reports an integer reading, so the bucket is round-half-up of the
        converted value — `_round_half_up`. floor == round here (the value is
        already integral), so this is unchanged.
      * **Sub-degree markets** (HK Observatory at 0.1 °C): the contract resolves
        to "the temperature RANGE that contains the highest temperature", and a
        whole-degree bucket "N°C" is the half-open range [N, N+1). The reading is
        therefore the FLOOR of the native value, not round-half-up — e.g. an
        Observatory high of 28.6 °C settles the 28 °C bucket, NOT 29. Using
        round-half-up here is exactly the bug that mis-settled HK by one bucket.
    """
    native = value_c * 9 / 5 + 32 if grain == "F" else value_c
    return math.floor(native) if sub_degree else _round_half_up(native)


@dataclass(frozen=True)
class MarketBucket:
    """One whole-degree outcome of a city/day temperature event.

    The microstructure fields below are READ-ONLY market context, captured so a
    snapshot can later be judged on *how real* the price was — they never feed a
    verdict, a vote, or a trade. Two facts the bare Yes price hides:
      * `volume` is CUMULATIVE traded notional over the market's whole life, so a
        bucket can carry huge volume yet sit at ~0 now (it was in contention
        earlier, then the day resolved away from it).
      * `liquidity` is CURRENT resting order-book depth, and is often *lowest* on
        the near-certain winning bucket (nobody posts two-sided orders on a 0.99
        outcome). Depth therefore measures contestedness, not conviction.
    `best_bid`/`best_ask`/`last_trade` are the live quote; a bucket with no bid
    and a 0.001 ask is a stale placeholder, not a real price."""
    label: str                  # e.g. "19°C", "13°C or below"
    yes_price: float | None     # market-implied P(high lands in this bucket)
    no_price: float | None
    token_ids: tuple[str, ...]
    lo: int | None              # inclusive lower edge, native unit (None = open)
    hi: int | None              # inclusive upper edge, native unit (None = open)
    # Read-only microstructure (USDC notionals; prices in [0,1]). Default None so
    # existing constructors/tests build unchanged.
    liquidity: float | None = None    # current resting depth, Gamma liquidityNum
    volume: float | None = None       # cumulative traded notional, Gamma volumeNum
    volume_24hr: float | None = None  # last-24h traded notional
    best_bid: float | None = None     # top of book; None = no bid posted
    best_ask: float | None = None
    last_trade: float | None = None

    def contains(self, reading_int: int) -> bool:
        if self.lo is None and self.hi is None:
            return False        # unparseable label never matches
        if self.lo is not None and reading_int < self.lo:
            return False
        if self.hi is not None and reading_int > self.hi:
            return False
        return True

    def has_two_sided_quote(self, max_spread: float = 0.10) -> bool:
        """True when this bucket carries a GENUINE two-sided quote: a real bid
        (>0) and an ask, with a spread tight enough that the price is informative.
        A bucket with no bid and a 0.001 placeholder ask returns False — its
        listed price is noise, and de-vigging across many such buckets inflates
        the market distribution with mass nobody is actually trading."""
        if self.best_bid is None or self.best_ask is None:
            return False
        if self.best_bid <= 0.0:
            return False
        return (self.best_ask - self.best_bid) <= max_spread


@dataclass(frozen=True)
class WeatherMarket:
    """A single city-on-a-day Polymarket event, parsed read-only."""
    event_id: str
    title: str
    city: str | None
    date_label: str | None
    station: str | None         # settlement station named in the rules
    grain: str                  # "C" or "F" — the unit the market settles in
    precision: str              # how finely it settles, e.g. "0.1°C" or "whole °C"
    resolution_source: str | None
    end_date: str | None
    slug: str | None
    buckets: tuple[MarketBucket, ...]
    # Event-level read-only totals (USDC). Default None so non-Gamma constructors
    # and tests build unchanged.
    volume: float | None = None        # whole-event cumulative traded notional
    liquidity: float | None = None     # whole-event resting depth

    def quote_quality(self, max_spread: float = 0.10) -> tuple[int, int]:
        """(n_two_sided, n_total): how many buckets carry a genuine two-sided
        quote out of all of them. A market where only one or two buckets are
        really quoted is a thin, one-sided book — its de-vigged distribution is
        dominated by placeholder asks and should be treated with suspicion."""
        n = len(self.buckets)
        live = sum(1 for b in self.buckets if b.has_two_sided_quote(max_spread))
        return (live, n)

    def modal_bucket(self) -> MarketBucket | None:
        """The bucket the market currently favours most (highest Yes price).
        A plain readout of the market's view — not a derived/de-vigged
        probability (that calibration step is deliberately left to later work)."""
        priced = [b for b in self.buckets if b.yes_price is not None]
        return max(priced, key=lambda b: b.yes_price) if priced else None

    def overround(self) -> float | None:
        """Σ of the bucket Yes prices minus 1 — the bookmaker's vig. >0 means the
        prices imply more than 100% probability (the market's margin)."""
        priced = [b.yes_price for b in self.buckets if b.yes_price is not None]
        return (sum(priced) - 1.0) if priced else None

    def implied_probabilities(self) -> dict[str, float] | None:
        """De-vigged market probabilities per bucket.

        The buckets form a complete, mutually-exclusive partition of the day's
        high, so the raw Yes prices (which sum to 1 + overround) are normalised
        proportionally back to a proper distribution: pᵢ = yesᵢ / Σ yes. This is
        the standard multiplicative de-vig; it assumes the margin is spread in
        proportion to each price (not an equal additive cut, nor Shin's
        informed-trader model). Returns None if fewer than two priced buckets."""
        priced = {b.label: b.yes_price for b in self.buckets if b.yes_price is not None}
        total = sum(priced.values())
        if len(priced) < 2 or total <= 0:
            return None
        return {label: p / total for label, p in priced.items()}

    def settles_sub_degree(self) -> bool:
        """True when the market settles finer than whole degrees (e.g. the Hong
        Kong Observatory at 0.1°C). Its bucket labels are still whole integers,
        so the contract's rule for mapping a 0.1° reading into a whole-degree
        bucket is *not* recoverable from the labels alone — and HK also settles
        on the Observatory, not the airport the council backtests. Snapping such
        a market to a whole degree would invent a mapping we have not verified."""
        return not self.precision.lower().startswith("whole")

    def native_reading(self, verdict_high_c: float) -> int:
        """The whole-degree, native-unit bucket index this verdict settles as.
        Round-half-up for whole-degree markets; floor (range-containment) for
        sub-degree markets — see _native_reading_int and settles_sub_degree()."""
        return _native_reading_int(verdict_high_c, self.grain,
                                   self.settles_sub_degree())

    def bucket_for_high(self, verdict_high_c: float) -> MarketBucket | None:
        """Which bucket a continuous °C high verdict settles into, by mapping it
        to the market's native whole-degree bucket index (round-half-up for
        whole-degree markets, floor/range-containment for sub-degree ones) and
        finding the containing range. Returns None if no bucket matches
        (shouldn't happen for a well-formed contiguous ladder)."""
        reading = self.native_reading(verdict_high_c)
        for b in self.buckets:
            if b.contains(reading):
                return b
        return None

    def edge_distance_c(self, verdict_high_c: float) -> float | None:
        """How far (in °C) the verdict sits from the nearest interior bucket
        boundary — small means the bucket assignment is fragile. None at the
        open-ended tails, where there is no near edge. Native °F edges are
        converted to °C so this is comparable across markets."""
        b = self.bucket_for_high(verdict_high_c)
        if b is None:
            return None
        # Where the bucket edges sit depends on the settlement rule:
        #   * round-half-up (whole-degree markets): edges at the half-integers,
        #     so bucket "N" spans [N-0.5, N+0.5).
        #   * floor / range-containment (sub-degree markets, e.g. HK 0.1°C):
        #     bucket "N" spans the half-open INTEGER range [N, N+1), so its edges
        #     sit at N (lower) and N+1 (upper).
        # Edges are in native units, converted to °C so the distance is comparable.
        dists = []
        if self.settles_sub_degree():
            if b.hi is not None:
                dists.append(abs(self._to_c(b.hi + 1.0) - verdict_high_c))
            if b.lo is not None:
                dists.append(abs(verdict_high_c - self._to_c(b.lo)))
        else:
            if b.hi is not None:
                dists.append(abs(self._to_c(b.hi + 0.5) - verdict_high_c))
            if b.lo is not None:
                dists.append(abs(verdict_high_c - self._to_c(b.lo - 0.5)))
        return min(dists) if dists else None

    def _bucket_label_for_reading(self, reading_int: int) -> str | None:
        """The bucket label a native whole-degree reading lands in (None if no
        bucket contains it)."""
        for b in self.buckets:
            if b.contains(reading_int):
                return b.label
        return None

    def rounding_rule_robustness(
        self, verdict_high_c: float
    ) -> tuple[bool, str | None, str | None] | None:
        """For a market that settles FINER than its whole-degree bucket labels
        (e.g. HK at 0.1 °C), test whether the chosen whole-degree bucket actually
        DEPENDS on the 0.1°→whole rounding rule the contract labels do not reveal.

        The settlement rule is now CONFIRMED from the live contract: it resolves
        to "the temperature range that contains the high", i.e. truncation/floor
        (a 28.6°C high settles the 28°C bucket). bucket_for_high commits to that
        floor rule. This diagnostic still reports whether the OTHER reading of the
        labels — round-half-up — would land the same bucket: it returns
        (robust, nearest_bucket, truncate_bucket) where truncate_bucket is the one
        actually settled and nearest_bucket is the round-half-up alternative.
        Disagreement is the honest red flag that the verdict sits close enough to
        an integer edge that the two rules diverge (fragile); agreement means the
        gap is immaterial. None for whole-degree markets, where the reading is
        already integral so floor == round. Native °F is handled in-unit."""
        if not self.settles_sub_degree():
            return None
        native = verdict_high_c * 9 / 5 + 32 if self.grain == "F" else verdict_high_c
        nearest = _round_half_up(native)        # round-half-up: the alternative label reading
        truncate = math.floor(native)           # floor / range-containment: the CONFIRMED rule
        near_b = self._bucket_label_for_reading(nearest)
        trunc_b = self._bucket_label_for_reading(truncate)
        return (near_b == trunc_b, near_b, trunc_b)

    def _to_c(self, native_value: float) -> float:
        return (native_value - 32) * 5 / 9 if self.grain == "F" else native_value


def _loads_list(raw) -> list:
    """Gamma encodes these fields as JSON strings; decode to a list or []."""
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, str) or not raw:
        return []
    try:
        val = json.loads(raw)
    except ValueError:
        return []
    return val if isinstance(val, list) else []


def _price(raw) -> float | None:
    try:
        p = float(raw)
    except (TypeError, ValueError):
        return None
    return p if 0.0 <= p <= 1.0 else None


def _num(raw) -> float | None:
    """A non-negative USDC notional (volume/liquidity). Unlike `_price` this is
    not clamped to [0,1] — it can be thousands. Negatives/garbage become None."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if v >= 0.0 else None


def _parse_bucket(m: dict) -> MarketBucket | None:
    outcomes = _loads_list(m.get("outcomes"))
    prices = _loads_list(m.get("outcomePrices"))
    tokens = tuple(str(t) for t in _loads_list(m.get("clobTokenIds")))
    yes = no = None
    if len(outcomes) == len(prices):
        for name, p in zip(outcomes, prices):
            n = str(name).strip().lower()
            if n == "yes":
                yes = _price(p)
            elif n == "no":
                no = _price(p)
    label = (m.get("groupItemTitle") or m.get("question") or "").strip()
    if not label:
        return None
    lo, hi = _bucket_edges(label)
    return MarketBucket(
        label=label, yes_price=yes, no_price=no, token_ids=tokens, lo=lo, hi=hi,
        liquidity=_num(m.get("liquidityNum")),
        volume=_num(m.get("volumeNum")),
        volume_24hr=_num(m.get("volume24hr")),
        best_bid=_price(m.get("bestBid")),
        best_ask=_price(m.get("bestAsk")),
        last_trade=_price(m.get("lastTradePrice")),
    )


def _parse_event(e: dict) -> WeatherMarket | None:
    title = str(e.get("title") or "").strip()
    markets = e.get("markets")
    if not title or not isinstance(markets, list):
        return None

    city = date_label = None
    tm = _TITLE_RE.search(title)
    if tm:
        city = tm.group("city").strip()
        date_label = tm.group("date").strip()

    station = None
    grain = "C"
    tenths = False
    source = e.get("resolutionSource") or None
    for m in markets:                      # rules live in each market's description
        desc = m.get("description") or ""
        sm = _STATION_RE.search(desc)
        if sm:
            station = sm.group("station").strip()
            grain = "F" if sm.group("unit").lower() == "fahrenheit" else "C"
            tenths = bool(_DECIMAL_RE.search(desc))
            low = desc.lower()
            if not source:
                if "wunderground" in low:
                    source = "Weather Underground"
                elif "noaa" in low:
                    source = "NOAA"
                elif "observatory" in low:
                    source = station
            break
    precision = f"0.1°{grain}" if tenths else f"whole °{grain}"

    parsed = [b for b in (_parse_bucket(m) for m in markets) if b is not None]
    if not parsed:
        return None
    # Gamma returns buckets in arbitrary order; sort into the temperature ladder
    # (below-tail, ascending interior, above-tail) so consumers see a real CDF.
    def _order(b: MarketBucket) -> float:
        if b.lo is not None:
            return b.lo
        if b.hi is not None:
            return b.hi - 0.5     # an "or below" tail sits just under its edge
        return float("-inf")
    buckets = tuple(sorted(parsed, key=_order))

    return WeatherMarket(
        event_id=str(e.get("id") or ""),
        title=title,
        city=city,
        date_label=date_label,
        station=station,
        grain=grain,
        precision=precision,
        resolution_source=source,
        end_date=(str(e.get("endDate")) if e.get("endDate") else None),
        slug=(str(e.get("slug")) if e.get("slug") else None),
        buckets=buckets,
        volume=_num(e.get("volume")),
        liquidity=_num(e.get("liquidity")),
    )


# --- authoritative settlement resolution ----------------------------------- #
#
# The truth the council scores itself on (its anchor station / ERA5 grid) is a
# PROXY for what the market actually paid out. They are MEANT to coincide (HK ==
# the HKO Observatory abs-daily-max, London == Wunderground EGLC), but "coincided
# yesterday" is not "coincides today" — so the only way to know whether a verdict
# matched the contract is to read the contract's OWN resolved outcome. A settled
# Gamma event carries it directly: the winning bucket's "Yes" outcomePrice is 1.0.
# `fetch_temperature_markets` only pulls open events, so settled outcomes are read
# here by SLUG (the per-day event slug, suffixed with the year).

_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")
_RESOLVED_YES = 0.99   # a settled winner prices at 1.0; allow float slop


def event_slug(city_label: str, target: dt.date, kind: str = "high") -> str:
    """The Gamma event slug for one city/day/attribute, e.g.
    "highest-temperature-in-hong-kong-on-june-12-2026" or
    "lowest-temperature-in-london-on-july-9-2026". `city_label` may be a full
    place label ("Hong Kong, HK"); only the part before the first comma is used.
    `kind` is "high" (default) or "low". NOTE the year suffix — the bare slug
    returns the SAME-day event from a PRIOR year (which can settle in different
    units)."""
    city = city_label.split(",", 1)[0].strip().lower()
    token = re.sub(r"[^a-z0-9]+", "-", city).strip("-")
    superlative = "lowest" if kind == "low" else "highest"
    return (f"{superlative}-temperature-in-{token}-on-"
            f"{_MONTHS[target.month - 1]}-{target.day}-{target.year}")


def resolved_event_slug(city_label: str, target: dt.date) -> str:
    """Back-compat alias: the daily-HIGH event slug (settlement audit uses this)."""
    return event_slug(city_label, target, "high")


def _match_event_by_slug(raw, slug: str):
    """WP-1: the ONE shared exact-slug matcher for `fetch_resolution` and `fetch_market_by_slug`.
    Returns the event dict whose slug EQUALS `slug`, else None. No `raw[0]` fallback — a
    non-matching first event is a DIFFERENT city/day and must never be mistaken for this one."""
    return next((e for e in (raw or [])
                 if isinstance(e, dict) and str(e.get("slug")) == slug), None)


@dataclass(frozen=True)
class Resolution:
    """The contract's OWN settled outcome for one city/day — the authoritative
    answer to "which bucket did the market pay out on", independent of the
    council's proxy truth. `resolved` is True only for a closed event with a
    YES-priced winning bucket; otherwise the day has not finalized."""
    slug: str
    resolved: bool
    winning_label: str | None = None
    winning_lo: int | None = None     # inclusive edges in the native unit
    winning_hi: int | None = None
    grain: str = "C"
    station: str | None = None
    source: str | None = None
    end_date: str | None = None
    # WP-1 (served-number campaign): fail-closed NO_MATCH. no_match=True means the feed returned
    # events but NONE matched the requested slug exactly — a slug/schema-drift alarm, NOT a resolution.
    # near_miss_slugs carries the top-3 candidate slugs for human repair. Distinct from a None return
    # (empty/failed fetch, transient). resolved is always False when no_match is True.
    no_match: bool = False
    near_miss_slugs: tuple = ()

    def contains(self, reading_int: int) -> bool:
        """True iff a native whole-degree reading lands in the winning bucket —
        i.e. a forecast snapping to `reading_int` would have settled YES. False
        for an unresolved day or an unparseable winning label."""
        if not self.resolved or (self.winning_lo is None and self.winning_hi is None):
            return False
        if self.winning_lo is not None and reading_int < self.winning_lo:
            return False
        if self.winning_hi is not None and reading_int > self.winning_hi:
            return False
        return True


class MarketData:
    """Read-only client for Polymarket temperature markets."""

    def __init__(
        self,
        http: SafeHTTPClient | None = None,
        request_budget: int = DEFAULT_MARKET_REQUEST_BUDGET,
    ) -> None:
        self.http = http or SafeHTTPClient()
        self._budget = max(1, request_budget)

    def fetch_temperature_markets(self, max_events: int = 200) -> list[WeatherMarket]:
        """Fetch open daily-high-temperature events and parse them read-only.

        Paginates with `offset`, but never makes more than `request_budget`
        requests, so the market layer cannot exhaust the shared per-run cap that
        the core weather pipeline depends on."""
        out: list[WeatherMarket] = []
        seen: set[str] = set()
        for page in range(self._budget):
            if len(out) >= max_events:
                break
            try:
                raw = self.http.get_json_array(
                    EVENTS_URL,
                    {
                        "closed": "false",
                        "tag_id": HIGHEST_TEMP_TAG,
                        "limit": _PAGE_SIZE,
                        "offset": page * _PAGE_SIZE,
                    },
                )
            except SecurityError:
                raise
            if not raw:
                break
            for e in raw:
                if not isinstance(e, dict):
                    continue
                parsed = _parse_event(e)
                if parsed and parsed.event_id not in seen:
                    seen.add(parsed.event_id)
                    out.append(parsed)
            if len(raw) < _PAGE_SIZE:
                break
        return out[:max_events]

    def fetch_market_by_slug(self, slug: str) -> WeatherMarket | None:
        """Fetch ONE open event by its exact slug and parse it read-only. Used for
        markets the tag-paged `fetch_temperature_markets` does not enumerate — the
        daily-LOW events (`lowest-temperature-in-…`) live under a different tag, so
        they are pulled by their known per-day slug instead. One request; returns
        None when no event matches the slug or the response is unusable."""
        try:
            raw = self.http.get_json_array(EVENTS_URL, {"slug": slug})
        except SecurityError:
            raise
        except Exception:
            return None
        event = _match_event_by_slug(raw, slug)
        return _parse_event(event) if event is not None else None

    def fetch_order_book(self, token_id: str) -> dict | None:
        """Read-only fetch of ONE token's live CLOB order book (bid/ask levels and
        sizes) from the public market-data endpoint. Parse the payload with
        clob_book.parse_book. Returns the raw dict, or None when the token is empty
        or the response is unusable. Market-DATA only: no order is placed, and no
        key or signature is ever sent. A sandbox SecurityError is propagated (like
        the other fetchers) so a real allowlist/SSRF violation is never swallowed
        here; ordinary fetch failures return None for the caller to record."""
        if not token_id:
            return None
        try:
            raw = self.http.get_json(CLOB_BOOK_URL, {"token_id": str(token_id)})
        except SecurityError:
            raise
        except Exception:
            return None
        return raw if isinstance(raw, dict) else None

    def fetch_resolution(self, slug: str) -> Resolution | None:
        """Read one settled event's authoritative outcome by slug. Returns a
        Resolution (resolved=False when the event exists but has not paid out yet)
        or None when no event is found / the response is unusable. One request;
        read-only. Reuses `_parse_event` so the winning bucket's edges, grain,
        station and source are parsed exactly as for live markets — the winner is
        simply the bucket whose Yes price has settled to 1.0."""
        try:
            raw = self.http.get_json_array(EVENTS_URL, {"slug": slug})
        except SecurityError:
            raise
        except Exception:
            return None
        if not raw:
            return None                    # empty/failed fetch — transient, retry (NOT no_match)
        event = _match_event_by_slug(raw, slug)
        if event is None:
            # WP-1 fail-closed: the feed returned events but none match this slug exactly. The old
            # code fell back to raw[0] here and could settle another city/day's contract. Refuse,
            # and surface the top-3 candidate slugs for human repair. A missing settlement is
            # recoverable; a wrong one is poison.
            near = tuple(str(e.get("slug")) for e in raw[:3] if isinstance(e, dict))
            return Resolution(slug=slug, resolved=False, no_match=True, near_miss_slugs=near)
        wm = _parse_event(event)
        if wm is None:
            return None
        winner = next((b for b in wm.buckets
                       if b.yes_price is not None and b.yes_price >= _RESOLVED_YES),
                      None)
        resolved = bool(event.get("closed")) and winner is not None
        return Resolution(
            slug=slug, resolved=resolved,
            winning_label=winner.label if winner else None,
            winning_lo=winner.lo if winner else None,
            winning_hi=winner.hi if winner else None,
            grain=wm.grain, station=wm.station,
            source=wm.resolution_source, end_date=wm.end_date)
