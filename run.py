#!/usr/bin/env python3
"""Council-of-5 backtested weather verdict — command line entrypoint.

Examples:
  python3 run.py "Tokyo"                  # tomorrow's high/low, full report
  python3 run.py "Chicago" --lead 0       # today
  python3 run.py "Paris" --lead 3 --window 90
  python3 run.py "Berlin" --json          # machine-readable
  python3 run.py "London" --market        # also log a council-vs-market snapshot
  python3 run.py --verify                 # score past verdicts vs observed
  python3 run.py --edge                   # settle snapshots, print C7 edge verdict
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path

from weather_council.agents import WINDY_MEMBERS
from weather_council.compare import (
    VerdictMarketComparison,
    comparison_to_dict,
    compare_high,
    grain_support_note,
    match_market,
)
from weather_council.council import (Council, Verdict, applied_bias_correction,
                                     regime_consensus)
from weather_council.edge import report_lines as edge_report_lines, score_snapshots
from weather_council.convergence import report_lines as convergence_report_lines
from weather_council.market import MarketData
from weather_council.security import RateLimitError, SecurityError
from weather_council.sources import Sources, place_today
from weather_council.tc_gate import tc_halt
from weather_council.intraday import intraday_floor
from weather_council.intraday_ceiling import intraday_ceiling
from weather_council.station_offset import measure_settlement_offset
from weather_council.storage import (fetch_settled_snapshots, log_market_snapshot,
                                     log_verdict, settle_market_snapshots, verify)

# User-declared settlement-reference stations: cities the user has explicitly
# pinned to a specific airport record to "compare and contrast" every verdict
# against. This is NOT the system guessing a station (which it deliberately never
# does) — each entry is a user-supplied directive. The record is pulled from
# the IEM ASOS METAR archive (already allowlisted), which is the SAME raw feed
# Wunderground's airport-history pages are built from, so the comparison is
# faithful to the cited URL without scraping it or widening the sandbox.
SETTLEMENT_REFERENCE: dict[str, dict[str, str]] = {
    "london": {
        "icao": "EGLC",
        "name": "London City Airport",
        "url": "https://www.wunderground.com/history/daily/gb/london/EGLC",
    },
}

# Cities anchored on a NON-airport settlement station, with a nearby airport shown
# only as a cross-reference. User-supplied directive, same discipline as
# SETTLEMENT_REFERENCE — never a guessed station table. Hong Kong is the canonical
# case: the verdict anchors on the Hong Kong Observatory (the settlement-grade
# record, served live from HKO open-data because Meteostat's HKO file ends 1992),
# and the VHHH airport — which the council used to anchor on — is demoted to a
# measured cross-reference so the airport↔Observatory gap stays visible.
ANCHOR_CROSS_REFERENCE: dict[str, dict[str, str]] = {
    "hong kong": {
        "cross_ref_token": "airport",   # how to spot the cross-ref station nearby
        "note": ("The verdict anchors on the Hong Kong Observatory HQ (Tsim Sha "
                 "Tsui) — the settlement-grade record, served live from the HKO "
                 "open-data API. The VHHH airport is shown only as a cross-reference."),
    },
    "london": {
        "cross_ref_token": "weather centre",   # the central London station, demoted
        "note": ("The verdict anchors on London City Airport (EGLC) — the "
                 "settlement-grade record London weather markets pay out on. The "
                 "central London Weather Centre is shown only as a cross-reference."),
    },
}


def _settlement_reference_for(place) -> dict[str, str] | None:
    """The user-declared settlement reference for this city, or None. Matched on
    case-insensitive city-name containment so 'London' / 'London, GB' both hit."""
    name = (getattr(place, "name", "") or "").strip().lower()
    for key, ref in SETTLEMENT_REFERENCE.items():
        if key in name or name in key:
            return ref
    return None


def _settlement_reference(sources: Sources, place, target, v: Verdict) -> dict | None:
    """Build the 'compare & contrast vs the cited Wunderground airport record'
    block for a user-pinned city (e.g. London -> EGLC). Pulls the EGLC daily
    record from the IEM METAR archive (the feed WU displays) and contrasts it
    with (a) the verdict for the target day, when that day has settled, and
    (b) the council's own anchor station over the backtest window, so any
    settlement-vs-backtest divergence is visible — the lesson from the HK miss.

    Returns None when the city isn't pinned or the record can't be fetched; the
    caller simply omits the section."""
    ref = _settlement_reference_for(place)
    if ref is None:
        return None
    icao = ref["icao"]
    ts = v.truth_source or {}
    try:
        w_start = dt.date.fromisoformat(ts.get("window_start"))
        w_end = dt.date.fromisoformat(ts.get("window_end"))
    except (TypeError, ValueError):
        w_end = place_today(place) - dt.timedelta(days=1)
        w_start = w_end - dt.timedelta(days=60)
    # Extend the fetch through the target day so a finished target is captured.
    fetch_end = max(w_end, min(target, place_today(place) - dt.timedelta(days=1)))
    base = {"icao": icao, "name": ref["name"], "url": ref["url"],
            "target_date": target.isoformat(),
            "verdict_high": v.high, "verdict_low": v.low}
    try:
        md = sources.fetch_metar_daily(icao, w_start, fetch_end, place.timezone)
    except Exception as exc:
        # The city IS pinned — don't vanish silently. Surface that the reference
        # is temporarily unavailable (e.g. the IEM archive rate-limited us) so the
        # "always compare" guarantee is visible even when the fetch fails.
        return {**base, "error": str(exc)}
    daily = md.get("daily") or {}
    if not daily:
        return {**base, "error": "no daily records returned for the window"}

    target_record = daily.get(target.isoformat())   # (high, low) or None
    # Contrast EGLC with the council's anchor station (if it's a *different*
    # airport) over the overlapping window: mean(EGLC − anchor) high.
    anchor_icao = (ts.get("station") or {}).get("icao")
    offset = None
    if anchor_icao and anchor_icao != icao:
        try:
            amd = sources.fetch_metar_daily(anchor_icao, w_start, fetch_end, place.timezone)
            adaily = amd.get("daily") or {}
            common = sorted(set(daily) & set(adaily))
            dh = [daily[d][0] - adaily[d][0] for d in common]
            if len(dh) >= 10:
                import statistics as _st
                offset = {
                    "anchor_icao": anchor_icao,
                    "high_mean": round(_st.mean(dh), 2),
                    "high_median": round(_st.median(dh), 2),
                    "n": len(common),
                }
        except Exception:
            offset = None

    recent = sorted(daily)[-7:]
    return {
        "icao": icao,
        "name": ref["name"],
        "url": ref["url"],
        "grain": md.get("grain"),
        "grain_confidence": md.get("grain_confidence"),
        "target_date": target.isoformat(),
        "target_status": v.target_status,
        "target_record": target_record,
        "verdict_high": v.high,
        "verdict_low": v.low,
        "anchor_offset": offset,
        "anchor_is_same": bool(anchor_icao and anchor_icao == icao),
        "recent": [{"date": d, "high": daily[d][0], "low": daily[d][1]} for d in recent],
    }


def _anchor_cross_reference_for(place) -> dict[str, str] | None:
    """The user-declared anchor/cross-reference directive for this city, or None.
    Matched on case-insensitive city-name containment, like SETTLEMENT_REFERENCE."""
    name = (getattr(place, "name", "") or "").strip().lower()
    for key, ref in ANCHOR_CROSS_REFERENCE.items():
        if key in name or name in key:
            return ref
    return None


def _anchor_cross_reference(sources: Sources, place, target, v: Verdict) -> dict | None:
    """For a city anchored on a non-airport settlement station (e.g. Hong Kong ->
    Observatory), surface the nearby airport as a *cross-reference*: the measured
    seasonal offset of the airport's daily high vs the anchor. The verdict already
    settles on the anchor; this just shows how the airport — which the council used
    to anchor on — differs, so the gap stays visible. Returns None when the city
    isn't pinned; an {error} dict (never a silent vanish) when the cross-reference
    can't be earned this run."""
    ref = _anchor_cross_reference_for(place)
    if ref is None:
        return None
    ts = v.truth_source or {}
    station = ts.get("station") or {}
    base = {"anchor_station": station.get("name"), "anchor_icao": station.get("icao"),
            "note": ref["note"], "verdict_high": v.high, "verdict_low": v.low,
            "data_source": ts.get("data_source")}
    if ts.get("kind") != "station" or not station.get("id"):
        return {**base, "error": "the verdict isn't anchored on a station this run"}
    token = ref.get("cross_ref_token", "airport")
    try:
        nearby = sources.nearest_stations(place)
    except Exception as exc:
        return {**base, "error": str(exc)}
    air = next((s for s in nearby
                if token in (s.name or "").lower() and s.id != station.get("id")), None)
    if air is None:
        return {**base, "error": f"no '{token}' station found nearby to cross-reference"}
    try:
        # measure_settlement_offset computes mean(cross-ref − anchor) on the daily
        # high, seasonal + leak-free; pass the airport's real Meteostat name so it
        # matches itself rather than relying on a hardcoded spelling.
        off = measure_settlement_offset(sources, place, str(station["id"]),
                                        air.name, target)
    except Exception as exc:
        return {**base, "error": str(exc)}
    if off is None:
        return {**base, "error": "couldn't earn a seasonal airport↔anchor offset this "
                "run (no recent same-season overlap matched)"}
    return {
        **base,
        "cross_ref_station": off.settlement_station_name,
        "cross_ref_distance_km": off.settlement_distance_km,
        "high_mean": off.high_mean,            # mean(airport_high − anchor_high), °C
        "high_median": off.high_median,
        "se": round(off.standard_error, 3),
        "is_modern": off.is_modern,
        "n_season": off.n_season,
        "overlap_end": off.overlap_end,
    }


def _num(x, width=6, prec=1):
    return f"{x:{width}.{prec}f}" if isinstance(x, (int, float)) else " " * (width - 1) + "-"


