"""Intraday-ceiling bucket sharpening — the lead-0 conviction lever.

Day-ahead bucket conviction is capped by information, not effort: the forecast
residual sigma (~0.6 °C London, ~1.0 °C HK) is the size of a whole-degree bucket,
so even a perfectly centred, perfectly calibrated forecast can only name the exact
settling bucket with probability P(|N(0,sigma)|<0.5) — ~60% for London, ~38% for
HK. The system already sits at that ceiling day-ahead. The ONLY honest way past it
is to shrink sigma, and on the settlement day itself the running maximum does
exactly that: once the daily peak is near, the final max is almost determined.

This module turns the live running-max-so-far plus an empirically learned
REMAINING-RISE distribution into a sharpened bucket pmf for the final daily max.
The remaining rise at a given hour is the empirical distribution of
(final_daily_max − running_max_so_far) over STRICTLY EARLIER days, resampled
through the same settlement quantizer the market pays out on (round-half-up for
London, floor for HK). It is therefore leak-free and non-parametric.

Validated on London EGLC hourly METAR (the settlement instrument's OWN record):
held-out exact-bucket hit climbs 16% (09:00) → 30% (12:00) → 89% (15:00) → 99%
(18:00), vs ~56% day-ahead and 2.5% climatology, sign-stable on disjoint folds
(reproduce with tools/intraday_ceiling_backtest.py).

Applicability. London (EGLC, IEM hourly), Manila (RPLL, IEM hourly) and Singapore
(WSSS — WU-NATIVE: running max, rises and settlement all read the market's own
Wunderground feed, plus the v3 live-register floor consult on live runs) each have
a settlement-grade hourly record. Hong Kong settles on the HKO daily maximum only
(no hourly record) so HK ABSTAINS (kind="unavailable"). READ-ONLY and TODAY-only:
never moves the day-ahead verdict; every live read is logged to the certification
ledger, and a pre-sunset read is a banked FLOOR, never "final".
"""

from __future__ import annotations

__all__ = ['IntradayCeiling', 'remaining_rise_samples', 'sharpen_pmf',
           'intraday_ceiling', 'banked_vs_leading', 'peak_close_hour_from_history']

import datetime as dt
import re
from dataclasses import dataclass, field

from .market import _native_reading_int
from .sources import Place, Sources, _fuse_live_floor, place_today

# A rise distribution needs enough strictly-earlier days before its sharpened pmf
# is trustworthy; below this we abstain rather than quote a tiny-sample pmf.
MIN_RISE_SAMPLES = 20
# Default lookback for learning the remaining-rise distribution.
DEFAULT_BACK_DAYS = 160


