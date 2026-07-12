"""Intraday GRADE resolver — the vocabulary gate that makes an intraday read legitimate.

The 2026-07-12 Karachi miss (called "32 effectively LOCKED", settled 33) was not a bad
forecast — it was a VOCABULARY-GRADE breach: a finality word on coin-flip evidence, a live
cur_f lead dismissed as an "artifact" while it was leading a real between-obs peak, and the
lagging on-hour table read as the settling surface. See ISSUES_2026-07-12_INTRADAY_ACCURACY.md
and feedback_market_leads_lagging_wu_endpoint.md.

PURE and LABELS-ONLY (HARD RULE 2 — never moves the served pmf/modal/running_max). One
function classifies a read into one honest GRADE; one function renders it. The evidence
inputs are mechanical, not judgment:

  * endpoint motion  — from the intraday TAPE (weather_council/intraday_tape.py), the
                       persisted sequence of `wunderground_daily_max` reads. A RISING
                       endpoint hard-blocks "locked" (Karachi went 90°F(n27)→91°F(n34)
                       after the obs looked flat).
  * lead sustainment — rule G4 made mechanical (tape.cur_f_sustained): held across >=2
                       reads with a REFRESHING v3 obs stamp. Sustained = corroborated live
                       signal (Karachi/Jeddah, banked); frozen stamp = the London 07-11
                       stale over-read. Either way it is a LIVE COIN-FLIP — named, never
                       dismissed, never dressed as banked.
  * peak window      — the archive's own leak-free peaked-by-q0.95 hour, computed by
                       intraday_ceiling from the SAME history as the rise pmf. Unknown ->
                       never closed -> never locked on the clock.
  * post-sunset      — real solar geometry (NOAA sunset, pure math, city lat/lon), not a
                       hand-written hour map.

"locked"/"final" appears in the render iff `Grade.may_say_locked` — post-sunset, OR
(peak window closed AND endpoint stable AND not rising AND obs declining AND no live lead).
Anything less is PROVISIONAL and says so, quoting the backtest rate as climatology.
Grain-aware: San Francisco settles whole °F and its buckets render as °F.
KAT: tests/test_intraday_grade.py (unittest — the repo's actual gate).
"""
from __future__ import annotations

__all__ = ["Grade", "intraday_grade", "grade_lines", "peak_window_closed",
           "sunset_local_hour"]

import math
from dataclasses import dataclass, field

from .market import _native_reading_int
from .intraday_ceiling import banked_vs_leading


# ------------------------------------------------------------------------- solar geometry

def sunset_local_hour(lat_deg: float, lon_deg: float, date, utc_offset_hours: float
                      ) -> float | None:
    """Local sunset hour (fractional) from the NOAA general solar equations — pure math,
    no feed. After this hour the surface temperature day is physically closed, so it is the
    honest "post-sunset = settlement-grade" boundary (replaces hand-written per-city hour
    maps). Longitude east-positive. Returns None in polar day (sun never sets — never claim
    post-sunset); 0.0 in polar night."""
    n = date.timetuple().tm_yday
    g = 2.0 * math.pi / 365.0 * (n - 1 + 0.5)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    lat = math.radians(lat_deg)
    cos_ha = (math.cos(math.radians(90.833)) / (math.cos(lat) * math.cos(decl))
              - math.tan(lat) * math.tan(decl))
    if cos_ha < -1.0:
        return None            # polar day — the sun never sets today
    if cos_ha > 1.0:
        return 0.0             # polar night
    ha = math.degrees(math.acos(cos_ha))
    sunset_utc_min = 720.0 + 4.0 * (ha - lon_deg) - eqtime
    return ((sunset_utc_min / 60.0) + utc_offset_hours) % 24.0


def peak_window_closed(hour: float, close_hour: float | None) -> bool:
    """True only when we can AFFIRMATIVELY say the peak window has passed. Unknown
    close_hour -> False (never lock on the clock when the distribution is unknown)."""
    return close_hour is not None and hour > close_hour + 1e-9


# --------------------------------------------------------------------------------- grade