def _pillars(v: Verdict) -> list[str]:
    L = ["  THREE-PILLAR PROCESS CHECK (observation -> computation -> interpretation)"]
    obs, ens, itp = v.observation, v.ensemble, v.interpretation

    cur = obs.current or {}
    t = cur.get("temperature_2m")
    if t is not None:
        line = (f"    [x] 1. OBSERVATION   now {t:.1f} °C, "
                f"{cur.get('relative_humidity_2m','?')}% RH, "
                f"wind {cur.get('wind_speed_10m','?')} km/h, "
                f"{cur.get('surface_pressure','?')} hPa")
        src = cur.get("temperature_source")
        if src:
            rt = cur.get("temperature_record_time")
            line += f"  [temp: {src}{(' @ ' + rt) if rt else ''}]"
        L.append(line)
    else:
        L.append("    [x] 1. OBSERVATION   current conditions unavailable")
    pc = cur.get("pressure_change_24h")
    if pc is not None:
        trend = ("falling — unsettled / storm risk" if pc <= -3
                 else "rising — clearing / stabilizing" if pc >= 3
                 else "steady")
        L.append(f"           barometric tendency: {pc:+.1f} hPa/24h ({trend})")
    L.append(f"           truth backbone: {obs.backbone}")
    if obs.recent:
        rec = ", ".join(f"{d} {h:.0f}/{lo:.0f}" for d, h, lo in obs.recent)
        L.append(f"           recent observed H/L: {rec}")

    qc = v.qc or {}
    L.append(f"    [x] 1b. QUALITY CONTROL {qc.get('screened',0)} values screened, "
             f"{qc.get('rejected',0)} rejected as out-of-band anomalies")

    if ens.member_count:
        members = " + ".join(f"{lab}({n})" for lab, n in ens.models.items())
        agree = ens.agreement_high
        agree_s = f"{agree*100:.0f}% of members within ±2 °C of mean" if agree is not None else ""
        L.append(f"    [x] 2. COMPUTATION   {len(v.votes)} NWP centers (deterministic) + "
                 f"{ens.member_count}-member perturbed ensemble")
        L.append(f"           ensemble: {members}")
        L.append(f"           ensemble mean H/L {ens.mean_high:.1f}/{ens.mean_low:.1f}, "
                 f"1σ spread {ens.spread_high:.1f}/{ens.spread_low:.1f}, "
                 f"P10–P90 high {ens.p10_high:.1f}–{ens.p90_high:.1f} °C")
        L.append(f"           ensemble agreement: {agree_s} "
                 f"(more agreement -> higher confidence)")
        L.append("           physics: Navier-Stokes, continuity, thermodynamics, "
                 "ideal-gas & moisture eqns solved inside each NWP model")
    else:
        L.append(f"    [x] 2. COMPUTATION   {len(v.votes)} NWP centers (ensemble unavailable here)")

    bh = f"{itp.mean_bias_removed_high:.2f}" if itp.mean_bias_removed_high is not None else "-"
    bl = f"{itp.mean_bias_removed_low:.2f}" if itp.mean_bias_removed_low is not None else "-"
    L.append(f"    [x] 3. INTERPRETATION {itp.members_used} centers used, "
             f"{itp.outliers_set_aside} outlier(s) set aside")
    L.append(f"           backtested adjustments: mean bias removed H/L {bh}/{bl} °C "
             f"over {itp.history_days}-day pattern history")
    L.append("           (per-location bias correction absorbs persistent terrain/microclimate error)")
    L.append("")
    return L


def _market_lines(c: VerdictMarketComparison) -> list[str]:
    L = ["  MARKET COMPARISON (model vs Polymarket implied — read-only, NOT an edge)"]
    L.append(f"    market   : {c.market_title}")
    # Sub-degree market (e.g. HK on the Observatory): we no longer withhold. The
    # verdict is transferred onto the settlement station's scale by a *measured*
    # offset, and the transfer's provenance (which stations, season, vintage) is
    # stated so the comparison is earned, not fabricated.
    # A sub-degree market is bridged by a measured station offset. CRUCIAL: that
    # offset is only trustworthy if measured on *recent* overlap. When it is
    # decades-stale (settlement_offset_modern is False) the two stations may have
    # diverged (e.g. HK's airport moved from urban Kai Tak to open-water Chek Lap
    # Kok in 1998, long after the 1992 overlap), so the transfer cannot be trusted
    # and we must NOT assert a model edge over the market — the market is pricing
    # the live settlement sensor we have no current data for.
    stale_transfer = c.settles_sub_degree and c.settlement_offset_modern is False
    if c.settles_sub_degree and c.settlement_same_station:
        # No cross-station transfer: the market settles on the SAME station the
        # council backtests on, so the verdict already lives on the settlement
        # scale and the offset is 0 °C by identity (one instrument), not assumed.
        L.append("    scale    : settles 0.1° on the SAME station the council backtests on; "
                 "the verdict is already on the settlement scale, so the offset is 0 °C by "
                 "identity (one instrument) — no cross-station transfer is made.")
        if c.settlement_offset_note:
            L.append(f"               {c.settlement_offset_note}")
    elif c.settles_sub_degree and c.settlement_offset_c is not None:
        L.append("    transfer : settles sub-degree on a different station than the backtest; "
                 "verdict moved onto that scale by a measured offset.")
        if c.settlement_offset_note:
            L.append(f"               {c.settlement_offset_note}")
        L.append(f"               settlement-scale high {c.settlement_high_c:.2f} °C "
                 f"(verdict {c.verdict_high_c:.1f} {c.settlement_offset_c:+.2f} offset)")
        if stale_transfer:
            L.append("               ⚠ STALE TRANSFER: this offset is climatological, not live — "
                     "the settlement and backtest stations may have diverged since the overlap "
                     "ended, so the settlement-scale number above is unreliable and no model "
                     "edge over the market is asserted (see below).")
    # How the verdict settles depends on the record's GRAIN. A sub-degree record
    # (Hong Kong on the HKO Observatory, 0.1 °C) keeps the tenths — 30.7 °C settles
    # as 30.7 °C, NOT a whole-degree "31". Only whole-degree airport-METAR records
    # snap to an integer. Conflating the two is exactly the rounding error that made
    # a continuous 30.7 °C look like a one-degree disagreement with the market.
    rounded = abs(c.verdict_high_c - c.verdict_reading) > 1e-9
    if c.settles_sub_degree:
        # The settlement record is finer than its whole-degree bucket labels, so no
        # whole-degree rounding applies. Show the verdict on the settlement-station
        # scale to 0.1 °C (the offset transfer is stated on the 'transfer' line).
        scale_c = c.settlement_high_c if c.settlement_high_c is not None else c.verdict_high_c
        L.append(f"    settles  : 0.1 °{c.grain} record (sub-degree) | high {scale_c:.1f} °C "
                 f"settles as {scale_c:.1f} °C — no whole-degree rounding applies "
                 f"-> bucket {c.verdict_bucket}")
        # Honest caveat: showing ONE bucket above needs a 0.1°→whole rule the
        # contract labels don't reveal. Say whether that unverified rule actually
        # CHANGES the bucket here — don't imply false certainty.
        if c.rounding_robust is False:
            L.append(f"    map rule : ⚠ the whole-degree bucket DEPENDS on the unverified "
                     f"0.1°→whole rule — round-to-nearest -> {c.rounding_near_bucket}, "
                     f"truncation -> {c.rounding_trunc_bucket}. The comparison below assumes "
                     f"round-to-nearest; a human should confirm the contract's rule.")
        elif c.rounding_robust is True:
            L.append(f"    map rule : the unverified 0.1°→whole rule does not change the "
                     f"bucket here (round-to-nearest and truncation both -> "
                     f"{c.rounding_near_bucket}).")
    else:
        # Whole-degree airport-METAR record: the contract reads an integer, so the
        # continuous verdict IS rounded half-up. Make that explicit so an integer
        # like "18" next to a market "19" isn't misread as a one-degree
        # disagreement when the verdict was e.g. 18.4 °C.
        snap = "rounds to" if rounded else "settles as"
        tag = " (ROUNDED)" if rounded else ""
        L.append(f"    settles  : whole °{c.grain} | verdict {c.verdict_high_c:.1f} °C "
                 f"{snap} {c.verdict_reading}°{tag} -> bucket {c.verdict_bucket}")
    fragile = c.edge_distance_c is not None and c.edge_distance_c <= 0.5
    if c.edge_distance_c is not None:
        flip = " — a shift this small flips the bucket" if fragile else ""
        L.append(f"    edge dist: {c.edge_distance_c:.2f} °C to the nearest bucket boundary "
                 f"(small = fragile assignment){flip}")
    L.append(f"    modal    : model says {c.model_modal}  |  market says {c.market_modal}")
    if c.model_modal and c.market_modal and c.model_modal != c.market_modal:
        scale_c = c.settlement_high_c if c.settlement_high_c is not None else c.verdict_high_c
        if stale_transfer:
            # No edge: the model is accurate on its *own* (backtest) station, but
            # the bridge to the settlement sensor is stale. The market prices the
            # live settlement sensor, so treat ITS bucket as the better
            # settlement-scale estimate rather than asserting the model is right.
            L.append(f"    DEFER    : model favours {c.model_modal} on the backtest station, but the "
                     f"transfer to the settlement sensor is stale (above), so this is NOT a model "
                     f"edge. The market favours {c.market_modal}; with no live settlement-sensor "
                     f"data, treat the market's {c.market_modal} as the better settlement-scale "
                     f"estimate. The model's value is the backtest-station forecast, not the "
                     f"settlement number.")
        else:
            # Earned disagreement: the model's settlement-scale reading vs the
            # market's favourite bucket is a fact, paired with the backtested bias.
            L.append(f"    DISAGREE : model {scale_c:.1f} °C favours {c.model_modal}, the market "
                     f"favours {c.market_modal}. The model is the backtested side "
                     f"(beat naive/persistence/climatology on {c.n_residuals} held-out days).")
    # State the backtested bias correction baked into the verdict as a fact, so a
    # divergence from the market reads as earned signal rather than a hedge. The
    # 0.05 °C floor is only a display threshold (below it the figure rounds to
    # ~0.0), not a performance knob. n_residuals is the held-out backtest count.
    if c.bias_correction_c is not None:
        if abs(c.bias_correction_c) >= 0.05:
            dirn = "down" if c.bias_correction_c < 0 else "up"
            tail = ("an earned reason to diverge from the market, not noise."
                    if not stale_transfer else
                    "this corrects the verdict on the BACKTEST station; it does not "
                    "justify diverging from the market, which prices the settlement "
                    "sensor the stale transfer can't reach.")
            L.append(f"    correction: the verdict bakes in a backtested bias correction of "
                     f"{c.bias_correction_c:+.2f} °C (raw multi-model blend pulled {dirn}), "
                     f"learned over {c.n_residuals} held-out days — {tail}")
        else:
            L.append(f"    correction: negligible bias correction ({c.bias_correction_c:+.2f} °C "
                     f"over {c.n_residuals} held-out days); any divergence here is the raw "
                     f"multi-model consensus itself, not a learned shift.")
    # When the verdict's bucket label is fragile, separate two layers so we don't
    # bury earned signal: the integer *label* is fragile (18.4 is a hair from the
    # edge), but the continuous verdict is the real, bias-corrected position
    # (quantified on the 'correction' line above).
    if rounded and fragile and not c.settles_sub_degree \
            and c.model_modal == c.verdict_bucket \
            and c.market_modal != c.model_modal:
        L.append(f"             ^ the integer label {c.verdict_reading}° is fragile: "
                 f"{c.verdict_high_c:.1f} °C is only {c.edge_distance_c:.2f} °C from the "
                 f"{c.verdict_bucket}/{c.market_modal} edge, so don't over-read the bucket.")
        L.append(f"               The continuous verdict ({c.verdict_high_c:.1f} °C) is the "
                 f"real signal — see 'correction' above and the model-vs-market column below.")
    if c.market_overround is not None:
        L.append(f"    overround: {c.market_overround*100:+.1f}% vig still in the raw Yes prices "
                 f"(removed in 'mkt P' below)")
    L.append(f"    {'bucket':>14}{'model P':>10}{'mkt P':>9}{'Δ pts':>9}")
    for b in c.buckets:
        mp = f"{b.model_prob*100:.1f}%"
        kp = f"{b.market_prob*100:.1f}%" if b.market_prob is not None else "-"
        d = f"{(b.model_prob - b.market_prob)*100:+.1f}" if b.market_prob is not None else "-"
        L.append(f"    {b.label:>14}{mp:>10}{kp:>9}{d:>9}")
    if c.largest_gap is not None:
        L.append(f"    largest model-vs-market gap: {c.largest_gap*100:.1f} pts")
    if c.liquidity_note:
        L.append(f"    depth    : {c.liquidity_note}")
    if c.unmatched_fraction:
        L.append(f"    note: {c.unmatched_fraction*100:.0f}% of resampled draws fell outside the ladder")
    cal = c.calibration
    if cal:
        cov = (f", {cal.coverage_80*100:.0f}% out-of-sample coverage of the 80% band "
               f"(n={cal.coverage_n})" if cal.coverage_80 is not None else "")
        skew = f", warm-skewed {cal.skew:+.2f}" if abs(cal.skew) >= 0.3 else ""
        L.append(f"    calibration: {cal.n} held-out errors — bias {cal.bias:+.2f} °C, "
                 f"spread {cal.spread:.2f} °C{skew}{cov}")
    if c.settlement_bias_note:
        L.append(f"    caveat   : {c.settlement_bias_note}")
    L.append("    -> NOT a validated edge: model probs are on the backtest-truth scale "
             "and no realized-outcome calibration exists yet (C7).")
    L.append("")
    return L