@dataclass(frozen=True)
class IntradayCeiling:
    """Sharpened final-max bucket pmf for one city/day, conditioned on the day's
    running max so far.

    kind:
      * "sharpened"   — a running max and a sufficient rise sample exist; `pmf`
                        is the settlement-bucket distribution and `modal_bucket`
                        the most likely outcome.
      * "unavailable" — no settlement-grade hourly record (HK), feed error, empty
                        obs, or too few rise samples; nothing sharpened, said loudly.
      * "not_today"   — target is not the city-local current day.
      * "not_basket"  — city is not a configured settlement city.
    """
    kind: str
    city: str
    target: str
    sub_degree: bool
    grain: str = "C"          # settlement unit: "C" (SG/London/Manila) or "F" (San Francisco)
    hour: int | None = None
    running_max_c: float | None = None
    n_rise: int = 0
    pmf: tuple[tuple[int, float], ...] = field(default_factory=tuple)
    modal_bucket: int | None = None
    modal_prob: float | None = None
    source: str | None = None
    note: str | None = None
    # Live-register evidence (v3 current feed; floor-raise-only — see sources._fuse_live_floor).
    live_cur_f: float | None = None
    live_max24_f: float | None = None
    feed: str = "v1"
    # BANKED-vs-LEADING split (2026-07-12 Karachi vocabulary fix). `running_max_c` may include a
    # live v3 cur_f that LEADS the settlement record (intended — Jeddah 07-09/07-11). But a cur_f
    # lead is NOT "banked" (observation-grade, 100%, can-never-go-down) until a settlement ob or
    # the WU daily-max endpoint corroborates it — it can fail to materialise on the record (the
    # London 07-11 over-read). `banked_running_max_c` is the observation-grade floor actually
    # present in an ob: max(obs-only running max, WU daily-max endpoint in °C). `wu_daily_max_f`
    # is that endpoint (°F). See feedback_market_leads_lagging_wu_endpoint.md; split via
    # banked_vs_leading(). Labels only — never moves the served pmf/modal/running_max.
    banked_running_max_c: float | None = None
    wu_daily_max_f: float | None = None
    # Grade-resolver inputs (2026-07-12 Karachi H2/H3): the endpoint's own obs-count (a growing
    # n with a rising max_f = the settlement surface actively catching a between-obs peak), the
    # v3 current-conditions obs timestamp (a FROZEN valid_local across reads = the London 07-11
    # stale-cur_f over-read; a refreshing one = a live lead), and the archive's own leak-free
    # peaked-by-q0.95 hour (computed from the SAME strictly-earlier history as the rise pmf —
    # replaces the hand-written peak-clock fallback map). All labels-only.
    wu_daily_max_n: int | None = None
    live_valid_local: str | None = None
    peak_close_hour: float | None = None
    # Peak-formed state (probe 2026-07-04, fold-stable): a day still AT its max ("holding")
    # carries ~13.5% bucket-raise risk after 15:00 vs ~0.8% once "declining" — the honest
    # NOT-FINAL risk is state-conditional. Risk-LABELING only; never moves modal/pmf.
    day_state: str | None = None
    state_late_risk: float | None = None

    @property
    def is_sharpened(self) -> bool:
        return self.kind == "sharpened"


def banked_vs_leading(ceiling: "IntradayCeiling") -> dict | None:
    """Split the live-fused running-max floor into its OBSERVATION-GRADE banked bucket and,
    when a live v3 cur_f alone leads beyond it, the UNCORROBORATED leading bucket — the
    vocabulary guard for the 2026-07-11 London / 2026-07-12 Karachi over-read failure mode
    (feedback_market_leads_lagging_wu_endpoint.md).

    The BANKED floor is the highest settlement bucket actually PRESENT in a settlement ob:
    the bucket of max(obs-only running max, WU daily-max endpoint). It is observation-grade —
    it can never go down. The LED bucket is the bucket of `running_max_c`, which may have been
    pushed a bucket higher by a live cur_f the lagging endpoint has not yet caught up to. That
    cur_f LEAD is intended (it correctly leads a stale v1 endpoint — Jeddah 07-09/07-11), but
    until a settlement ob or the daily-max endpoint corroborates it, it is NOT banked: it can
    fail to land on the settlement record.

    Returns None when there is no distinct banked figure to draw (no live fusion, or missing
    inputs) so callers keep the plain running-max wording. Otherwise returns
    {banked_bucket, banked_c, led_bucket, uncorroborated_lead, cur_f, endpoint_f, grain} —
    `uncorroborated_lead` is True exactly when the led bucket sits above the banked bucket.
    Pure/labels only: it reads the ceiling, moves no served number."""
    if ceiling is None:
        return None
    rm = ceiling.running_max_c
    banked_c = ceiling.banked_running_max_c
    if rm is None or banked_c is None:
        return None
    grain = getattr(ceiling, "grain", "C") or "C"
    sub = bool(ceiling.sub_degree)
    banked_bucket = _native_reading_int(banked_c, grain, sub)
    led_bucket = _native_reading_int(rm, grain, sub)
    return {
        "banked_bucket": banked_bucket,
        "banked_c": banked_c,
        "led_bucket": led_bucket,
        "uncorroborated_lead": led_bucket > banked_bucket,
        "cur_f": ceiling.live_cur_f,
        "endpoint_f": ceiling.wu_daily_max_f,
        "grain": grain,
    }


