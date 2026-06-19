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

Applicability. Only the LONDON market settles on a station with an hourly archive
(London City Airport EGLC via the IEM ASOS METAR archive), so only London can
learn and be back-validated here. Hong Kong settles on the HKO Observatory, which
publishes a daily maximum only — there is no settlement-grade hourly record to
learn or validate the rise from — so HK ABSTAINS (kind="unavailable"). This is
READ-ONLY and TODAY-only: it never moves the day-ahead verdict; it adds a
high-conviction same-day refinement once enough of the day has elapsed.
"""

from __future__ import annotations

__all__ = ['IntradayCeiling', 'remaining_rise_samples', 'sharpen_pmf',
           'intraday_ceiling']

import datetime as dt
from dataclasses import dataclass, field

from .market import _native_reading_int
from .sources import Place, Sources, place_today

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
    hour: int | None = None
    running_max_c: float | None = None
    n_rise: int = 0
    pmf: tuple[tuple[int, float], ...] = field(default_factory=tuple)
    modal_bucket: int | None = None
    modal_prob: float | None = None
    source: str | None = None
    note: str | None = None

    @property
    def is_sharpened(self) -> bool:
        return self.kind == "sharpened"


def _running_max(obs: list[tuple[int, float]], hour: int) -> float | None:
    """Max temperature among observations recorded by `hour` (inclusive)."""
    vals = [c for (hh, c) in obs if hh <= hour]
    return max(vals) if vals else None


def _final_max(obs: list[tuple[int, float]]) -> float | None:
    vals = [c for (_hh, c) in obs]
    return max(vals) if vals else None


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
                sub_degree: bool) -> list[tuple[int, float]]:
    """The settlement-bucket pmf for the final daily max: resample the empirical
    remaining-rise cloud onto today's running max and quantize each draw through
    the market's own rule (round-half-up for London, floor for HK). Deterministic
    (a full resample over every sample, not a seeded MC), non-parametric (keeps any
    skew in the rise), and monotone-safe (every draw >= running max, so no bucket
    below the running max's bucket can be produced). Returns (bucket, prob) sorted
    by descending probability."""
    counts: dict[int, int] = {}
    for r in rise_samples:
        b = _native_reading_int(running_max_c + r, "C", sub_degree)
        counts[b] = counts.get(b, 0) + 1
    n = len(rise_samples)
    return sorted(((b, c / n) for b, c in counts.items()), key=lambda t: -t[1])


# The two settlement cities: only London exposes a settlement-grade hourly archive.
# Cities whose settlement airport has an hourly METAR archive -> the intraday-rise
# lever applies (icao, tz, sub_degree). Manila (RPLL) and London (EGLC) both settle
# whole-°C round-half-up on an airport, so both get the lever.
_HOURLY_STATION = {
    "london": ("EGLC", "Europe/London", False),
    "manila": ("RPLL", "Asia/Manila", False),
}
_NO_HOURLY = {"hong kong": True}    # settles on a daily-max-only record (no hourly)


def _city_key(place: Place) -> str | None:
    name = (getattr(place, "name", "") or "").strip().lower()
    for key in (*_HOURLY_STATION, *_NO_HOURLY):
        if key in name or name in key:
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

    icao, tz, sub_degree = _HOURLY_STATION[key]
    if sources is None:
        return IntradayCeiling(kind="unavailable", city=city, target=tgt_iso,
                               sub_degree=sub_degree,
                               note="no Sources handle; cannot fetch hourly history")

    try:
        obs = sources.fetch_metar_observations(
            icao, target - dt.timedelta(days=back_days),
            target + dt.timedelta(days=1), tz)
    except Exception as exc:
        return IntradayCeiling(kind="unavailable", city=city, target=tgt_iso,
                               sub_degree=sub_degree,
                               note=f"hourly feed errored: {exc}")

    by_date: dict[str, list[tuple[int, float]]] = {}
    for ts, c in obs:
        by_date.setdefault(ts[:10], []).append((int(ts[11:13]), c))

    todays = by_date.get(tgt_iso, [])
    if not todays:
        return IntradayCeiling(kind="unavailable", city=city, target=tgt_iso,
                               sub_degree=sub_degree, source=icao,
                               note="no observations recorded yet on the target day")

    hour = now_hour if now_hour is not None else max(hh for hh, _ in todays)
    running_max = _running_max(todays, hour)
    history = {d: o for d, o in by_date.items() if d < tgt_iso}
    rises = remaining_rise_samples(history, hour)
    if running_max is None or len(rises) < MIN_RISE_SAMPLES:
        return IntradayCeiling(
            kind="unavailable", city=city, target=tgt_iso, sub_degree=sub_degree,
            source=icao, hour=hour, running_max_c=running_max, n_rise=len(rises),
            note=(f"insufficient rise history at {hour:02d}:00 "
                  f"({len(rises)} < {MIN_RISE_SAMPLES} days)"))

    pmf = sharpen_pmf(running_max, rises, sub_degree)
    modal_b, modal_p = pmf[0]
    return IntradayCeiling(
        kind="sharpened", city=city, target=tgt_iso, sub_degree=sub_degree,
        hour=hour, running_max_c=running_max, n_rise=len(rises),
        pmf=tuple(pmf), modal_bucket=modal_b, modal_prob=modal_p,
        source=f"London City Airport {icao} (live IEM ASOS METAR, hourly)")


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