def _settlement_reference_lines(ref: dict) -> list[str]:
    """Render the user-pinned 'compare & contrast vs Wunderground airport' block."""
    L = [f"  SETTLEMENT RECORD — Wunderground {ref['icao']} ({ref['name']}) [user-pinned]"]
    L.append(f"    source   : {ref['url']}")
    if ref.get("error"):
        vh, vl = ref.get("verdict_high"), ref.get("verdict_low")
        L.append(f"    status   : reference temporarily unavailable ({ref['error']}); the "
                 f"{ref['icao']} record (same feed as the page above) couldn't be fetched this "
                 f"run. Verdict {vh:.1f}/{vl:.1f} °C still stands to be checked against it.")
        L.append("")
        return L
    L.append(f"               (pulled from the IEM ASOS METAR archive — the same raw "
             f"feed this page shows; native grain whole °{ref['grain']})")
    tr = ref.get("target_record")
    vh, vl = ref.get("verdict_high"), ref.get("verdict_low")
    if tr is not None:
        rh, rl = tr
        eh = vh - rh if vh is not None else None
        el = vl - rl if vl is not None else None
        settled = ref.get("target_status") != "forecast"
        word = "RECORDED" if settled else "so far (day not finished)"
        L.append(f"    {ref['target_date']} {ref['icao']} {word}: high {rh:.0f}°  low {rl:.0f}°")
        if eh is not None and el is not None:
            L.append(f"    verdict vs record: verdict {vh:.1f}/{vl:.1f} °C — "
                     f"high {eh:+.1f} °C, low {el:+.1f} °C vs the {ref['icao']} record"
                     + ("" if settled else " (provisional)"))
    else:
        L.append(f"    {ref['target_date']}: no {ref['icao']} record yet (target not "
                 f"finished or not in the archive) — verdict {vh:.1f}/{vl:.1f} °C stands "
                 f"to be checked against it once the day settles.")
    off = ref.get("anchor_offset")
    if ref.get("anchor_is_same"):
        L.append(f"    anchor   : the council already backtests on {ref['icao']} — the "
                 f"settlement record and the verdict's truth source are the same station.")
    elif off is not None:
        L.append(f"    contrast : {ref['icao']} runs {off['high_mean']:+.2f} °C vs the "
                 f"council's anchor station {off['anchor_icao']} on the daily high "
                 f"(median {off['high_median']:+.2f}, n={off['n']} overlapping days) — "
                 f"the settlement and backtest stations differ; read the verdict against "
                 f"{ref['icao']} accordingly.")
    rec = ref.get("recent") or []
    if rec:
        L.append(f"    recent {ref['icao']} daily record (most recent {len(rec)} days):")
        for r in rec:
            L.append(f"      {r['date']}  high {r['high']:.0f}°  low {r['low']:.0f}°")
    L.append("")
    return L


def _anchor_cross_reference_lines(ref: dict) -> list[str]:
    """Render the demoted-station cross-reference for a verdict anchored on a
    user-pinned settlement station (Hong Kong -> Observatory, London -> EGLC):
    the measured seasonal offset of the old anchor's daily high vs the new one."""
    anchor = ref.get("anchor_station") or "the settlement station"
    icao = ref.get("anchor_icao")
    anchor_disp = f"{anchor} ({icao})" if icao else anchor
    xref = ref.get("cross_ref_station") or "the cross-reference station"
    L = [f"  ANCHOR & CROSS-REFERENCE — anchored on {anchor_disp} [user-pinned]"]
    L.append(f"    anchor   : {ref['note']}")
    if ref.get("data_source") == "hko_opendata":
        L.append("    feed     : Hong Kong Observatory open-data "
                 "(data.weather.gov.hk) — the live settlement record")
    if ref.get("error"):
        L.append(f"    x-ref    : cross-reference unavailable this run "
                 f"({ref['error']}); the verdict still stands on the {anchor} anchor.")
        L.append("")
        return L
    vintage = "live overlap" if ref["is_modern"] else "climatological (stale) overlap"
    d = ref.get("cross_ref_distance_km")
    dist = f" ({d:.1f} km)" if isinstance(d, (int, float)) else ""
    L.append(f"    x-ref    : {xref}{dist} runs "
             f"{ref['high_mean']:+.2f} °C on the daily high vs the {anchor} anchor "
             f"(median {ref['high_median']:+.2f}, ±{ref['se']:.2f} SE, "
             f"n={ref['n_season']} same-season days, {vintage})")
    if abs(ref["high_mean"]) < 0.15:
        L.append(f"    -> {xref} and {anchor} track within ±0.15 °C on the seasonal "
                 f"high; the anchor choice barely moves the number, but the verdict "
                 f"now settles on the record markets actually pay out on.")
    else:
        L.append(f"    -> {xref} reads {ref['high_mean']:+.2f} °C off the {anchor} "
                 f"anchor; keeping the old {xref} anchor would have biased the verdict "
                 f"by that much against the settlement record.")
    L.append("")
    return L


_CONSENSUS_TAG = {"matched": "MATCHED", "loose": "LOOSE", "split": "SPLIT"}


def _regime_consensus_lines(v: Verdict) -> list[str]:
    """Single consolidated read: the regime, which validation is load-bearing in
    it, and whether the independent mechanisms reach a matched verdict. Subsumes
    the standalone naive-vs-verdict and deterministic-vs-ensemble comparisons."""
    rc = regime_consensus(v)
    reg, con = rc["regime"], rc["consensus"]
    est = con["estimators"]
    scale = "σ" if con["scaled_by_effective_sigma"] else f"σ≈{con['sigma_used']}°C"
    L = ["  REGIME & CONSENSUS (do the independent mechanisms agree?)"]
    L.append(f"    regime   : {reg['label']}")
    L.append(f"    consensus: {_CONSENSUS_TAG[con['status']]} — estimators agree "
             f"within {con['worst_ratio']}{scale} (worst: {con['worst_axis']})")
    L.append(f"      verdict (deterministic) : "
             f"{est['high']['verdict']:.1f} / {est['low']['verdict']:.1f} °C")
    if est['high']['naive'] is not None:
        L.append(f"      naive equal-weight      : "
                 f"{est['high']['naive']:.1f} / {est['low']['naive']:.1f} °C")
    if est['high']['ensemble_mean'] is not None:
        L.append(f"      perturbed-ensemble mean : "
                 f"{est['high']['ensemble_mean']:.1f} / {est['low']['ensemble_mean']:.1f} °C")
    for i, line in enumerate(rc["trusted_validation"]):
        L.append(f"    {'trust    :' if i == 0 else '             '} {line}")
    L.append(f"    -> {rc['takeaway']}")
    L.append("")
    return L


HEALTHCHECK_STATUS = Path(__file__).resolve().parent / "reports" / "healthcheck_status.json"


def _healthcheck_banner(today: dt.date | None = None,
                        status_path: Path = HEALTHCHECK_STATUS) -> list[str]:
    """Read-only banner that surfaces the latest daily health-check status beside
    the verdict. The health check is the recommend-only monitor; this ONLY DISPLAYS
    its findings — it never reads back into, gates, or moves any verdict number.
    An absent or malformed status file yields no banner, so the verdict is never
    blocked by the monitor. Findings here require human review; nothing auto-applies.
    """
    try:
        s = json.loads(status_path.read_text())
    except (OSError, ValueError):
        return []                                  # no monitor status -> no banner
    today = today or dt.date.today()
    date_s = s.get("date", "?")
    try:
        age = (today - dt.date.fromisoformat(date_s)).days
        age_s = "today" if age == 0 else f"{age} day(s) ago"
        stale = age > 2
    except (TypeError, ValueError):
        age_s, stale = "date unknown", True
    L = ["", "  DAILY HEALTH CHECK (recommend-only monitor — display only, never moves this verdict)"]
    L.append(f"    as of {date_s} ({age_s})"
             + ("   ⚠ STALE — monitor has not run in >2 days" if stale else ""))
    bm, base = s.get("basket_mae"), s.get("baseline_mae")
    if bm is not None and base is not None:
        verdict = "⚠ REGRESSION" if s.get("regression") else "stable"
        L.append(f"    basket MAE {bm:.4f} vs baseline {base:.4f} "
                 f"({bm - base:+.4f}) — {verdict}")
    elif bm is not None:
        L.append(f"    basket MAE {bm:.4f} (no baseline on file yet)")
    cov, lab = s.get("calibration_coverage_pct"), s.get("calibration_label")
    if cov is not None:
        L.append(f"    80% interval coverage {cov:.1f}% — {lab}")
    m = s.get("metrics") or {}
    if m.get("run_seconds") is not None:
        err = m.get("city_error_rate")
        err_s = f", city error rate {err * 100:.0f}%" if err is not None else ""
        L.append(f"    monitor run {m['run_seconds']:.1f}s over "
                 f"{m.get('cities_usable', '?')}/{m.get('cities_total', '?')} cities"
                 f"{err_s}, {m.get('requests', '?')} requests")
    reco = s.get("recommendations") or []
    if reco:
        L.append("    RECOMMENDATIONS (human review — do NOT auto-apply):")
        for r in reco:
            L.append(f"      - {r}")
    else:
        L.append("    no constant changes recommended.")
    return L