@dataclass(frozen=True)
class Grade:
    """The honest grade of one intraday read. `name` is one of:
      * "final"                — settlement-grade: post-sunset OR mechanically locked.
      * "leading_coinflip"     — a live cur_f leads the record; banked vs led UNRESOLVED.
      * "declining_provisional"— peak looks passed on the obs, not yet settlement-final.
      * "holding_provisional"  — day still at its max; the peak may not have formed.
      * "banked_floor"         — a floor with no state signal yet.
    `may_say_locked` is the single boolean the renderer consults before EVER using
    "locked"/"final". `coin_flip` is (banked, led) when a lead is unresolved. `unit` is the
    settlement unit the buckets render in ("°C", or "°F" for San Francisco). Labels only —
    carries no served probability."""
    name: str
    banked_bucket: int | None
    led_bucket: int | None
    coin_flip: tuple[int, int] | None
    may_say_locked: bool
    endpoint_rising: bool
    endpoint_stable: bool
    peak_closed: bool
    post_sunset: bool
    day_state: str | None
    unit: str = "°C"
    lead_sustained: bool | None = None   # None = no lead / not assessable
    cur_f: float | None = None
    endpoint_f: float | None = None
    endpoint_n: int | None = None
    lead_failed: bool = False            # post-sunset with the lead never banked
    lead_bank_stat: tuple[int, int] | None = None   # measured (banked, total) from the tape
    detail: tuple[str, ...] = field(default_factory=tuple)


def intraday_grade(ceiling, *, hour: float,
                   endpoint_rising: bool = False, endpoint_stable: bool = False,
                   lead_sustained: bool | None = None,
                   peak_close_hour: float | None = None,
                   post_sunset: bool = False,
                   lead_bank_stat: tuple[int, int] | None = None) -> Grade:
    """Classify one intraday read into its honest Grade. PURE — the motion/sustainment
    booleans come from the tape (intraday_tape.endpoint_motion / cur_f_sustained), the
    peak-close hour from the ceiling's own archive (default), sunset from solar geometry.
    Precedence: post-sunset final -> live-lead coin-flip -> mechanical final -> declining
    -> holding -> bare floor."""
    split = banked_vs_leading(ceiling)
    day_state = getattr(ceiling, "day_state", None)
    grain = getattr(ceiling, "grain", "C") or "C"
    sub = bool(getattr(ceiling, "sub_degree", False))
    unit = "°F" if grain == "F" else "°C"
    endpoint_n = getattr(ceiling, "wu_daily_max_n", None)
    if peak_close_hour is None:
        peak_close_hour = getattr(ceiling, "peak_close_hour", None)

    if split is not None:
        banked_bucket = split["banked_bucket"]
        led_bucket = split["led_bucket"]
        lead = bool(split["uncorroborated_lead"])
        cur_f, endpoint_f = split.get("cur_f"), split.get("endpoint_f")
    else:
        rm = getattr(ceiling, "running_max_c", None)
        banked_bucket = _native_reading_int(rm, grain, sub) if rm is not None else None
        led_bucket, lead, cur_f, endpoint_f = None, False, None, None

    closed = peak_window_closed(hour, peak_close_hour)
    common = dict(banked_bucket=banked_bucket, endpoint_rising=endpoint_rising,
                  endpoint_stable=endpoint_stable, post_sunset=post_sunset,
                  day_state=day_state, unit=unit, cur_f=cur_f, endpoint_f=endpoint_f,
                  endpoint_n=endpoint_n, lead_bank_stat=lead_bank_stat)

    # POST-SUNSET: the day is physically over; the endpoint IS the settlement. A still-
    # standing lead means the cur_f never banked -> the settlement is the BANKED bucket.
    if post_sunset:
        return Grade(name="final", led_bucket=led_bucket, coin_flip=None,
                     may_say_locked=True, peak_closed=True, lead_failed=lead,
                     lead_sustained=lead_sustained if lead else None, **common)

    # LIVE LEAD: an unresolved coin-flip. NEVER lockable; both buckets named, neither
    # dismissed — sustained or not only changes the corroboration wording.
    if lead:
        return Grade(name="leading_coinflip", led_bucket=led_bucket,
                     coin_flip=(banked_bucket, led_bucket), may_say_locked=False,
                     peak_closed=closed, lead_sustained=lead_sustained, **common)

    # MECHANICAL FINAL: peak provably past AND endpoint stopped moving AND not rising AND
    # the obs declining. A rising endpoint blocks this outright (the Karachi state).
    may_lock = bool(closed and endpoint_stable and not endpoint_rising
                    and day_state == "declining")
    name = ("final" if may_lock
            else "declining_provisional" if day_state == "declining"
            else "holding_provisional" if day_state == "holding"
            else "banked_floor")
    return Grade(name=name, led_bucket=None, coin_flip=None, may_say_locked=may_lock,
                 peak_closed=closed, **common)