def _running_max(obs: list[tuple[int, float]], hour: int) -> float | None:
    """Max temperature among observations recorded by `hour` (inclusive)."""
    vals = [c for (hh, c) in obs if hh <= hour]
    return max(vals) if vals else None


def _final_max(obs: list[tuple[int, float]]) -> float | None:
    vals = [c for (_hh, c) in obs]
    return max(vals) if vals else None


def _day_state(obs, hour, delta=0.3):
    """PURE: has today's peak demonstrably formed by `hour`? "declining" only once the
    peak-passed signal PERSISTS — the last TWO readings both sit >delta below the running
    max. A single-read decline is a FALSE peak-passed signal on 16-30% of July days (the
    07-04 EGLC trap); requiring persistence thirds it for +30min median delay — CERTIFIED
    2026-07-06 on the frozen gate (persistent_decline_lock.md: reliability 80.7->91.6 /
    78.2->89.8 EGLC, 75.7->88.3 / 75.9->89.5 WSSS, all four half-cells PASS). "holding"
    otherwise (still at/near the max — the peak may not have happened yet). None without
    data. delta=0.3C ~= within one deg-F tick."""
    prior = [(hh, c) for hh, c in sorted(obs) if hh <= hour]
    rm = _running_max(obs, hour)
    if not prior or rm is None:
        return None
    below = [c < rm - delta for _, c in prior[-2:]]
    return "declining" if len(below) == 2 and all(below) else "holding"