def _bucket_call(v: Verdict, ceiling=None) -> dict:
    """The decision-relevant answer: which whole-degree bucket the market settles on,
    and the conviction IN THAT BUCKET (its own probability) — not the ±2°C point
    reliability that `Confidence` reports. Uses the intraday lever when it is sharpened
    and confident (same-day, post-peak — London ~89–99%), else the day-ahead bucket
    distribution (the modal of the residual cloud resampled through the settlement
    quantizer). So a day-ahead coin-flip honestly reads LOW, not HIGH."""
    from weather_council.market import _native_reading_int
    sub = "hong kong" in v.place.label().lower()
    rule = "floor / 0.1°C" if sub else "round-half-up / whole °C"
    resid = (getattr(v.validation, "residuals_high", None) if v.validation else None) or []
    da_bucket = _native_reading_int(v.high, "C", sub)
    da_prob = None
    if len(resid) >= 12:
        cnt: dict[int, int] = {}
        for e in resid:
            b = _native_reading_int(v.high + e, "C", sub)
            cnt[b] = cnt.get(b, 0) + 1
        da_bucket = max(cnt, key=cnt.get)          # modal of the settlement-bucket pmf
        da_prob = cnt[da_bucket] / len(resid)
    use_intra = (ceiling is not None and getattr(ceiling, "is_sharpened", False)
                 and ceiling.modal_prob is not None and ceiling.modal_prob >= 0.70)
    if use_intra:
        bucket, prob = ceiling.modal_bucket, ceiling.modal_prob
        source = (f"intraday — running max {ceiling.running_max_c:.1f}°C by "
                  f"{ceiling.hour:02d}:00, σ collapsed near the peak")
    else:
        bucket, prob = da_bucket, da_prob
        source = "day-ahead distribution"
    tier = ("HIGH" if (prob or 0) >= 0.70 else
            "MODERATE" if (prob or 0) >= 0.50 else "LOW")
    return {"bucket": bucket, "prob": prob, "tier": tier, "source": source,
            "rule": rule, "used_intraday": use_intra,
            "day_ahead_bucket": da_bucket, "day_ahead_prob": da_prob}


def _bucket_call_lines(v: Verdict, ceiling=None) -> list[str]:
    c = _bucket_call(v, ceiling)
    L = ["", f"  BUCKET CALL — the {c['rule']} bucket the market settles on"]
    if c["prob"] is None:
        L.append(f"    => {c['bucket']}°C  (conviction unavailable — too little "
                 f"held-out history)")
        return L
    L.append(f"    => {c['bucket']}°C  —  {c['tier']} conviction {c['prob']*100:.0f}%   "
             f"[{c['source']}]")
    if not c["used_intraday"] and c["tier"] == "LOW":
        hk = "hong kong" in v.place.label().lower()
        if hk:
            L.append("    day-ahead HK bucket is information-limited (σ ≈ bucket width, "
                     "floor rule) — no hourly settlement record yet, so no intraday "
                     "sharpening; treat as a distribution, not a confident call")
        else:
            L.append("    day-ahead bucket is information-limited (σ ≈ bucket width) — a "
                     "confident single-bucket call comes intraday as the peak nears "
                     "(run with --intraday on the settlement day)")
    return L