# ------------------------------------------------------------------------------ rendering

def grade_lines(grade: Grade, *, backtest_prob: float | None = None,
                source: str | None = None) -> list[str]:
    """Render the honest headline + grade-critical lines. Vocabulary is chosen ENTIRELY by
    `grade.name`/`grade.may_say_locked` — "LOCK"/"final" appears iff `may_say_locked`.
    The SETTLING SURFACE (the WU daily-max endpoint — the record that pays) headlines every
    block (H2), so no reader mistakes the on-hour table or a nowcast for the settlement.
    Labels only; the caller appends its own range/override lines afterwards."""
    b, u = grade.banked_bucket, grade.unit
    L: list[str] = []

    if isinstance(grade.endpoint_f, (int, float)):
        n = f", n={grade.endpoint_n} obs" if grade.endpoint_n is not None else ""
        motion = (" — STILL RISING" if grade.endpoint_rising
                  else " — stable across reads" if grade.endpoint_stable else "")
        L.append(f"    settling surface: WU daily-max endpoint {grade.endpoint_f:.0f}°F"
                 f"{n}{motion} (this record pays; the on-hour table and any nowcast are "
                 f"context only)")

    if grade.name == "final":
        why = ("post-sunset — the temperature day is physically closed" if grade.post_sunset
               else "peak window passed; endpoint stable and not rising; obs declining")
        L.append(f"    ► INTRADAY LOCK (final) : {b}{u} — settlement-grade ({why}).")
        if grade.lead_failed and grade.led_bucket is not None:
            L.append(f"      the earlier {grade.led_bucket}{u} live lead never banked on "
                     f"the record — it settled {b}{u}.")

    elif grade.name == "leading_coinflip":
        led = grade.led_bucket
        L.append(f"    ► INTRADAY : {b}{u} banked (observation-grade) · {led}{u} LEADING — "
                 f"live {b}/{led} coin-flip, UNRESOLVED. Name both; pick neither.")
        if grade.lead_sustained:
            L.append(f"      the lead is SUSTAINED — cur_f held across reads on a refreshing "
                     f"v3 stamp (rule-G4 corroborated). NOT a probable over-read: a sustained "
                     f"lead often banks via a between-obs afternoon peak.")
        elif grade.lead_sustained is False:
            L.append(f"      the lead is SINGLE-READ / not yet refreshing — needs one more "
                     f"refreshed read to corroborate (a frozen v3 stamp was the London 07-11 "
                     f"stale over-read). Do not bank it; do not dismiss it.")
        else:
            L.append(f"      lead sustainment unknown (no tape history yet this day) — "
                     f"treat as unresolved either way.")
        stat = grade.lead_bank_stat
        if stat and stat[1] > 0:
            L.append(f"      tape record: {stat[0]}/{stat[1]} past uncorroborated leads "
                     f"ended up banking on the settlement record.")
        rising = ("the endpoint is STILL RISING toward it" if grade.endpoint_rising
                  else "the endpoint has not caught it yet")
        L.append(f"      resolves only when the daily-max endpoint catches the lead "
                 f"(→ {led}{u}) or the day closes with the record at {b}{u} (→ {b}{u}); "
                 f"{rising}.")

    elif grade.name == "declining_provisional":
        pr = (f"backtest ≈{backtest_prob*100:.0f}%" if backtest_prob is not None
              else "backtest rate")
        block = ("endpoint STILL RISING" if grade.endpoint_rising
                 else "endpoint not yet confirmed stable across two reads"
                 if not grade.endpoint_stable
                 else "peak window not yet provably closed")
        L.append(f"    ► INTRADAY FLOOR : {b}{u} — PROVISIONAL (declining; {pr}, "
                 f"climatology-grade, NOT final — {block}).")

    elif grade.name == "holding_provisional":
        L.append(f"    ► INTRADAY FLOOR : {b}{u} banked — PROVISIONAL (peak NOT formed; day "
                 f"HOLDING). No lock while holding — confidence is earned only once the day "
                 f"DECLINES and the endpoint stops rising.")
    else:  # banked_floor
        L.append(f"    ► INTRADAY FLOOR : {b}{u} banked (floor) — no peak-state signal yet.")

    if source:
        L.append(f"      grounded in: {source}")
    return L


# ------------------------------------------------------------------------------ self-test

