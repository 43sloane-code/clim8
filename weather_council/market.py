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

import json
import re
from dataclasses import dataclass

from .security import SafeHTTPClient, SecurityError
from .sources import _round_half_up  # one shared settlement-rounding convention

EVENTS_URL = "https://gamma-api.polymarket.com/events"
# Gamma tag for the daily city high-temperature markets that settle on a named
# airport station — the markets this project can actually speak to.
HIGHEST_TEMP_TAG = 104596

# Leave headroom under the per-run request budget for the weather pipeline; the
# market layer is a side comparison and must never starve the core verdict.
DEFAULT_MARKET_REQUEST_BUDGET = 4
_PAGE_SIZE = 100

_TITLE_RE = re.compile(r"highest temperature in (?P<city>.+?) on (?P<date>.+?)\?*$", re.I)
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


def _native_reading_int(value_c: float, grain: str) -> int:
    """The whole-degree reading a record settles on: convert °C to the market's
    native unit and round half-up — the same convention the council's settlement
    block uses, so bucket matching and the settlement bucket can never disagree."""
    return _round_half_up(value_c * 9 / 5 + 32 if grain == "F" else value_c)


@dataclass(frozen=True)
class MarketBucket:
    """One whole-degree outcome of a city/day temperature event."""
    label: str                  # e.g. "19°C", "13°C or below"
    yes_price: float | None     # market-implied P(high lands in this bucket)
    no_price: float | None
    token_ids: tuple[str, ...]
    lo: int | None              # inclusive lower edge, native unit (None = open)
    hi: int | None              # inclusive upper edge, native unit (None = open)

    def contains(self, reading_int: int) -> bool:
        if self.lo is None and self.hi is None:
            return False        # unparseable label never matches
        if self.lo is not None and reading_int < self.lo:
            return False
        if self.hi is not None and reading_int > self.hi:
            return False
        return True


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
        """The whole-degree, native-unit reading this verdict would settle as.
        Only meaningful for whole-degree markets; see settles_sub_degree()."""
        return _native_reading_int(verdict_high_c, self.grain)

    def bucket_for_high(self, verdict_high_c: float) -> MarketBucket | None:
        """Which bucket a continuous °C high verdict settles into, by rounding it
        to the market's native whole-degree reading and finding the containing
        range. Returns None if no bucket matches (shouldn't happen for a
        well-formed contiguous ladder)."""
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
        unit = 5 / 9 if self.grain == "F" else 1.0
        # An edge sits halfway above the top / below the bottom integer of the
        # bucket (round-half-up boundary), in native units, converted to °C.
        dists = []
        if b.hi is not None:
            hi_edge_c = self._to_c(b.hi + 0.5)
            dists.append(abs(hi_edge_c - verdict_high_c))
        if b.lo is not None:
            lo_edge_c = self._to_c(b.lo - 0.5)
            dists.append(abs(verdict_high_c - lo_edge_c))
        return min(dists) if dists else None

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
        label=label, yes_price=yes, no_price=no, token_ids=tokens, lo=lo, hi=hi
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
    )


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