def render(v: Verdict, comparison: VerdictMarketComparison | None = None,
           settlement_ref: dict | None = None,
           cross_reference: dict | None = None,
           c7_validated: bool = False, ceiling=None) -> str:
    L = []
    L.append(f"COUNCIL VERDICT  —  {v.place.label()}  ({v.target})")
    L.append("=" * 64)
    L.extend(_pillars(v))
    L.append(f"  HIGH : {v.high:6.1f} °C        LOW : {v.low:6.1f} °C")
    ts = v.truth_source or {}
    finished_truth = ("the station's own reading" if ts.get("kind") == "station"
                      else "ERA5 reanalysis of a finished day")
    status_s = ("FORECAST (day not yet finished — not a confirmed reading)"
                if v.target_status == "forecast"
                else f"RECORDED ({finished_truth})")
    L.append(f"    these are: {status_s}")
    if ts.get("kind") == "station":
        L.append(f"    anchored on: {ts.get('label','')}")
    L.append(f"    of: {v.target_basis}")
    L.extend(_bucket_call_lines(v, ceiling))
    cd = v.confidence_detail
    hr = cd.get("hit_rate_within_2c")
    hr_s = f"{hr*100:.0f}% held-out hits -> {cd.get('backtest_tier','?')}" if hr is not None \
        else "no held-out history -> low"
    wsig = cd.get("within_system_sigma")
    xsig = cd.get("cross_system_disagreement")
    rsig = cd.get("representativeness_sigma")
    eff = cd.get("effective_uncertainty")
    pen = cd.get("tiers_downgraded", 0)
    parts = []
    if wsig is not None:
        parts.append(f"within-model σ {wsig:.1f}")
    if xsig is not None:
        parts.append(f"panel-vs-ensemble {xsig:.1f}")
    if rsig is not None:
        parts.append(f"grid-vs-station σ {rsig:.1f}")
    eff_s = (f"effective σ {eff:.1f} °C [{', '.join(parts)}] "
             f"({'routine, no downgrade' if pen == 0 else f'elevated, -{pen} tier'})"
             ) if eff is not None else "no dispersion data"
    L.append(f"  Point reliability (±2°C, NOT the bucket): {v.confidence.upper()}")
    L.append(f"    earned base : {hr_s}")
    L.append(f"    today's risk: {eff_s}")
    if cd.get("seasonal_downgrade"):
        sg = cd.get("season_gap_days")
        L.append(f"    seasonal    : backtest truth is ~{sg} days (day-of-year) off the "
                 f"target — out-of-season (Meteostat bulk archive lag); the hit-rate above "
                 f"is from a different regime, so confidence is cut one extra tier.")
        sa = (v.truth_source or {}).get("seasonal_analog") or {}
        if sa.get("applied"):
            L.append(f"    analog bias : re-learned each member's bias from same-day-of-year "
                     f"analog days ({sa['analog_start']}..{sa['analog_end']}, "
                     f"±{sa['window_days']}d, {sa['analog_obs_days']} obs days) for "
                     f"{sa['members_corrected']} member(s) — the trailing window is the wrong "
                     f"season to learn a bias on, so the correction is trained on the right one.")
    L.append(f"    (range across panel: high {v.high_spread} / low {v.low_spread} °C, shown for reference)")
    L.append("")

    L.extend(_regime_consensus_lines(v))

    rp = v.representativeness
    if rp.sigma is not None:
        on_station = (v.truth_source or {}).get("kind") == "station"
        if on_station:
            header = "  SPATIAL REPRESENTATIVENESS (how steep the field is at the station)"
            gap = ("the field is flat here — the model resolves this point cleanly"
                   if rp.sigma <= 0.75
                   else "a moderate local gradient — the model's value at the station "
                        "carries some spatial uncertainty"
                   if rp.sigma <= 1.5
                   else "a steep local gradient (coast/terrain/urban edge) — even "
                        "anchored at the station, the model's gridded field is "
                        "uncertain at this exact point; folded into confidence")
        else:
            header = "  SPATIAL REPRESENTATIVENESS (grid cell vs. a point station inside it)"
            gap = ("the grid cell stands in well for any point inside it"
                   if rp.sigma <= 0.75
                   else "a specific station may differ moderately from this grid value"
                   if rp.sigma <= 1.5
                   else "a specific station (e.g. an official observatory) can differ "
                        "substantially — treat the headline number as the grid cell, not the station")
        L.append(header)
        L.append(f"    across-cell σ : {rp.sigma:.1f} °C "
                 f"(±{rp.offset_deg:.2f}° neighbours, {rp.sample_days} days)")
        L.append(f"    -> {gap}")
        L.append("")

    s = v.settlement
    if s:
        unit = "whole °F" if s["grain"] == "F" else "whole °C"
        L.append("  SETTLEMENT ALIGNMENT (how this resolves against the market's record)")
        low_grain = s.get("grain_confidence") == "low"
        caveat = "  ⚠ LOW confidence — evidence thin/ambiguous, grain not asserted" if low_grain else ""
        L.append(f"    native grain : {unit} "
                 f"(detected: {s['grain_evidence'].get(s['grain'])*100:.0f}% of "
                 f"raw METAR obs are integral in {s['grain']}){caveat}")
        L.append(f"    settles as   : high {s['high_native']}  low {s['low_native']}  "
                 f"(verdict {v.high:.1f}/{v.low:.1f} °C snapped to the integer record)")
        chk = s.get("source_check")
        if chk:
            L.append(f"    source check : raw METAR vs the Meteostat truth we backtest on, "
                     f"{chk['n']} overlapping days")
            L.append(f"      high  mean {chk['high_mean']:+.2f} °C  median "
                     f"{chk['high_median']:+.2f} °C  largest {chk['high_max']:+.2f} °C  "
                     f"({chk['tail_days_ge3']} day(s) ≥3 °C apart)")
            L.append(f"      low   mean {chk['low_mean']:+.2f} °C  median "
                     f"{chk['low_median']:+.2f} °C")
            if chk["tail_days_ge3"]:
                L.append("      -> Meteostat clips the daily high on hot days vs the raw "
                         "METAR the record settles on; bias-correcting on it under-reads peaks.")
                for t in chk.get("tail_days", [])[:5]:    # the worst few, named (B2)
                    L.append(f"         {t['date']}: METAR {t['metar_high']:.1f} vs "
                             f"Meteostat {t['observed_high']:.1f} °C  ({t['delta']:+.1f})")
        L.append("")

    if comparison is not None:
        L.extend(_market_lines(comparison))

    if settlement_ref is not None:
        L.extend(_settlement_reference_lines(settlement_ref))

    if cross_reference is not None:
        L.extend(_anchor_cross_reference_lines(cross_reference))

    ci = v.convergence
    if ci:
        ch = ci["high"].decide(c7_validated) if ci.get("high") else None
        cl = ci["low"].decide(c7_validated) if ci.get("low") else None
        L.extend(convergence_report_lines(ch, cl, c7_validated))

    d = v.diurnal
    if d.peak_time or d.obs_peak_hour is not None:
        L.append("  DIURNAL PEAK / TROUGH (when within the day it lands)")
        if d.peak_time:
            ok = "✓ matches" if d.peak_in_band else "⚠ off"
            ob = (f" — historically peaks ~{d.obs_peak_hour:02.0f}:00 ±{d.obs_peak_sd:.1f}h ({ok})"
                  if d.obs_peak_hour is not None else "")
            L.append(f"    hottest : {d.peak_temp:.1f} °C around {d.peak_time} local{ob}")
        if d.trough_time:
            ok = "✓ matches" if d.trough_in_band else "⚠ off"
            ob = (f" — historically bottoms ~{d.obs_trough_hour:02.0f}:00 ±{d.obs_trough_sd:.1f}h ({ok})"
                  if d.obs_trough_hour is not None else "")
            L.append(f"    coldest : {d.trough_temp:.1f} °C around {d.trough_time} local{ob}")
        L.append(f"    (peak/trough times from multi-model hourly curve; observed bands "
                 f"from {d.history_days}-day ERA5 hourly archive)")
        L.append("")

    r = v.records
    if r.sample_days:
        L.append(f"  CLIMATOLOGY & RECORDS  (this date ±{r.window_days}d, "
                 f"observed since {r.since_year}, {r.sample_days} sample days)")
        if r.record_high is not None:
            L.append(f"    record high : {r.record_high:5.1f} °C ({r.record_high_year})"
                     f"        normal high: {r.normal_high:.1f} °C")
        if r.record_low is not None:
            L.append(f"    record low  : {r.record_low:5.1f} °C ({r.record_low_year})"
                     f"        normal low : {r.normal_low:.1f} °C")
        if r.peak_percentile is not None:
            warm = ("well above normal" if r.peak_percentile >= 0.9
                    else "above normal" if r.peak_percentile >= 0.66
                    else "near normal" if r.peak_percentile >= 0.33
                    else "below normal" if r.peak_percentile >= 0.1
                    else "well below normal")
            L.append(f"    -> forecast peak {v.high:.1f} °C ranks {r.peak_percentile*100:.0f}th "
                     f"percentile of recorded highs ({warm})")
        L.append("")

    L.append("  COUNCIL MEMBERS (top-band independent NWP centers)")
    L.append(f"    {'center':22}{'raw H/L':>13}{'adj H/L':>13}"
             f"{'MAE H/L':>13}{'wt%H':>6}  n")
    for vote in v.votes:
        sk_h, sk_l = vote.skill_high, vote.skill_low
        raw = f"{_num(vote.raw_high,5)}/{_num(vote.raw_low,5)}"
        adj = f"{_num(vote.corrected_high,5)}/{_num(vote.corrected_low,5)}"
        mae = (f"{sk_h.mae_corrected:4.2f}/{sk_l.mae_corrected:4.2f}"
               if sk_h and sk_l else "   -/-   ")
        wt = v.weights_high.get(vote.spec.member_id)
        wt_s = f"{wt*100:4.0f}" if wt is not None else "   -"
        n = min(sk_h.n if sk_h else 0, sk_l.n if sk_l else 0)
        flag = "" if vote.eligible and vote.spec.member_id in v.weights_high else "  (set aside)"
        # Mark the rows Windy.com surfaces (ECMWF/GFS/ICON) as a transparent
        # cross-check — same model, skill-weighted here rather than shown raw.
        name = vote.spec.institution + (" [W]" if vote.spec.member_id in WINDY_MEMBERS else "")
        L.append(f"    {name:22}{raw:>13}{adj:>13}{mae:>13}{wt_s:>6}  {n}{flag}")
    L.append("    raw = center's forecast | adj = after backtested bias removal | "
             "wt% = blend weight for HIGH")
    if any(vote.spec.member_id in WINDY_MEMBERS for vote in v.votes):
        L.append("    [W] = a model Windy.com displays — your cross-check; the council "
                 "weights it by backtested skill, not Windy's raw value")
    L.append("")

    val = v.validation
    L.append("  BACKTEST VALIDATION (weights trained on older history, tested on held-out days)")
    if val.test_days and val.council_mae_high is not None:
        council = val.council_mae_high + val.council_mae_low
        L.append(f"    held-out days tested : {val.test_days}")
        L.append(f"    {'forecast':12}{'MAE high':>11}{'MAE low':>10}{'sum H+L':>10}  vs council")
        rows = [("council", val.council_mae_high, val.council_mae_low, None),
                ("naive avg", val.naive_mae_high, val.naive_mae_low, "the equal-weight mean of all centers"),
                ("persistence", val.persistence_mae_high, val.persistence_mae_low, "yesterday's observed value"),
                ("climatology", val.climatology_mae_high, val.climatology_mae_low, "the seasonal normal")]
        for name, hh, ll, _desc in rows:
            if hh is None or ll is None:
                continue
            delta = (hh + ll) - council
            tag = "" if name == "council" else (f"  {delta:+.2f} °C ({'better' if delta > 0 else 'worse'})")
            L.append(f"    {name:12}{hh:8.2f} °C{ll:7.2f} °C{hh+ll:8.2f} °C{tag}")
        L.append("    (council must beat all three reference forecasts to justify itself)")
        if (val.council_rmse_high is not None and val.council_mae_high
                and val.council_mae_low):
            rh, rl = val.council_rmse_high, val.council_rmse_low
            rath = rh / val.council_mae_high
            ratl = rl / val.council_mae_low
            # Gaussian errors give RMSE/MAE ≈ 1.25; markedly higher means a fat,
            # bust-prone tail — the structure that makes a single widening factor a
            # bad fix even when the 80% band under-covers.
            note = ("  — heavy tail: occasional big busts (a constant band-widening "
                    "would lift coverage but cost CRPS)" if max(rath, ratl) >= 1.35 else "")
            L.append(f"    council RMSE high {rh:.2f} / low {rl:.2f} °C "
                     f"(RMSE/MAE {rath:.2f}/{ratl:.2f}){note}")
        if val.council_win_rate is not None:
            L.append(f"    council closer than naive on {val.council_win_rate*100:.0f}% of held-out predictions")
        if val.hit_rate_2c is not None:
            L.append(f"    within ±2 °C on {val.hit_rate_2c*100:.0f}% of held-out predictions")
        if val.crps_council is not None:
            L.append("")
            L.append("    PROBABILISTIC SKILL (proper scoring rule — CRPS, °C; lower is better)")
            skill_s = (f"  -> skill {val.crps_skill*100:+.0f}%"
                       if val.crps_skill is not None else "")
            L.append(f"      CRPS council {val.crps_council:.3f}  vs dressed climatology "
                     f"{val.crps_climatology:.3f}{skill_s} "
                     f"({val.crps_n} scored days)")
            if val.coverage_80 is not None:
                cov = val.coverage_80 * 100
                cal = ("well-calibrated" if 75 <= cov <= 85
                       else "over-confident (under-dispersed)" if cov < 75
                       else "under-confident (over-dispersed)")
                L.append(f"      80% interval covers {cov:.0f}% of outcomes "
                         f"(width {val.sharpness_80:.1f} °C) — {cal}")
            L.append("      (the verdict's bucket probabilities are this distribution, "
                     "now scored — not asserted)")
        bv_pairs = [("high", val.bucket_verdict_high), ("low", val.bucket_verdict_low)]
        if getattr(val, "bucket_verdict_note", None):
            L.append("")
            L.append("    MARKET-BUCKET VERDICT (withheld)")
            L.append(f"      {val.bucket_verdict_note}")
        elif any(ev is not None for _, ev in bv_pairs):
            L.append("")
            L.append("    MARKET-BUCKET VERDICT (scored on the whole-degree bucket the "
                     "market settles on — measure-only)")
            for tag, ev in bv_pairs:
                if ev is None:
                    continue
                L.append(f"      {tag:5}names the settling bucket on "
                         f"{ev.hit_rate*100:.0f}% of held-out days "
                         f"(point verdict alone {ev.point_hit_rate*100:.0f}%; n={ev.n_scored})")
                # Localize the misses: a directional off-by-one (cloud centre lags
                # a moving bias — drift) vs boundary-driven (verdict right to within
                # rounding but on the wrong side of a settlement edge) vs gross error.
                directional = abs(ev.signed_bias) >= 0.15
                boundary = ev.fragility >= 0.05
                if directional:
                    lean = "COOL" if ev.signed_bias < 0 else "WARM"
                    L.append(f"            misses lean {lean} "
                             f"(under {ev.frac_under*100:.0f}% / over {ev.frac_over*100:.0f}%, "
                             f"bias {ev.signed_bias:+.2f} bucket) — directional; the residual "
                             f"cloud centre lags (lever: recency/trend-weight the residuals)")
                elif boundary:
                    L.append(f"            misses sit nearer a settlement edge than hits "
                             f"(edge {ev.mean_edge_miss:.2f} vs {ev.mean_edge_hit:.2f} °C) — "
                             f"boundary-driven (lever: sharpen the cloud / flag "
                             f"boundary-pinned verdicts)")
                else:
                    L.append(f"            misses are gross errors, not off-by-one "
                             f"(bias {ev.signed_bias:+.2f}, edge miss {ev.mean_edge_miss:.2f} vs "
                             f"hit {ev.mean_edge_hit:.2f} °C) — point accuracy is the limit")
            L.append("      (MEASURE-ONLY: the served verdict is unchanged; this scores "
                     "what the market pays on, not what CRPS measures)")
        rb = val.recency_bias
        if rb is not None:
            served = val.bias_halflife_served is not None
            L.append("")
            tag = ("APPLIED — this station serves recency-weighted bias"
                   if served else "recommend-only")
            L.append(f"    RECENCY-BIAS CHECK (does recency-weighting each member's bias "
                     f"sharpen the verdict? half-life {rb.halflife_days:.0f}d — {tag})")
            mae_delta = rb.mae_incumbent - rb.mae_candidate
            if served:
                # Recency is the served bias here; the audit confirms it still pays.
                verb = ("CONFIRMED" if rb.recommend
                        else "WARNING — served but no longer clears the floor")
                L.append(f"      ⮕ {verb}: the SERVED recency-weighted bias beats the "
                         f"plain trailing mean — held-out CRPS {rb.crps_candidate:.3f} vs "
                         f"{rb.crps_incumbent:.3f} ({rb.improvement_pct*100:+.1f}%, "
                         f"{rb.z:+.1f}σ past noise), bucket-hit "
                         f"{rb.bucket_hit_candidate*100:.0f}% vs "
                         f"{rb.bucket_hit_incumbent*100:.0f}%, point MAE "
                         f"{rb.mae_candidate:.3f} vs {rb.mae_incumbent:.3f} °C "
                         f"({-mae_delta:+.3f} °C; n={rb.n_paired}). The headline MAE/CRPS/"
                         f"bucket above already reflect this served correction.")
            elif rb.recommend:
                L.append(f"      ⮕ RECOMMEND: recency-weight the member bias. held-out CRPS "
                         f"{rb.crps_candidate:.3f} vs current {rb.crps_incumbent:.3f} "
                         f"({rb.improvement_pct*100:+.1f}%, {rb.z:+.1f}σ past noise), "
                         f"bucket-hit {rb.bucket_hit_candidate*100:.0f}% vs "
                         f"{rb.bucket_hit_incumbent*100:.0f}%, point MAE "
                         f"{rb.mae_candidate:.3f} vs {rb.mae_incumbent:.3f} °C "
                         f"({-mae_delta:+.3f} °C; n={rb.n_paired}). Surface for human review.")
            else:
                L.append(f"      no change recommended: recency bias does not beat the served "
                         f"plain-mean bias on the SERVED distribution past the noise floor "
                         f"(CRPS {rb.crps_candidate:.3f} vs {rb.crps_incumbent:.3f}, "
                         f"{rb.z:+.1f}σ; bucket-hit {rb.bucket_hit_candidate*100:.0f}% vs "
                         f"{rb.bucket_hit_incumbent*100:.0f}%; n={rb.n_paired}).")
                if mae_delta > 0.01:
                    L.append(f"        note: point MAE IS lower ({rb.mae_candidate:.3f} vs "
                             f"{rb.mae_incumbent:.3f} °C, {-mae_delta:+.3f} °C) — the headline °C "
                             f"verdict sharpens, but the cloud already absorbs that constant "
                             f"offset so the bucket the market settles on is unchanged.")
            # Per-attribute split: high and low settle as separate markets, so a
            # pooled recommend can hide an attribute on which recency does nothing.
            # When this station SERVES recency (applied to both attributes), an
            # attribute whose own audit does NOT clear the gate is recency applied
            # without its own justification — a tighten-to-this-attribute flag.
            per_attr = [("high", val.recency_bias_high), ("low", val.recency_bias_low)]
            if any(e is not None for _, e in per_attr):
                L.append("      per-attribute (each settles separately):")
                for name, e in per_attr:
                    if e is None:
                        L.append(f"        {name}: (too few paired days to score)")
                        continue
                    status = "RECOMMEND" if e.recommend else "no edge"
                    flag = ""
                    if served and not e.recommend:
                        flag = "  ⚠ SERVED here without its own justification"
                    L.append(f"        {name}: {status} — CRPS {e.crps_candidate:.3f} vs "
                             f"{e.crps_incumbent:.3f} ({e.improvement_pct*100:+.1f}%, "
                             f"{e.z:+.1f}σ), bucket-hit {e.bucket_hit_candidate*100:.0f}% vs "
                             f"{e.bucket_hit_incumbent*100:.0f}% (n={e.n_paired}).{flag}")
        cb = val.calibration
        if cb is not None:
            L.append("")
            L.append("    SELF-IMPROVEMENT CHECK (recommend-only — never auto-applied)")
            if cb.recommend:
                L.append("      ⮕ RECOMMEND: scale the predictive spread by per-day "
                         "member dispersion (conditional distribution).")
                L.append(f"        held-out CRPS {cb.crps_conditional:.3f} vs current "
                         f"{cb.crps_incumbent:.3f} ({cb.improvement_pct*100:+.1f}%, "
                         f"{cb.z:+.1f}σ past noise, disp↔|err| r={cb.disp_corr:+.2f}, "
                         f"n={cb.n_scored}). Surface for human review; the served "
                         f"verdict is unchanged.")
            else:
                L.append(f"      no change recommended: conditional spread (scale by "
                         f"member dispersion) does not beat the current single residual "
                         f"cloud past the noise floor "
                         f"(CRPS {cb.crps_conditional:.3f} vs {cb.crps_incumbent:.3f}, "
                         f"{cb.z:+.1f}σ, disp↔|err| r={cb.disp_corr:+.2f}, n={cb.n_scored}).")
        ss = val.spread_skill
        if ss is not None:
            L.append("")
            L.append("    SPREAD–SKILL CHECK (is member disagreement an honest per-day "
                     "uncertainty signal? — recommend-only)")
            L.append(f"      {ss.label}: consistency(disp,|err|) r={ss.consistency:+.2f}, "
                     f"relative-reliability gap {ss.reliability_gap*100:.0f}% over "
                     f"{len(ss.bins)} spread bins (n={ss.n}).")
            L.append(f"      averaging factor 1/α≈{ss.avg_members_factor:.1f}× — raw member "
                     f"spread overstates the blend's error by ~this (≈√members); the "
                     f"bucket cloud already uses the de-scaled error, so this only "
                     f"VERIFIES the spread, never changes it.")
        rh = val.rank_histogram
        pc = val.pit_calibration
        if rh is not None or pc is not None:
            L.append("")
            L.append("    ENSEMBLE-CALIBRATION CHECK (is the spread the right SIZE, and is the "
                     "served distribution honest? — recommend-only)")
            if rh is not None:
                d = rh.diag
                L.append(f"      rank histogram (raw NWP member panel — Talagrand): "
                         f"{rh.verdict} [{d.shape}], "
                         f"edge ratio {d.edge_ratio:.2f}, χ² z={d.z:+.1f}, n={d.n}.")
                L.append(f"        {rh.meaning}.")
            if pc is not None:
                d = pc.diag
                L.append(f"      PIT of the SERVED cloud (the distribution compare.py turns "
                         f"into bucket probabilities): {pc.verdict} [{d.shape}], "
                         f"χ² z={d.z:+.1f}, n={d.n}.")
                L.append(f"        {pc.meaning}.")
    else:
        L.append("    insufficient history in window for split-sample validation")
    L.append("")
    L.append(f"  Provenance: every figure above came from a live API call "
             f"({v.requests_made} sandboxed requests). No values are model-generated.")
    L.extend(_healthcheck_banner())          # read-only monitor status; never moves the verdict
    return "\n".join(L)