def _self_test() -> None:
    import datetime as dt
    from .intraday_ceiling import IntradayCeiling

    def ceil(banked_c, run_c, cur_f=None, ep_f=None, ep_n=None, state="holding",
             grain="C", pch=None):
        return IntradayCeiling(
            kind="sharpened", city="X", target="2026-07-12", sub_degree=False, grain=grain,
            hour=14, running_max_c=run_c, banked_running_max_c=banked_c,
            live_cur_f=cur_f, wu_daily_max_f=ep_f, wu_daily_max_n=ep_n,
            day_state=state, peak_close_hour=pch)

    # 1. Karachi replay: banked 90°F (32), cur_f 91°F leads to 33, sustained on the tape.
    #    LIVE coin-flip, both buckets, NEVER lockable, framed as corroborated — not over-read.
    g = intraday_grade(ceil(32.22, 32.78, cur_f=91, ep_f=90, ep_n=27, pch=13),
                       hour=14, endpoint_stable=True, lead_sustained=True)
    assert g.name == "leading_coinflip" and g.coin_flip == (32, 33), g
    assert g.may_say_locked is False
    txt = " ".join(grade_lines(g))
    assert "SUSTAINED" in txt and "NOT a probable over-read" in txt
    assert "settling surface" in txt and "90°F" in txt          # H2 headline

    # 2. Rising endpoint HARD-BLOCKS a lock even when the obs read "declining".
    g = intraday_grade(ceil(32.78, 32.78, ep_f=91, ep_n=34, state="declining", pch=14),
                       hour=16, endpoint_rising=True)
    assert g.may_say_locked is False and g.name == "declining_provisional", g
    assert "STILL RISING" in " ".join(grade_lines(g, backtest_prob=0.96))

    # 3. Mechanical final needs ALL of: closed + stable + not rising + declining.
    g = intraday_grade(ceil(32.22, 32.22, ep_f=90, ep_n=32, state="declining", pch=14),
                       hour=18, endpoint_stable=True)
    assert g.name == "final" and g.may_say_locked is True, g
    # ...and each missing condition demotes it:
    assert intraday_grade(ceil(32.22, 32.22, state="declining", pch=14),
                          hour=18).may_say_locked is False          # not stable
    assert intraday_grade(ceil(32.22, 32.22, state="declining"),
                          hour=18, endpoint_stable=True).may_say_locked is False  # pch unknown
    assert intraday_grade(ceil(32.22, 32.22, state="holding", pch=14),
                          hour=18, endpoint_stable=True).may_say_locked is False  # holding

    # 4. Post-sunset resolves an un-banked lead DOWN to the banked bucket, and says so.
    g = intraday_grade(ceil(35.0, 35.56, cur_f=96, ep_f=95, state="declining"),
                       hour=20, post_sunset=True)
    assert g.name == "final" and g.banked_bucket == 35 and g.lead_failed is True
    assert "never banked" in " ".join(grade_lines(g))

    # 5. Grain-aware: San Francisco renders °F buckets (banked 21.11°C = 70°F).
    g = intraday_grade(ceil(21.11, 21.11, grain="F"), hour=12)
    assert g.unit == "°F" and g.banked_bucket == 70
    assert "70°F" in " ".join(grade_lines(g))

    # 6. Measured tape rate quoted when present.
    g = intraday_grade(ceil(32.22, 32.78, cur_f=91, ep_f=90), hour=14,
                       lead_sustained=True, lead_bank_stat=(3, 4))
    assert "3/4 past uncorroborated leads" in " ".join(grade_lines(g))

    # 7. Solar geometry sanity (the post-sunset gate is real, not a map): Singapore
    #    Changi 2026-07-12 sets ~19:15 SGT; London City ~21:15 BST.
    sg = sunset_local_hour(1.35, 103.99, dt.date(2026, 7, 12), 8.0)
    ldn = sunset_local_hour(51.505, 0.055, dt.date(2026, 7, 12), 1.0)
    assert 19.0 < sg < 19.5, sg
    assert 21.0 < ldn < 21.5, ldn
    assert peak_window_closed(20, None) is False    # unknown clock never locks

    print("intraday_grade self-test PASSED — sustained lead is a corroborated live coin-flip "
          "(not dismissed); a rising endpoint blocks 'locked'; mechanical final needs closed+"
          "stable+not-rising+declining; post-sunset (real solar geometry) un-banks a failed "
          "lead; SF renders °F; the settling endpoint headlines every block.")


if __name__ == "__main__":
    _self_test()