def state_late_risk(history, hour, state, sub_degree, min_n=20, month=None, grain="C"):
    """PURE, leak-free: over strictly-earlier days in `state` at `hour`, the empirical rate of
    the SETTLED bucket ending above the running-max bucket — the state-conditional raise risk
    behind the NOT-FINAL line. With `month` given, conditions on the (state x meteorological
    season) cell when it holds n>=30 prior days — CERTIFIED 2026-07-06 on the frozen gate
    (lock_state_season_calibration.md: Brier beats the blended rate on BOTH halves BOTH
    cities, +6.4..14.1%); thinner cells fall back to the state-only rate (n>=min_n), else
    None. History keys are ISO dates; undated keys simply skip the season cell."""
    sea = (month % 12) // 3 if month is not None else None
    cell, st_only = [], []
    for day, obs in history.items():
        if _day_state(obs, hour) != state:
            continue
        rm, fm = _running_max(obs, hour), _final_max(obs)
        if rm is None or fm is None:
            continue
        up = _native_reading_int(fm, grain, sub_degree) > _native_reading_int(rm, grain, sub_degree)
        st_only.append(up)
        if (sea is not None and len(day) >= 7 and day[5:7].isdigit()
                and (int(day[5:7]) % 12) // 3 == sea):
            cell.append(up)
    if sea is not None and len(cell) >= 30:
        return sum(cell) / len(cell)
    return (sum(st_only) / len(st_only)) if len(st_only) >= min_n else None


def peak_close_hour_from_history(history: dict[str, list[tuple[float, float]]],
                                 q: float = 0.95) -> float | None:
    """Data-derived, leak-free peak-close hour: for each STRICTLY-EARLIER day, the earliest
    hour at which the running max first equals the day's final max (the peak-attained hour);
    return the q-quantile across days. A current hour strictly greater than this means >=q of
    days had already peaked by now, so a new daily max is rare. Returns None on empty history
    (the grade then refuses to call the peak window closed — never lock on an unknown clock).
    No magic constant: the number is the archive's own peaked-by distribution, from the SAME
    history dict the remaining-rise pmf is learned from."""
    import math
    peaked_at: list[float] = []
    for obs in history.values():
        fm = _final_max(obs)
        if fm is None:
            continue
        hrs = sorted(hh for hh, c in obs if c >= fm - 1e-9)
        if hrs:
            peaked_at.append(hrs[0])
    if not peaked_at:
        return None
    peaked_at.sort()
    idx = min(len(peaked_at) - 1, max(0, math.ceil(q * len(peaked_at)) - 1))
    return peaked_at[idx]


def remaining_rise_samples(history: dict[str, list[tuple[int, float]]],
                           now_hour: int) -> list[float]:
    """Empirical (final_daily_max − running_max_by_now_hour) over the supplied
    earlier days. Each value is >= 0 by construction (the final max can only be at
    or above the running max). `history` must contain ONLY strictly-earlier days —
    the caller guarantees leak-freedom. Days with no observation by `now_hour` are
    skipped (they contribute no usable conditional sample)."""
    out: list[float] = []
    for obs in history.values():
        rm = _running_max(obs, now_hour)
        fm = _final_max(obs)
        if rm is not None and fm is not None:
            out.append(fm - rm)
    return out


def sharpen_pmf(running_max_c: float, rise_samples: list[float],
                sub_degree: bool, grain: str = "C") -> list[tuple[int, float]]:
    """The settlement-bucket pmf for the final daily max: resample the empirical
    remaining-rise cloud onto today's running max and quantize each draw through
    the market's own rule (round-half-up whole-°C for London/SG, round-half-up
    whole-°F for San Francisco, floor for HK). `running_max_c` is always °C;
    `_native_reading_int` converts to the settlement `grain` before quantizing.
    Deterministic (a full resample over every sample, not a seeded MC), non-
    parametric (keeps any skew in the rise), and monotone-safe (every draw >=
    running max, so no bucket below the running max's bucket can be produced).
    Returns (bucket, prob) sorted by descending probability."""
    counts: dict[int, int] = {}
    for r in rise_samples:
        b = _native_reading_int(running_max_c + r, grain, sub_degree)
        counts[b] = counts.get(b, 0) + 1
    n = len(rise_samples)
    return sorted(((b, c / n) for b, c in counts.items()), key=lambda t: -t[1])


# Cities whose settlement airport has an hourly archive -> the intraday-rise lever applies
# (icao, tz, sub_degree, name). All six settle on an airport record; the lever needs the hourly
# feed to build today's running max + remaining-rise pmf.
_HOURLY_STATION = {
    "london": ("EGLC", "Europe/London", False, "London City Airport"),
    "manila": ("RPLL", "Asia/Manila", False, "Ninoy Aquino Intl"),
    "singapore": ("WSSS", "Asia/Singapore", False, "Changi"),
    "san francisco": ("KSFO", "America/Los_Angeles", False, "San Francisco Intl"),
    "karachi": ("OPKC", "Asia/Karachi", False, "Jinnah Intl"),
    "jeddah": ("OEJN", "Asia/Riyadh", False, "King Abdulaziz Intl"),
}
_NO_HOURLY = {"hong kong": True}    # settles on a daily-max-only record (no hourly)
# Settlement UNIT per city: whole °C everywhere except San Francisco, which settles
# (and its 2°F Polymarket buckets pay out) in whole °F. The running max is always
# carried in °C; the quantizer converts to this grain before bucketing.
_SETTLE_GRAIN = {"san francisco": "F"}    # default "C"
# Cities whose intraday lever reads the WUNDERGROUND settlement feed (whole °F)
# instead of IEM METAR (whole °C) — running max, remaining-rise and settled bucket
# then live on the SAME feed the market pays out on. IEM's coarser °C grain hides
# the °F boundary fragility (Singapore 14:00: IEM 91% vs WU-faithful 78%), so a
# WU-native lever reports honest, settlement-correct conviction. Validated stable
# via reports/_wu_native_intraday_sg.py.
_WU_INTRADAY = {"singapore", "san francisco"}
# Cities that consult the live WU v3 current/24h-register (floor-raise-only) — SEPARATE from
# which hourly feed backs the running max. London's hourly running-max BACKBONE reads IEM
# whole-°C, but that feed is coarse and misses between-obs peaks, whereas London SETTLES on WU
# (EGLC ∈ storage._WU_SETTLE_TZ): 07-07 EGLC hourly topped at 88°F (31°C) while the WU register
# caught 90°F and the market SETTLED 32 — our lock said 31 purely because London was excluded
# from this consult. The current reading is a real station value; fusing it closes the whole-°C
# undershoot at the °F boundary. (Register still gated vs yesterday inside _fuse_live_floor.)
_LIVE_REGISTER = {"singapore", "london", "san francisco", "karachi", "jeddah"}


def _city_key(place: Place) -> str | None:
    name = (getattr(place, "name", "") or "").strip().lower()
    keys = (*_HOURLY_STATION, *_NO_HOURLY)
    # Exact match first (the common case).
    for key in keys:
        if key == name:
            return key
    # Phrase-boundary match for decorated forms like "London, GB" or
    # "San Francisco, US". Substring and prefix-into-longer-phrase matches are
    # forbidden: "Londonderry", "Manilal", and "San Francisco de Macorís" must
    # not inherit settlement config from a configured city.
    for key in keys:
        if re.search(r"\b" + re.escape(key) + r"\b(?!\s+[a-z])", name):
            return key
    return None


def intraday_ceiling(place: Place, target: dt.date, *,
                     sources: Sources | None = None,
                     today: dt.date | None = None,
                     now_hour: int | None = None,
                     back_days: int = DEFAULT_BACK_DAYS) -> IntradayCeiling:
    """Compute the read-only intraday-ceiling sharpening for one city/day.

    Only applies to the current city-local day (lead 0) and only to a city whose
    settlement instrument has an hourly archive (London EGLC). HK and any other
    city return kind="unavailable"/"not_basket". `now_hour` overrides the
    evaluation hour (defaults to the latest observed hour today); `today` overrides
    the city-local date (tests/determinism).
    """
    city = getattr(place, "name", "") or "?"
    tgt_iso = target.isoformat()
    key = _city_key(place)
    if key is None:
        return IntradayCeiling(kind="not_basket", city=city, target=tgt_iso,
                               sub_degree=False,
                               note="not a configured settlement city")

    local_today = today if today is not None else place_today(place)
    if target != local_today:
        when = "the future" if target > local_today else "already settled"
        return IntradayCeiling(
            kind="not_today", city=city, target=tgt_iso,
            sub_degree=key in _NO_HOURLY,
            note=(f"target {tgt_iso} is {when}; intraday sharpening is current-day "
                  f"only (city-local today is {local_today.isoformat()})"))

    if key in _NO_HOURLY:
        return IntradayCeiling(
            kind="unavailable", city=city, target=tgt_iso, sub_degree=True,
            note=("Hong Kong settles on the HKO Observatory daily maximum; there "
                  "is no settlement-grade hourly record to learn or validate a "
                  "remaining-rise from, so no intraday sharpening is served"))

    icao, tz, sub_degree, station_name = _HOURLY_STATION[key]
    grain = _SETTLE_GRAIN.get(key, "C")     # "F" for San Francisco, "C" otherwise
    if sources is None:
        return IntradayCeiling(kind="unavailable", city=city, target=tgt_iso,
                               sub_degree=sub_degree, grain=grain,
                               note="no Sources handle; cannot fetch hourly history")

    use_wu = key in _WU_INTRADAY
    try:
        if use_wu:
            obs = sources.wunderground_hourly_observations(
                icao, target - dt.timedelta(days=back_days),
                target + dt.timedelta(days=1), tz)
        else:
            obs = sources.fetch_metar_observations(
                icao, target - dt.timedelta(days=back_days),
                target + dt.timedelta(days=1), tz)
    except Exception as exc:
        return IntradayCeiling(kind="unavailable", city=city, target=tgt_iso,
                               sub_degree=sub_degree,
                               note=f"hourly feed errored: {exc}")

    # WU is sub-hourly (~30 min) -> keep fractional hours so "by H:00" excludes the
    # :30 reading (a leak otherwise); IEM stays integer-hour as before.
    by_date: dict[str, list[tuple[float, float]]] = {}
    for ts, c in obs:
        hh = int(ts[11:13]) + (int(ts[14:16]) / 60.0 if use_wu else 0)
        by_date.setdefault(ts[:10], []).append((hh, c))

    todays = by_date.get(tgt_iso, [])
    if not todays:
        return IntradayCeiling(kind="unavailable", city=city, target=tgt_iso,
                               sub_degree=sub_degree, source=icao,
                               note="no observations recorded yet on the target day")

    hour = now_hour if now_hour is not None else max(hh for hh, _ in todays)
    running_max = _running_max(todays, hour)
    day_state = _day_state(todays, hour)        # obs-only, BEFORE any register fusion

    # BANKED (observation-grade) floor: the highest settlement bucket actually PRESENT in an ob.
    # It starts at today's obs-only running max and is only ever widened by another real ob (the
    # WU daily-max endpoint, below) — NEVER by a live cur_f/register lead. `running_max` may climb
    # above this via a cur_f that leads the lagging endpoint (intended), but that lead is not
    # banked until the record catches up (feedback_market_leads_lagging_wu_endpoint.md).
    banked_running_max = running_max
    wu_daily_max_f = None

    # LIVE-REGISTER CONSULT (WU cities, floor-raise only): the v1 history rows lag ~30-45min
    # and miss between-obs spikes; the oracle's own v3 current feed + 24h register is the
    # freshest read of the SAME instrument. Fused through sources._fuse_live_floor (current
    # always counts; the register only when it exceeds yesterday's max). 07-04: rows said 91°F
    # while the register read 92 — this consult closes exactly that gap. Failure => no-op.
    live_cur = live_max24 = None
    feed = "v1"
    live_note = None
    wu_daily_max_n = live_valid = None
    if key in _LIVE_REGISTER and now_hour is None:   # live runs only — replays/backtests stay v1
        try:
            live = sources.wunderground_current_v3(icao)
            if live is not None:
                live_cur, live_max24 = live["cur_f"], live["max24_f"]
                live_valid = live.get("valid_local")   # frozen across reads = stale cur_f (07-11)
                yday = (target - dt.timedelta(days=1))
                yrow = sources.wunderground_daily_series(icao, yday, yday, tz).get(yday.isoformat())
                # WU's OWN authoritative daily-max caps the register (phantom guard, Jeddah
                # 2026-07-09): a max24 above the settlement record's own daily high is not real.
                try:
                    dmax = sources.wunderground_daily_max(icao, target, tz)   # WP-2: local-day max
                    wu_daily_max_f = dmax.get("max_f") if dmax else None
                    wu_daily_max_n = dmax.get("n_obs") if dmax else None
                except Exception:
                    wu_daily_max_f = None
                # WP-3: on a daily-max endpoint outage, yesterday's peak (°F) is the recent-daily-max
                # fallback cap so the phantom cap degrades explicitly instead of vanishing.
                yday_cap_f = (yrow[0] * 9.0 / 5.0 + 32.0) if yrow else None
                fused, live_note = _fuse_live_floor(running_max, live_cur, live_max24,
                                                    yrow[0] if yrow else None,
                                                    wu_record_max_f=wu_daily_max_f,
                                                    cap_fallback_f=yday_cap_f)
                if fused is not None and (running_max is None or fused > running_max):
                    running_max = fused
                # The daily-max endpoint IS a settlement ob (it aggregates real between-obs peaks),
                # so it CORROBORATES the banked floor; a cur_f/register lead above it does not.
                if wu_daily_max_f is not None:
                    endpoint_c = (wu_daily_max_f - 32.0) * 5.0 / 9.0
                    if banked_running_max is None or endpoint_c > banked_running_max:
                        banked_running_max = endpoint_c
                feed = "wu+live"
        except Exception as exc:
            from .failures import record_soft_failure
            record_soft_failure("ceiling_register_consult", exc)   # swallow stays; not silent

    history = {d: o for d, o in by_date.items() if d < tgt_iso}
    rises = remaining_rise_samples(history, hour)
    if running_max is None or len(rises) < MIN_RISE_SAMPLES:
        return IntradayCeiling(
            kind="unavailable", city=city, target=tgt_iso, sub_degree=sub_degree,
            grain=grain, source=icao, hour=hour, running_max_c=running_max,
            n_rise=len(rises),
            note=(f"insufficient rise history at {int(hour):02d}:00 "
                  f"({len(rises)} < {MIN_RISE_SAMPLES} days)"))

    pmf = sharpen_pmf(running_max, rises, sub_degree, grain)
    modal_b, modal_p = pmf[0]
    s_risk = (state_late_risk(history, hour, day_state, sub_degree,
                              month=target.month, grain=grain)
              if day_state is not None else None)
    return IntradayCeiling(
        kind="sharpened", city=city, target=tgt_iso, sub_degree=sub_degree,
        grain=grain, hour=hour, running_max_c=running_max, n_rise=len(rises),
        pmf=tuple(pmf), modal_bucket=modal_b, modal_prob=modal_p,
        day_state=day_state, state_late_risk=s_risk,
        live_cur_f=live_cur, live_max24_f=live_max24, feed=feed,
        banked_running_max_c=banked_running_max, wu_daily_max_f=wu_daily_max_f,
        wu_daily_max_n=wu_daily_max_n, live_valid_local=live_valid,
        peak_close_hour=peak_close_hour_from_history(history),
        source=(f"{station_name} {icao} "
                + (f"(live Wunderground hourly, whole-°F → settlement °{grain})" if use_wu
                   else "(live IEM ASOS METAR, hourly)")
                + (f" + {live_note}" if live_note else "")))


def _self_test() -> None:
    """Oracle: on a synthetic record where the daily peak always lands by 14:00,
    sharpening AT/AFTER the peak must concentrate almost all mass on the true
    settling bucket (sigma collapses), while sharpening early must stay diffuse —
    proving the lever reflects real information gain, not a manufactured edge."""
    import random
    rng = random.Random(11)
    # 60 synthetic days. The morning max is only loosely tied to the peak: the
    # remaining rise from 09:00 to the 14:00 peak VARIES day to day (3–8 °C), so an
    # early forecast is genuinely uncertain while the post-peak running max is not.
    history: dict[str, list[tuple[int, float]]] = {}
    for i in range(60):
        morning = round(rng.uniform(14.0, 18.0), 1)     # running max by 09:00
        rise = round(rng.uniform(3.0, 8.0), 1)          # variable rise to the peak
        peak = round(morning + rise, 1)
        obs = [(6, morning - 1.0), (9, morning), (12, morning + 0.6 * rise),
               (14, peak), (16, peak - 0.2), (18, peak - 0.8)]
        history[f"2026-04-{i+1:02d}"] = obs

    # Late (by 14:00) the running max IS the peak -> rise ~ 0 -> pmf nails it.
    late = remaining_rise_samples(history, 14)
    assert max(late) <= 0.2 + 1e-9, "post-peak rise must be ~0"
    pmf_late = sharpen_pmf(22.0, late, sub_degree=False)
    assert pmf_late[0][0] == 22 and pmf_late[0][1] >= 0.95, pmf_late[:3]

    # Early (by 09:00) a large, variable rise remains -> diffuse pmf.
    early = remaining_rise_samples(history, 9)
    assert min(early) >= 3.0, "pre-peak rise must be large"
    pmf_early = sharpen_pmf(16.0, early, sub_degree=False)
    assert pmf_early[0][1] < pmf_late[0][1], "early pmf must be less sharp than late"

    # Floor vs round: a sub-degree (HK-style) draw of 28.6 floors to 28, not 29.
    assert sharpen_pmf(28.6, [0.0], sub_degree=True)[0][0] == 28
    assert sharpen_pmf(21.6, [0.0], sub_degree=False)[0][0] == 22

    # Monotone safety: no produced bucket sits below the running max's own bucket.
    rm = 21.4
    lo_bucket = _native_reading_int(rm, "C", False)
    assert all(b >= lo_bucket for b, _ in sharpen_pmf(rm, [0.0, 0.4, 1.1, 2.3], False))

    print("intraday_ceiling self-test PASSED (post-peak pmf concentrates ≥0.95 on "
          "the true bucket; pre-peak stays diffuse; floor vs round correct; no "
          "bucket below the running max — real information gain, not a manufactured edge)")


if __name__ == "__main__":
    _self_test()