def _bucket_verdict_json(ev) -> dict | None:
    """Serialize a BucketVerdictEval for the report. None passes through."""
    if ev is None:
        return None
    return {
        "hit_rate": ev.hit_rate,
        "point_hit_rate": ev.point_hit_rate,
        "signed_bias_buckets": ev.signed_bias,
        "frac_over": ev.frac_over,
        "frac_under": ev.frac_under,
        "mean_edge_hit": ev.mean_edge_hit,
        "mean_edge_miss": ev.mean_edge_miss,
        "fragility": ev.fragility,
        "scored_days": ev.n_scored,
        "applied": False,
    }


def _recency_eval_json(ev) -> dict | None:
    """Serialize one RecencyBiasEval (pooled or single-attribute). None passes
    through. The per-attribute objects use this so a reader can see whether the
    pooled recommend is carried by one market or both."""
    if ev is None:
        return None
    return {
        "recommend": ev.recommend,
        "crps_incumbent": ev.crps_incumbent,
        "crps_candidate": ev.crps_candidate,
        "improvement_pct": ev.improvement_pct,
        "sigma_past_noise": ev.z,
        "bucket_hit_incumbent": ev.bucket_hit_incumbent,
        "bucket_hit_candidate": ev.bucket_hit_candidate,
        "paired_days": ev.n_paired,
    }


def verdict_to_dict(
    v: Verdict,
    comparison: VerdictMarketComparison | None = None,
    market_note: str | None = None,
    settlement_ref: dict | None = None,
    cross_reference: dict | None = None,
) -> dict:
    val = v.validation
    d = {
        "place": v.place.label(),
        "target": v.target,
        "verdict": {"high": v.high, "low": v.low, "confidence": v.confidence},
        "target_status": v.target_status,
        "target_basis": v.target_basis,
        "truth_source": v.truth_source,
        "settlement": v.settlement,
        "representativeness": {
            "offset_deg": v.representativeness.offset_deg,
            "neighbor_points": v.representativeness.neighbor_points,
            "sample_days": v.representativeness.sample_days,
            "spatial_sigma": {
                "high": v.representativeness.spatial_sigma_high,
                "low": v.representativeness.spatial_sigma_low,
            },
            "sigma": v.representativeness.sigma,
        },
        "confidence_detail": v.confidence_detail,
        "regime_consensus": regime_consensus(v),
        "live_spread": {"high": v.high_spread, "low": v.low_spread},
        "naive_baseline": {"high": v.naive_high, "low": v.naive_low},
        "members": [
            {
                "id": vote.spec.member_id,
                "institution": vote.spec.institution,
                "shown_by_windy": vote.spec.member_id in WINDY_MEMBERS,
                "raw": {"high": vote.raw_high, "low": vote.raw_low},
                "bias_corrected": {"high": vote.corrected_high, "low": vote.corrected_low},
                "mae_corrected": {
                    "high": vote.skill_high.mae_corrected if vote.skill_high else None,
                    "low": vote.skill_low.mae_corrected if vote.skill_low else None,
                },
                "bias": {
                    "high": vote.skill_high.bias if vote.skill_high else None,
                    "low": vote.skill_low.bias if vote.skill_low else None,
                },
                "samples": min(vote.skill_high.n if vote.skill_high else 0,
                               vote.skill_low.n if vote.skill_low else 0),
                "weight_high": v.weights_high.get(vote.spec.member_id),
                "weight_low": v.weights_low.get(vote.spec.member_id),
                "eligible": vote.eligible,
                "notes": vote.notes,
            }
            for vote in v.votes
        ],
        "validation": {
            "test_days": val.test_days,
            "council_mae": {"high": val.council_mae_high, "low": val.council_mae_low},
            "council_rmse": {"high": val.council_rmse_high, "low": val.council_rmse_low},
            "naive_mae": {"high": val.naive_mae_high, "low": val.naive_mae_low},
            "persistence_mae": {"high": val.persistence_mae_high, "low": val.persistence_mae_low},
            "climatology_mae": {"high": val.climatology_mae_high, "low": val.climatology_mae_low},
            "council_win_rate_vs_naive": val.council_win_rate,
            "hit_rate_within_2c": val.hit_rate_2c,
            "crps": {
                "council": val.crps_council,
                "climatology": val.crps_climatology,
                "skill_vs_climatology": val.crps_skill,
                "coverage_80": val.coverage_80,
                "sharpness_80": val.sharpness_80,
                "scored_days": val.crps_n,
            },
            "self_improvement_check": ({
                "method": "conditional predictive spread scaled by per-day member "
                          "dispersion (heteroscedastic distribution)",
                "recommend": val.calibration.recommend,
                "crps_conditional": val.calibration.crps_conditional,
                "crps_incumbent": val.calibration.crps_incumbent,
                "improvement_pct": val.calibration.improvement_pct,
                "sigma_past_noise": val.calibration.z,
                "dispersion_error_corr": val.calibration.disp_corr,
                "scored_days": val.calibration.n_scored,
                "applied": False,
            } if val.calibration is not None else None),
            "spread_skill": ({
                "label": val.spread_skill.label,
                "reliable": val.spread_skill.reliable,
                "tracks_error": val.spread_skill.tracks_error,
                "consistency": val.spread_skill.consistency,
                "reliability_gap": val.spread_skill.reliability_gap,
                "averaging_factor": val.spread_skill.avg_members_factor,
                "rmse": val.spread_skill.rmse,
                "mean_spread": val.spread_skill.mean_spread,
                "n": val.spread_skill.n,
                "applied": False,
            } if val.spread_skill is not None else None),
            "rank_histogram": ({
                "verdict": val.rank_histogram.verdict,
                "shape": val.rank_histogram.diag.shape,
                "edge_ratio": val.rank_histogram.diag.edge_ratio,
                "reduced_chi2": val.rank_histogram.diag.reduced_chi2,
                "z": val.rank_histogram.diag.z,
                "uniform": val.rank_histogram.diag.uniform,
                "bins": list(val.rank_histogram.diag.bins),
                "n": val.rank_histogram.n,
                "applied": False,
            } if val.rank_histogram is not None else None),
            "pit_calibration": ({
                "verdict": val.pit_calibration.verdict,
                "shape": val.pit_calibration.diag.shape,
                "edge_ratio": val.pit_calibration.diag.edge_ratio,
                "reduced_chi2": val.pit_calibration.diag.reduced_chi2,
                "z": val.pit_calibration.diag.z,
                "uniform": val.pit_calibration.diag.uniform,
                "bins": list(val.pit_calibration.diag.bins),
                "n": val.pit_calibration.n,
                "applied": False,
            } if val.pit_calibration is not None else None),
            "coverage_calibration": ({
                "method": "constant inflation factor on the served residual cloud, "
                          "scored per attribute (high and low clouds separately, the "
                          "same objects compare_high/compare_low resample), learned "
                          "online from realized out-of-sample coverage (split conformal)",
                "recommend": val.coverage_calibration.recommend,
                "candidate_factor": val.coverage_calibration.final_factor,
                "coverage_incumbent": val.coverage_calibration.coverage_incumbent,
                "coverage_calibrated": val.coverage_calibration.coverage_calibrated,
                "target": val.coverage_calibration.target,
                "under_sigma": val.coverage_calibration.under_sigma,
                "crps_calibrated": val.coverage_calibration.crps_calibrated,
                "crps_incumbent": val.coverage_calibration.crps_incumbent,
                "improvement_pct": val.coverage_calibration.improvement_pct,
                "sigma_past_noise": val.coverage_calibration.z,
                "scored_days": val.coverage_calibration.n_scored,
                "applied": False,
            } if val.coverage_calibration is not None else None),
            "bucket_verdict": {
                "method": "modal whole-degree settlement bucket (point verdict dressed "
                          "with the strictly-earlier residual cloud, rounded half-up) vs "
                          "the realized bucket, leak-free over the same walk-forward; "
                          "scored per attribute (high/low are separate markets); "
                          "measure-only, never moves the served verdict",
                "high": _bucket_verdict_json(val.bucket_verdict_high),
                "low": _bucket_verdict_json(val.bucket_verdict_low),
                "withheld": getattr(val, "bucket_verdict_note", None),
            },
            "recency_bias": ({
                "method": "recency-weight each member's bias with an exponential "
                          "half-life (vs the served plain training-mean bias), re-run "
                          "the SAME leak-free blend, score the candidate vs incumbent "
                          "prediction streams on paired per-day CRPS (SE-gated) AND the "
                          "whole-degree bucket hit-rate; recommend-only",
                "halflife_days": val.recency_bias.halflife_days,
                "recommend": val.recency_bias.recommend,
                "mae_incumbent": val.recency_bias.mae_incumbent,
                "mae_candidate": val.recency_bias.mae_candidate,
                "crps_incumbent": val.recency_bias.crps_incumbent,
                "crps_candidate": val.recency_bias.crps_candidate,
                "crps_improvement": val.recency_bias.crps_improvement,
                "improvement_pct": val.recency_bias.improvement_pct,
                "sigma_past_noise": val.recency_bias.z,
                "bucket_hit_incumbent": val.recency_bias.bucket_hit_incumbent,
                "bucket_hit_candidate": val.recency_bias.bucket_hit_candidate,
                "paired_days": val.recency_bias.n_paired,
                "halflife_served": val.bias_halflife_served,
                "applied": val.bias_halflife_served is not None,
                "per_attribute": {
                    "high": _recency_eval_json(val.recency_bias_high),
                    "low": _recency_eval_json(val.recency_bias_low),
                },
            } if val.recency_bias is not None else None),
        },
        "observation": {
            "current": v.observation.current,
            "recent_observed": [
                {"date": d, "high": h, "low": lo} for d, h, lo in v.observation.recent
            ],
            "backbone": v.observation.backbone,
        },
        "quality_control": v.qc,
        "computation_ensemble": {
            "member_count": v.ensemble.member_count,
            "models": v.ensemble.models,
            "mean": {"high": v.ensemble.mean_high, "low": v.ensemble.mean_low},
            "spread_1sigma": {"high": v.ensemble.spread_high, "low": v.ensemble.spread_low},
            "p10_p90_high": [v.ensemble.p10_high, v.ensemble.p90_high],
            "p10_p90_low": [v.ensemble.p10_low, v.ensemble.p90_low],
            "agreement_within_2c": {
                "high": v.ensemble.agreement_high, "low": v.ensemble.agreement_low,
            },
            "backtest_days": v.ensemble.backtest_days,
            "blend_eligible": v.ensemble.blend_eligible,
            "corrected_mean_high": v.ensemble.corrected_mean_high,
            "corrected_mean_low": v.ensemble.corrected_mean_low,
        },
        "records": {
            "since_year": v.records.since_year,
            "window_days": v.records.window_days,
            "sample_days": v.records.sample_days,
            "record_high": {"temp": v.records.record_high, "year": v.records.record_high_year},
            "record_low": {"temp": v.records.record_low, "year": v.records.record_low_year},
            "normal": {"high": v.records.normal_high, "low": v.records.normal_low},
            "peak_percentile": v.records.peak_percentile,
        },
        "diurnal": {
            "peak": {"temp": v.diurnal.peak_temp, "time": v.diurnal.peak_time},
            "trough": {"temp": v.diurnal.trough_temp, "time": v.diurnal.trough_time},
            "curve": [{"time": t, "temp": c} for t, c in v.diurnal.curve],
            "observed_peak_hour": {"mean": v.diurnal.obs_peak_hour, "sd": v.diurnal.obs_peak_sd},
            "observed_trough_hour": {"mean": v.diurnal.obs_trough_hour, "sd": v.diurnal.obs_trough_sd},
            "peak_time_consistent": v.diurnal.peak_in_band,
            "trough_time_consistent": v.diurnal.trough_in_band,
            "history_days": v.diurnal.history_days,
        },
        "interpretation": {
            "members_used": v.interpretation.members_used,
            "outliers_set_aside": v.interpretation.outliers_set_aside,
            "mean_bias_removed": {
                "high": v.interpretation.mean_bias_removed_high,
                "low": v.interpretation.mean_bias_removed_low,
            },
            "history_days": v.interpretation.history_days,
        },
        "requests_made": v.requests_made,
    }
    if comparison is not None:
        d["market_comparison"] = comparison_to_dict(comparison)
    elif market_note is not None:
        d["market_note"] = market_note
    if settlement_ref is not None:
        d["settlement_reference"] = settlement_ref
    if cross_reference is not None:
        d["anchor_cross_reference"] = cross_reference
    return d


def to_json(
    v: Verdict,
    comparison: VerdictMarketComparison | None = None,
    market_note: str | None = None,
    settlement_ref: dict | None = None,
    cross_reference: dict | None = None,
) -> str:
    return json.dumps(
        verdict_to_dict(v, comparison, market_note, settlement_ref,
                        cross_reference), indent=2)


def _intraday_verdict_bucket(f, v: Verdict) -> int | None:
    """The whole-degree bucket the verdict's own high settles into, under the
    SAME quantizer the intraday floor uses — so the two are directly comparable."""
    from weather_council.market import _native_reading_int
    if v.high is None:
        return None
    return _native_reading_int(v.high, "C", f.sub_degree)


def _intraday_lines(f, v: Verdict) -> list[str]:
    """Human-readable read-only block for the intraday dead-bucket annotation."""
    L = ["", "  INTRADAY DEAD-BUCKET ELIMINATION (read-only; today only)"]
    if f.kind == "not_basket":
        L.append(f"    {f.city} is not a configured settlement city — skipped.")
        return L
    if f.kind == "not_today":
        L.append(f"    {f.note}.")
        return L
    if f.kind == "unverified":
        L.append("    UNVERIFIED — live settlement feed gave no usable reading; "
                 "NO buckets eliminated.")
        if f.note:
            L.append(f"    reason: {f.note}")
        return L
    # kind == "floor"
    L.append(f"    running max so far: {f.running_max_c:.1f}°C "
             f"({f.n_obs} obs, {f.source}"
             + (f", {f.record_time}" if f.record_time else "") + ")")
    L.append(f"    settlement rule: {f.label}")
    L.append(f"    => the {f.floor_bucket}°C bucket is already GUARANTEED reached; "
             f"every bucket below {f.floor_bucket}°C is mechanically impossible.")
    vb = _intraday_verdict_bucket(f, v)
    if vb is not None and f.is_dead(vb):
        L.append(f"    !! the verdict's {vb}°C bucket is ALREADY DEAD — observed "
                 f"reality has overtaken the central pick (verdict not changed; "
                 f"investigate).")
    elif vb is not None:
        L.append(f"    verdict bucket {vb}°C is still live (consistent).")
    return L


def _intraday_to_dict(f, v: Verdict) -> dict:
    vb = _intraday_verdict_bucket(f, v)
    return {
        "kind": f.kind,
        "city": f.city,
        "target": f.target,
        "settlement_rule": f.label,
        "running_max_c": f.running_max_c,
        "record_time": f.record_time,
        "source": f.source,
        "n_obs": f.n_obs,
        "floor_bucket": f.floor_bucket,
        "verdict_bucket": vb,
        "verdict_bucket_dead": (vb is not None and f.is_dead(vb)),
        "note": f.note,
    }


def _ceiling_lines(c) -> list[str]:
    """Read-only block for the intraday-ceiling sharpening (lead-0 conviction)."""
    L = ["", "  INTRADAY-CEILING SHARPENING (read-only; today only — the conviction lever)"]
    if c.kind == "not_basket":
        L.append(f"    {c.city} is not a configured settlement city — skipped.")
        return L
    if c.kind == "not_today":
        L.append(f"    {c.note}.")
        return L
    if c.kind == "unavailable":
        L.append(f"    UNAVAILABLE — {c.note}.")
        return L
    # kind == "sharpened"
    L.append(f"    running max by {c.hour:02d}:00 local: {c.running_max_c:.1f}°C "
             f"({c.source})")
    L.append(f"    remaining-rise learned from {c.n_rise} strictly-earlier days "
             f"(leak-free, resampled through the settlement quantizer)")
    top = "  ".join(f"{b}°C {p*100:.0f}%" for b, p in c.pmf[:4])
    L.append(f"    sharpened final-max pmf: {top}")
    # Conviction is honest about the hour: early in the day the remaining rise is
    # large and the pmf stays diffuse; it concentrates only as the peak nears.
    pct = c.modal_prob * 100
    if c.modal_prob >= 0.70:
        L.append(f"    => HIGH-CONVICTION call: {c.modal_bucket}°C at {pct:.0f}% "
                 f"(vs ~56% day-ahead — σ has collapsed near/after the peak)")
    elif c.modal_prob >= 0.40:
        L.append(f"    => leaning {c.modal_bucket}°C at {pct:.0f}% — firming up as "
                 f"the peak nears (not yet high-conviction)")
    else:
        L.append(f"    => still diffuse ({c.modal_bucket}°C at {pct:.0f}%) — too "
                 f"early; conviction rises through the afternoon as the peak nears")
    return L


def _ceiling_to_dict(c) -> dict:
    return {
        "kind": c.kind, "city": c.city, "target": c.target, "hour": c.hour,
        "running_max_c": c.running_max_c, "n_rise": c.n_rise,
        "modal_bucket": c.modal_bucket, "modal_prob": c.modal_prob,
        "pmf": [{"bucket": b, "prob": p} for b, p in c.pmf],
        "source": c.source, "note": c.note,
    }


def _build_comparison(
    sources: Sources, v: Verdict, place, target
) -> tuple[VerdictMarketComparison | None, str | None]:
    """Fetch the matching read-only Polymarket market and place the model's
    bucket distribution beside it. Shares the run's SafeHTTPClient so the fetch
    counts against the same request budget the core pipeline uses.

    Returns (comparison, note). `comparison` is None when no market matches, the
    council kept too few held-out errors, or the market settles finer than its
    whole-degree bucket labels (HK); in that last case `note` explains why the
    comparison is withheld rather than fabricated."""
    residuals = v.validation.residuals_high
    if not residuals:
        return None, None
    markets = MarketData(http=sources.http).fetch_temperature_markets()
    market = match_market(markets, place.name, target)
    if market is None:
        return None, None
    source_check = (v.settlement or {}).get("source_check")
    bias_corr = applied_bias_correction(v, "high")
    # A sub-degree market (e.g. HK on the Observatory) settles on a different
    # station and finer than its labels. Rather than withhold outright, try to
    # measure the settlement-vs-backtest station offset from Meteostat and
    # transfer the verdict onto the settlement scale. Only if that offset cannot
    # be earned do we fall back to the explanatory withhold note.
    station_offset = None
    if market.settles_sub_degree():
        ts = (v.truth_source or {})
        if ts.get("kind") == "station" and (ts.get("station") or {}).get("id"):
            station_offset = measure_settlement_offset(
                sources, place, str(ts["station"]["id"]),
                market.station or "", target)
        if station_offset is None:
            return None, grain_support_note(market, v.high)
    return compare_high(market, v.high, residuals, source_check, bias_corr,
                        station_offset=station_offset), None


def _dump_wf_stream(out_dir: str, city: str, validation) -> None:
    """Write the leak-free per-day walk-forward stream to two CSVs (high, low).

    Columns: date,point,realized. The stream is exactly the (date, served point
    verdict, realized) triples the council's own held-out evaluation produced —
    no re-simulation, no leakage, measure-only. Feeds an external bucket-
    calibration backtest (monte-carlo/backtest_mc.py walkforward --data). Silent
    no-op for an attribute with an empty stream (too few held-out days)."""
    if validation is None:
        print("dump-stream: no validation panel (window too short?)", file=sys.stderr)
        return
    slug = "".join(c if c.isalnum() else "_" for c in city.strip().lower()).strip("_")
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    for attr, stream in (("high", getattr(validation, "wf_high", [])),
                         ("low", getattr(validation, "wf_low", []))):
        if not stream:
            print(f"dump-stream: {attr} stream empty, skipped", file=sys.stderr)
            continue
        path = d / f"{slug}_{attr}.csv"
        with path.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "point", "realized"])
            for date_iso, point, realized in stream:
                w.writerow([date_iso, f"{point:.4f}", f"{realized:.4f}"])
        print(f"dump-stream: wrote {len(stream)} rows -> {path}", file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Council-of-5 backtested weather verdict.")
    ap.add_argument("city", nargs="?", help="city name, e.g. 'Tokyo'")
    ap.add_argument("--lead", type=int, default=1,
                    help="days ahead (0=today, 1=tomorrow). default 1")
    ap.add_argument("--window", type=int, default=60,
                    help="backtest history window in days. default 60")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--market", action="store_true",
                    help="also fetch the matching Polymarket market (read-only) and "
                         "compare the model's per-bucket probabilities to the market's")
    ap.add_argument("--verify", action="store_true",
                    help="score past logged verdicts against observed temps")
    ap.add_argument("--edge", action="store_true",
                    help="settle logged market snapshots against the anchor station "
                         "and print the C7 council-vs-market calibration verdict "
                         "(read-only, recommend-only)")
    ap.add_argument("--intraday", action="store_true",
                    help="annotate which whole-degree buckets today's observed "
                         "running max has already ruled out, read off the live "
                         "settlement instrument (HKO / EGLC). Read-only, today only; "
                         "never moves the verdict.")
    ap.add_argument("--dump-stream", metavar="DIR",
                    help="write the leak-free per-day walk-forward stream "
                         "({city}_high.csv / {city}_low.csv, columns "
                         "date,point,realized) to DIR for external bucket-calibration "
                         "backtesting (monte-carlo/backtest_mc.py). Measure-only.")
    args = ap.parse_args(argv)

    try:
        if args.verify:
            lines = verify()
            print("\n".join(lines) if lines else "no past verdicts ready to verify yet")
            return 0

        if args.edge:
            settled = settle_market_snapshots()
            if settled:
                print("settled:")
                print("\n".join(f"  {s}" for s in settled))
            report = score_snapshots(fetch_settled_snapshots())
            print("\n".join(edge_report_lines(report)))
            return 0

        if not args.city:
            ap.error("a city is required (or use --verify)")
        if not (0 <= args.lead <= 15):
            ap.error("--lead must be between 0 and 15")
        if not (15 <= args.window <= 365):
            ap.error("--window must be between 15 and 365")

        sources = Sources()
        place = sources.geocode(args.city)
        # "today" is the place's own civil date, not the host's: a same-day
        # (lead 0) verdict for a city in another timezone must target the day the
        # forecast feed actually carries for that city (e.g. Hong Kong is already
        # "tomorrow" relative to a UTC-1 host). See sources.place_today.
        target = place_today(place) + dt.timedelta(days=args.lead)

        # Tropical-cyclone halt gate (HK only; no-op elsewhere). A risk control,
        # checked BEFORE verdict assembly: when a named TC's 5-day forecast cone
        # threatens Hong Kong the harness refuses a bucket rather than serve a
        # falsely confident one. A feed/parse failure is surfaced loudly and
        # never silently treated as all-clear (see weather_council/tc_gate.py).
        tc_unverified_note = None
        try:
            tc = tc_halt(place)
        except Exception as exc:  # defensive: the gate must never crash a verdict
            tc = None
            tc_unverified_note = f"TC GATE UNVERIFIED — gate errored: {exc}"
        if tc is not None and tc.is_halt:
            km = f"{tc.closest_km:.0f} km" if tc.closest_km is not None else "n/a"
            hrs = f"+{tc.within_hours}h" if tc.within_hours is not None else "n/a"
            line = (f"VERDICT: ABSTAIN — TC {tc.name} inside 5-day cone "
                    f"({tc.source}, asof {tc.asof_utc}); closest approach "
                    f"{km} at {hrs}")
            if args.json:
                print(json.dumps({"city": place.label(), "target": str(target),
                                  "verdict": "ABSTAIN", "reason": "tc_halt",
                                  "tc": {"name": tc.name, "source": tc.source,
                                         "asof_utc": tc.asof_utc,
                                         "closest_km": tc.closest_km,
                                         "within_hours": tc.within_hours}},
                                 indent=2))
            else:
                print(line)
            return 5
        if tc is not None and tc.is_unverified:
            tc_unverified_note = (f"TC GATE UNVERIFIED — could not confirm Hong "
                                  f"Kong is clear of tropical cyclones: {tc.name}")

        verdict = Council(sources).deliberate(place, target, args.window)
        log_verdict(verdict)

        if args.dump_stream:
            _dump_wf_stream(args.dump_stream, args.city, verdict.validation)

        # Whether C7 realized-outcome calibration has earned a validated edge yet
        # (DB read only, no network). Gates whether the mechanism-convergence
        # layer's nudge is ever ALLOWED to move the headline; until then it is
        # annotation only. Never True until ≥20 settled days beat the market.
        try:
            c7_validated = score_snapshots(fetch_settled_snapshots()).is_edge_validated
        except Exception:
            c7_validated = False

        comparison = None
        market_note = None
        if args.market:
            comparison, market_note = _build_comparison(sources, verdict, place, target)
            if comparison is not None:
                # Persist the comparison so C7 can grade it once the day settles
                # against the verdict's anchor station (recommend-only ledger).
                log_market_snapshot(verdict, comparison)

        # User-pinned settlement reference (e.g. London -> Wunderground EGLC):
        # always compare & contrast the verdict against that airport's record.
        settlement_ref = _settlement_reference(sources, place, target, verdict)
        # Non-airport-anchored city (e.g. Hong Kong -> Observatory): surface the
        # nearby airport as a measured cross-reference to the anchor.
        cross_reference = _anchor_cross_reference(sources, place, target, verdict)

        # Read-only intraday dead-bucket annotation (today only); never mutates
        # the verdict — observed reality only ever RULES OUT low buckets.
        intraday = ceiling = None
        if args.intraday:
            try:
                intraday = intraday_floor(place, target, sources=sources)
            except Exception as exc:
                print(f"intraday annotation errored (verdict unaffected): {exc}",
                      file=sys.stderr)
            try:
                ceiling = intraday_ceiling(place, target, sources=sources)
            except Exception as exc:
                print(f"intraday-ceiling errored (verdict unaffected): {exc}",
                      file=sys.stderr)

        if args.json:
            d = verdict_to_dict(verdict, comparison, market_note, settlement_ref,
                                cross_reference)
            if intraday is not None:
                d["intraday"] = _intraday_to_dict(intraday, verdict)
            if ceiling is not None:
                d["intraday_ceiling"] = _ceiling_to_dict(ceiling)
            d["bucket_call"] = _bucket_call(verdict, ceiling)
            print(json.dumps(d, indent=2))
        else:
            print(render(verdict, comparison, settlement_ref, cross_reference,
                         c7_validated=c7_validated, ceiling=ceiling))
            if args.market and comparison is None:
                if market_note:
                    print("\n  MARKET COMPARISON (withheld)\n    " + market_note)
                else:
                    print("\n  (no open Polymarket market matched this city/day — "
                          "nothing to compare)")
            if intraday is not None:
                print("\n".join(_intraday_lines(intraday, verdict)))
            if ceiling is not None:
                print("\n".join(_ceiling_lines(ceiling)))
        # A blind TC gate is reported loudly alongside the verdict so the
        # operator knows the HK risk control could not confirm safety this run.
        if tc_unverified_note:
            banner = "!" * 60
            print(f"\n{banner}\n  {tc_unverified_note}\n{banner}",
                  file=sys.stderr)
        return 0
    except RateLimitError as exc:
        print(f"upstream rate-limited (transient — retry shortly): {exc}",
              file=sys.stderr)
        return 3
    except SecurityError as exc:
        print(f"blocked by sandbox / validation: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
