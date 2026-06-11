#!/usr/bin/env python3
"""live_nwp_point — the LIVE feed connection the prediction loop was blocked on.

Until now every tail/extreme and confidence gate in this stack treated the live NWP
point as REQUIRES-LIVE and refused to fabricate it, because the council CSVs are FROZEN
historical files whose right edge is pinned by the ~38-day station-truth lag. The point
column in those CSVs is a real Open-Meteo forecast, but it stops at 2026-04-30 for HK —
so a forward prediction (e.g. 2026-06-10) had to fall back to climatology.

This tool closes that gap HONESTLY: it polls the SAME seven operational models the live
council uses (agents.COUNCIL) through the SAME sandboxed client (SafeHTTPClient: HTTPS
only, host-allowlist = the Open-Meteo endpoints, SSRF guard, per-hop redirect revalidation,
request budget), for a future target date, and returns a per-model + ensemble summary.
It is RECOMMEND-ONLY: it prints a forward point; it never moves a served verdict, never
trades, and never writes into the frozen CSVs.

Two parts, deliberately split so the logic is testable without a network:
  * ensemble_summary(quotes)  — PURE aggregation (mean, cross-model spread, count). Has a
                                known-answer selftest + a falsifiable control (all-None ->
                                empty; a dropped model is excluded, not zero-filled).
  * fetch_live_ensemble(...)  — the network call, isolated behind SafeHTTPClient. This is
                                the REQUIRES-LIVE half; it raises rather than invent a value.

Stdlib only. The grid-cell forecast can sit ~1-2 degC off the settlement station (HK
Royal Observatory / London City) — the council's station_offset corrector handles that in
production; here the raw grid ensemble is reported WITH that caveat stated, not hidden.
"""
from __future__ import annotations

import datetime as dt
import os
import statistics
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

# The seven operational members the live council polls (weather_council/agents.py COUNCIL).
# Hardcoded as ids (not imported) so this tool stays standalone, but kept in lockstep by
# this comment: ecmwf_ifs025, ukmo_seamless, gfs_seamless, icon_seamless,
# meteofrance_seamless, gem_seamless, jma_seamless.
COUNCIL_MODELS = [
    ("ecmwf",  "ecmwf_ifs025",        "ECMWF IFS"),
    ("ukmo",   "ukmo_seamless",       "UK Met Office"),
    ("gfs",    "gfs_seamless",        "NOAA GFS"),
    ("icon",   "icon_seamless",       "DWD ICON"),
    ("arpege", "meteofrance_seamless", "Meteo-France ARPEGE"),
    ("gem",    "gem_seamless",        "Env. Canada GEM"),
    ("jma",    "jma_seamless",        "JMA"),
]
# AI-NWP CROSS-CHECK — tested and DELIBERATELY NOT a member (ai_nwp_validate, crux):
# ECMWF AIFS (Open-Meteo `ecmwf_aifs025_single`, allowlisted) was evaluated as a Tier-3
# cross-check on its 21-day live overlap vs HKO truth (May19-Jun8 2026). Three findings ->
# NO-BAKE: (1) AIFS runs a systematic -0.79degC COOL bias vs the 7-model physical ensemble
# (sd 0.51); (2) adding it as an 8th member made raw-grid MAE WORSE (1.881 -> 1.963); (3) the
# AIFS-vs-ensemble divergence does NOT flag forecast risk (corr(|div|,|err|) = -0.14, n=16;
# mean |err| above-median-divergence 1.77 vs below 1.97 — backwards). So AIFS is FEASIBLE but
# NOT additive: it is neither a primary member nor a validated confidence flag. Re-test when
# the AIFS history window grows (it is single-regime/small-n now). GraphCast is not served by
# Open-Meteo (HTTP 400). Keep the council physical-only.

# Settlement anchors (station coordinates, not a city centroid). HK settles on the Royal
# Observatory record; London on EGLC (per the user-pinned settlement reference).
STATIONS = {
    "hong_kong": (22.302, 114.174, "Asia/Hong_Kong", "HK Royal Observatory"),
    "london":    (51.505, 0.055,   "Europe/London",  "London City (EGLC)"),
}

LIVE_URL = "https://api.open-meteo.com/v1/forecast"


# ---------------------------------------------- HK settlement station-offset corrector
# Open-Meteo gridded temperature_2m_max systematically UNDER-reads the HKO Absolute Daily
# Max (the Polymarket settlement truth): the urban HKO station peaks higher and sharper
# than the smoothed hourly grid cell. Measured over 1946 days (2021-01..2026-04) pairing
# gathered HKO CLMMAXT truth with Open-Meteo ERA5 archive tmax for the same cell
# (offset_backtest; reports/hko_observatory_daily_2021_2026.csv):
#
#     season    offset  sd    n     note
#     DJF       +0.99  1.24  510    (per-season both-halves untested)
#     MAM       +2.02  1.52  521
#     JJA       +2.60  1.22  460    JJA-dry h1 +3.36 / h2 +2.27  -> DRIFTING
#     SON       +1.93  1.27  455
#   JJA-dry     +2.81  1.28  208
#   JJA-wet     +2.43  1.15  252
#
# CRUX / overfit-guard: the JJA-dry offset is NOT both-halves stable (early years ~+3.4,
# recent years ~+2.3). An independent 1-week forecast-grid cross-check gave +2.34 (dry),
# which AGREES with the recent half -> we use a drift-aware, recent-leaning central (+2.4),
# NOT the inflated full-period mean (+2.81), and carry the full sd (~1.3) as residual
# uncertainty. This is a CALIBRATION WITH STATED INSTABILITY, not a constant.
#
# OUT-OF-SAMPLE (Jun 1-8 2026, archive grid vs user-observed HKO absmax):
#   * DRY branch VALIDATED: clean dry days Jun 1/2/3 corrected residuals -0.10/-0.60/+0.20
#     (rmse 0.37) -- the +2.40 dry offset reproduces the settlement value well. This is the
#     Jun-10 regime (forecast 0.8mm). One dry OUTLIER (Jun 4: grid over-read, residual -2.7)
#     -> the sd ~1.3 and a real left tail are kept, NOT narrowed.
#   * WET branch is a LUMPED 5y mean and OVER-corrects heavy rain: Jun 6 (71mm) corrected
#     residual -4.83 (grid over-reads on rain-cooled days). DO NOT trust the wet corrected
#     high on heavy-rain (>~20mm) days.
#   * WET-INTENSITY SPLIT BACKTEST (wet_intensity_split, JJA 2021-25, n=460) — NO-BAKE (crux):
#       dry<5 +2.81(n208,UNSTABLE) | wet5-20 +2.67(n174,UNSTABLE) | heavy20-50 +1.95(n65,STABLE)
#       | torr>=50 +1.62(n13,STABLE) | flat-wet +2.43(n252). A real, MONOTONE, both-halves-STABLE
#       gradient: >=20mm bin +1.89 (h1 +2.06/h2 +1.73, |d|0.33), delta -0.54 vs flat wet.
#       PRE-REGISTERED gate (delta<-0.6 AND stable) -> FAILS magnitude leg (-0.54), so NOT baked
#       (no goalpost-moving). And it would NOT have fixed the headline Jun-6 -4.83: the torrential
#       bin is the TIGHTEST (sd 0.74), so that miss is a single-day GRID BLOWOUT (tail), not a
#       bin-mean error. Real fix = wet-day ensemble-DISAGREEMENT detection + wider sigma, NOT a
#       central-offset branch. Disclosed; flat WET kept; heavy-wet centrals treated as upper bounds.
#
# MAY 2026 CROSS-REFERENCE (31 days, archive grid vs user-observed HKO absmax) — two findings
# that mean these baked centrals run HIGH for 2026 and must be treated as upper bounds:
#   * CROSS-SEASONAL DRIFT confirmed: 2026 offsets run ~0.6-0.8 BELOW the multi-year means.
#     May 2026 dry offset +1.34 (vs baked MAM +2.02 -> over-corrects by ~0.85, rmse still
#     drops 1.53->1.29). June 2026 dry ~+1.6-2.2 (vs baked JJA-dry +2.40). The whole table
#     is biased high by the early-year-heavy multi-year fit; a recent-years refit is owed.
#   * RECOVERY-DAY SUPPRESSION: days right after rain have small/negative offsets (May 6
#     after rain -0.40; May 15-18 wet spell -0.5..+0.8). A "dry" day that is really a post-
#     rain recovery (e.g. 2026-06-10, dry after the Jun6-9 wet spell) should use a LOWER
#     offset (~+1.0-1.5), not the established-dry +2.40. No recovery flag is baked yet.
# APRIL 2026 CROSS-REFERENCE (30 days, archive grid vs user-observed HKO absmax) — DRIFT
# now confirmed a THIRD consecutive month, hardening the "baked centrals run HIGH for 2026":
#   * April 2026 raw offset +1.43 (sd 0.83); DRY +1.48, WET +1.35. vs baked MAM +2.02 the
#     corrector over-corrects by -0.59 on average (rmse 1.65 raw -> 1.02 corrected: still a
#     net improvement, but with a SYSTEMATIC negative residual = the table is too high).
#   * Three 2026 months now agree the dry-regime offset is ~+1.4, NOT +2.0-2.8:
#       Apr-dry +1.48 | May-dry +1.17..1.34 | Jun-dry(recovery) ~+1.3.  This is a robust,
#       multi-month OOS signal that 2026 sits ~0.6-0.9 below the early-year-heavy fit.
#   * Regime note: April 2026 ran +2.3C vs Climatological Normal (warm anomaly, warmer than
#     May's +0.4) yet the GRID offset still came in low -> the drift is a grid/station
#     coupling shift, NOT merely a cool year. The refit should be regime-weighted, not a
#     blanket subtraction.
# Each entry: (central_offset_degC, residual_sd_degC).
# RECENCY REFIT (drift_refit, held-out validated): an exp recency-weighted offset (18-mo
# half-life) beat the flat all-years fit on a HELD-OUT last-6-months window (MAE 1.04->0.86,
# -17%), both-halves STABLE (h1 +1.43 / h2 +1.26). The validated deltas (recency - flat) are
# applied to the centrals below: DJF -0.27, MAM -0.36, JJA -0.22, SON -0.23. CAVEAT: the
# holdout window is Dec..Apr, so DJF/MAM are DIRECTLY validated; the JJA shift is recency-
# EXTRAPOLATED (no recent summer in the holdout) and the 2026 direct-OOS (Apr+May+Jun dry
# ~+1.4) argues JJA_dry runs even lower THIS year -> for a specific summer day prefer the
# live regime offset, keep the wide sd here.
#
# RECOVERY BRANCH (recovery_branch backtest) — a hypothesis PARTLY FALSIFIED, partly kept:
#   * A GLOBAL recovery-day suppression (dry day with prior-3d rain >=10mm) is REFUTED: pooled
#     across seasons recovery-dry +2.32 > established-dry +1.61 (WRONG sign) and both-halves
#     UNSTABLE (h1 +0.41 / h2 +0.99). The pooled effect is a SIMPSON'S-PARADOX artifact —
#     recovery days cluster in JJA (the highest-offset season). DO NOT bake a global branch.
#   * WITHIN JJA the suppression is REAL and both-halves STABLE: established-dry +3.23 vs
#     recovery-dry +2.60 = -0.62 (h1 -0.83 / h2 -0.42, |diff| 0.41, n_rec=137). This IS a
#     both-halves-verified ordering (unlike the dry-vs-wet margin), so it is baked ONLY for
#     JJA: JJA_dry_recovery = JJA_dry(+2.18) - 0.62 = +1.56. Routed when month in JJA AND dry
#     AND prior-3d rain >=10mm (e.g. 2026-06-10, dry after the Jun6-8 wet spell).
STATION_OFFSET_HK = {
    "DJF":              (0.72, 1.24),  # 0.99 - 0.27 (refit, held-out validated)
    "MAM":              (1.66, 1.52),  # 2.02 - 0.36 (refit, held-out validated)
    "SON":              (1.70, 1.27),  # 1.93 - 0.23 (refit)
    "JJA_wet":          (2.21, 1.15),  # 2.43 - 0.22 (refit, JJA recency-extrapolated)
    "JJA_dry":          (2.18, 1.30),  # 2.40 - 0.22 (refit); 2026 direct-OOS suggests ~+1.4
    "JJA_dry_recovery": (1.56, 1.20),  # 2.18 - 0.62 (within-JJA recovery, both-halves STABLE)
}


def _season_key(month: int, dry: bool, recovery: bool = False) -> str:
    if month in (6, 7, 8):
        if not dry:
            return "JJA_wet"
        return "JJA_dry_recovery" if recovery else "JJA_dry"
    if month in (12, 1, 2):
        return "DJF"
    if month in (3, 4, 5):
        return "MAM"
    return "SON"


def hk_settlement_offset(month: int, dry: bool, recovery: bool = False):
    """(central_offset_degC, residual_sd_degC) to ADD to an Open-Meteo HK grid tmax to
    estimate the HKO Absolute Daily Max settlement value. dry := forecast precip < 5mm.
    recovery := a dry JJA day with prior-3-day rain >=10mm (post-rain), which has a
    both-halves-validated -0.62degC suppression vs an established-dry JJA day.

    CAVEAT: the WET branch is a lumped 5-year mean. Out-of-sample (Jun 2026) it OVER-corrects
    heavy-rain days (the grid over-reads when rain cools the real peak), so a wet corrected
    high is LOW-CONFIDENCE on >~20mm days pending a heavy-rain split. The DRY branch is OOS-
    validated (Jun 1-3 2026 corrected rmse 0.37). A global recovery branch was FALSIFIED
    (Simpson's paradox); recovery is honoured ONLY inside JJA."""
    return STATION_OFFSET_HK[_season_key(month, dry, recovery)]


def corrected_hk_high(grid_high: float, month: int, dry: bool, recovery: bool = False):
    """(corrected_high_degC, residual_sd_degC): adds the season/precip-conditioned offset.
    The sd is the residual spread of the offset itself — combine in quadrature with the
    cross-model ensemble spread to get the full predictive sigma."""
    off, sd = hk_settlement_offset(month, dry, recovery)
    return grid_high + off, sd


# -------------------------------------------- London (EGLC) settlement station-offset
# Unlike HK (Royal Observatory peaks well above the smoothed grid cell), London settles ON
# EGLC London City Airport (the user-pinned Polymarket reference), so the Open-Meteo grid cell
# at the EGLC coords IS essentially the station — the offset is SMALL and positive, not the
# +1.6..+2.2degC HK lift. Derived (london_offset_backtest) from 1825 days (2021-2025) pairing
# EGLC settlement-grade daily highs (IEM ASOS METAR reconstruction, grain=C) with Open-Meteo
# archive tmax at the same cell. ALL FOUR seasons are both-halves STABLE:
#     season  offset  sd    n     h1/h2          note
#     DJF     +0.66  0.77  450   +0.70/+0.62   |d|0.08 stable
#     MAM     +0.35  1.01  460   +0.49/+0.22   |d|0.26 stable
#     JJA     +0.59  0.92  460   +0.66/+0.52   |d|0.14 stable
#     SON     +0.51  0.75  455   +0.52/+0.50   |d|0.02 stable
# CRUX decisions (what was tested and NOT baked):
#   * DRY/WET split: dry runs ~0.15degC above wet in every season and is both-halves stable,
#     but the delta (~0.15) is immaterial vs sd ~0.9 -> NOT baked (season-only is enough).
#   * No RECOVERY branch: post-rain suppression is an HK monsoon phenomenon; London is not
#     monsoon-driven and the wet/dry gap is tiny, so no recovery routing.
#   * 2026 OUT-OF-SAMPLE DRIFT (the same bug as HK): 2026 runs ~0.3-0.5degC BELOW the multi-year
#     fit (DJF +0.41, MAM -0.10, JJA +0.04 vs baked +0.66/+0.35/+0.59). The baked centrals are
#     the STABLE multi-year baseline; the city-agnostic adaptive_offset() rides on top and self-
#     heals this drift toward the realized ~0 — exactly the two-layer HK architecture.
# Each entry: (central_offset_degC, residual_sd_degC).
STATION_OFFSET_LONDON = {
    "DJF": (0.66, 0.77),
    "MAM": (0.35, 1.01),
    "JJA": (0.59, 0.92),
    "SON": (0.51, 0.75),
}


def _london_season_key(month: int) -> str:
    if month in (12, 1, 2):
        return "DJF"
    if month in (3, 4, 5):
        return "MAM"
    if month in (6, 7, 8):
        return "JJA"
    return "SON"


def london_settlement_offset(month: int):
    """(central_offset_degC, residual_sd_degC) to ADD to an Open-Meteo London grid tmax to
    estimate the EGLC London City Airport daily-max settlement value. Season-only: the EGLC
    offset is small (+0.35..+0.66) and the dry/wet split was immaterial (~0.15degC) so it is
    not baked. 2026 runs ~0.3-0.5 below this multi-year baseline (drift) -> let adaptive_offset
    track the live regime on top."""
    return STATION_OFFSET_LONDON[_london_season_key(month)]


def corrected_london_high(grid_high: float, month: int):
    """(corrected_high_degC, residual_sd_degC): adds the season-conditioned EGLC offset.
    Combine the sd in quadrature with the cross-model ensemble spread for the full sigma."""
    off, sd = london_settlement_offset(month)
    return grid_high + off, sd


# -------------------------------------------------- Tier-2: cross-model disagreement read
def disagreement_flag(spread: float) -> str:
    """Tier-2 read of the live cross-model spread. A wide spread (especially in a convective /
    wet regime) means the grid->station corrector and the baked offset-sd are LESS trustworthy
    that day, so the predictive sigma should widen and the prior carry more weight. Thresholds
    from the JJA spread distribution (~0.7 typical; >1.3 is a top-decile disagreement day).

    CRUX / NO-BAKE (wet_sigma_backtest, JJA 2021-25, n=460): a REGIME-conditioned sigma (wider
    per-day sd on rainy days) was tested and FALSIFIED — corrected-high residual SD is FLAT
    across rain bins (dry 1.24 / wet5-20 1.07 / heavy>=20 1.12; heavy/dry ratio 0.90) and a
    regime sigma calibrated WORSE than the flat baked offset-sd (z^2 1.402 vs 1.345). The wet
    signal lives in the CENTRAL BIAS (dry runs +1.04 low, heavy -0.32 high) — handled by
    adaptive_offset — and in a FAT TAIL (the Jun-6 -4.83 blowout sits inside the TIGHTEST bin),
    not in a bin-mean sigma. So sigma stays = sqrt(offset_sd^2 + cross_model_spread^2); the live
    cross-model spread (this flag) is the only validated per-day widener (it trims the offset-sd-
    only overconfidence toward z^2 ~ 1)."""
    if spread > 1.3:
        return "HIGH (>1.3: top-decile model disagreement — widen sigma, lean on prior)"
    if spread > 0.9:
        return "moderate (>0.9)"
    return "low (models agree)"


# ---- ADAPTIVE (drift-aware) offset -------------------------------------------------------
# The #1 recurring accuracy bug, confirmed in BOTH HK and London: the baked, history-derived
# grid->station offset OVER-LIFTS vs the current year because it never sees the current
# season's OWN realized residuals (2026 HK dry ran ~+1.4 vs baked +2.18; 2026 London ran ~0
# vs history +0.9). FIX: shrink the trailing realized offset toward the baked seasonal value
# with a Bayesian pseudo-count, so the corrector tracks the live regime and self-heals drift.
# VALIDATED (adaptive_offset_backtest, HK walk-forward 2022-25, LEAK-FREE trailing<t, K=30d,
# kappa=10): all-season MAE 1.096 -> 0.982 (-10%), BOTH halves win (h1 -0.199, h2 -0.029).
# JJA-only wins overall (-0.112) and big in h1 (-0.245); h2 is a WASH (+0.020, within MCSE) ->
# it NEVER significantly hurts, often helps. CAVEAT: on an extreme-heat regime-break day the
# trailing mean can UNDER-correct (2025-06-10 needed +4.40; static +2.18, adaptive +1.70 both
# missed) — the seasonal offset itself under-reads extreme heat, a SEPARATE issue. Recommend-
# only; NOT wired into the served run.py path (composed opt-in by the sims).
def adaptive_offset(static_off: float, trailing_realized, n_trailing: int, kappa: int = 10):
    """Pseudo-count shrink of the trailing realized offset toward the baked seasonal
    static_off. n_trailing large -> trust the recent settlement record; sparse/None -> fall
    back to static. Pure + deterministic so it is trivially known-answer testable.
    Validated 2022-2025 HK walk-forward (leak-free, both halves): -10% MAE vs static.

    Two REFINEMENTS pre-registered (K=30,kappa=10,min-match=6,floor=0.8) and NO-BAKED:
      (H1) regime-matched trailing (average only same dry/wet trailing days): all-season MAE
      0.973 vs plain 0.982 -- a real but ~0.01degC gain, and it FAILS its stated purpose in
      JJA (0.968 vs plain 0.966, h2 +0.019 worse than static). Below noise; not worth the
      regime-classification complexity.
      (H2) dispersion sigma (predictive sd = regime-matched trailing-offset stdev, floored):
      OVERCONFIDENT on every window (mean z^2 1.34-1.39 vs fixed-sigma 0.81-0.99; 2sigma cov
      0.90 vs 0.97). The baked residual sd already calibrates near-perfectly in JJA (z^2 0.99)
      because trailing-offset stdev misses the forecast-error component. Keep the baked sigma."""
    if trailing_realized is None or n_trailing <= 0:
        return static_off
    return (n_trailing * trailing_realized + kappa * static_off) / (n_trailing + kappa)


# ----------------------------------------------------------------- pure aggregation
def ensemble_summary(quotes: dict) -> dict:
    """Summarise per-model (high, low) quotes into a council point.

    quotes: {model_id: (high, low) or None}. Models that returned None (model has no
    grid value for that lead, or a transport error the caller chose to swallow) are
    EXCLUDED from the mean and spread — never zero-filled, which would bias the point
    toward 0 degC. Returns means, cross-model sample sd (the live disagreement spread,
    distinct from a residual sd), the contributing model list, and per-model dicts."""
    highs, lows, contributing = [], [], []
    for mid, q in quotes.items():
        if q is None:
            continue
        hi, lo = q
        if hi is None or lo is None:
            continue
        highs.append(float(hi))
        lows.append(float(lo))
        contributing.append(mid)
    n = len(contributing)
    def _mean(xs):
        return statistics.fmean(xs) if xs else float("nan")
    def _sd(xs):
        return statistics.pstdev(xs) if len(xs) >= 2 else 0.0
    return {
        "n": n,
        "models": contributing,
        "high_mean": _mean(highs),
        "high_spread": _sd(highs),
        "low_mean": _mean(lows),
        "low_spread": _sd(lows),
        "highs": dict(zip(contributing, highs)),
        "lows": dict(zip(contributing, lows)),
    }


# --------------------------------------------------------------- the live network half
def _pair_for_target(daily: dict, target_iso: str):
    """Pull (high, low) for target_iso out of an Open-Meteo daily block, or None."""
    times = daily.get("time", []) or []
    highs = daily.get("temperature_2m_max", []) or []
    lows = daily.get("temperature_2m_min", []) or []
    if target_iso not in times:
        return None
    i = times.index(target_iso)
    if i >= len(highs) or i >= len(lows):
        return None
    hi, lo = highs[i], lows[i]
    if hi is None or lo is None:
        return None
    return float(hi), float(lo)


def fetch_live_ensemble(lat: float, lon: float, tz: str, target: dt.date,
                        models=COUNCIL_MODELS, http=None) -> dict:
    """Poll each council model's live forecast for `target` through SafeHTTPClient.

    Returns {model_id: (high, low) or None}. REQUIRES-LIVE: the network errors are NOT
    swallowed into fabricated values — a model that errors or has no grid value for the
    lead comes back None and is excluded by ensemble_summary. Raises SecurityError only
    if the client itself cannot be constructed (no allowlisted egress)."""
    from weather_council.security import SafeHTTPClient, SecurityError
    http = http or SafeHTTPClient()
    today = dt.datetime.now(_zone(tz)).date()
    lead = (target - today).days
    forecast_days = max(lead + 1, 1)
    target_iso = target.isoformat()
    quotes = {}
    for _mid, model, _label in models:
        try:
            data = http.get_json(LIVE_URL, {
                "latitude": lat, "longitude": lon,
                "daily": "temperature_2m_max,temperature_2m_min",
                "timezone": tz, "models": model, "forecast_days": forecast_days,
            })
            quotes[model] = _pair_for_target(data.get("daily", {}), target_iso)
        except SecurityError:
            quotes[model] = None
    return quotes


def _zone(tz):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz)
    except Exception:
        return dt.timezone.utc


# ----------------------------------------------------------------------- selftest
def _selftest() -> int:
    fails = []

    def check(box, cond, msg):
        print(f"   [{'PASS' if cond else 'FAIL'}] {msg}")
        if not cond:
            fails.append(f"{box}: {msg}")

    print("=" * 84)
    print("live_nwp_point — known-answer selftest for the PURE aggregation (no network)")
    print("=" * 84)

    # -- Box A: aggregation recovers the right mean/spread on a known quote set --------
    print("\n[A] ensemble_summary recovers mean + cross-model spread (known answer)")
    quotes = {"m1": (30.0, 24.0), "m2": (32.0, 26.0), "m3": (28.0, 22.0)}
    s = ensemble_summary(quotes)
    check("A", s["n"] == 3, f"all 3 models contribute (n={s['n']})")
    check("A", abs(s["high_mean"] - 30.0) < 1e-9,
          f"high mean = {s['high_mean']:.3f} (known 30.0)")
    check("A", abs(s["low_mean"] - 24.0) < 1e-9,
          f"low mean = {s['low_mean']:.3f} (known 24.0)")
    check("A", abs(s["high_spread"] - statistics.pstdev([30.0, 32.0, 28.0])) < 1e-9,
          f"high spread = {s['high_spread']:.3f} (pstdev of the three)")

    # -- Box B: falsifiable control -- dropped models are EXCLUDED, never zero-filled --
    print("\n[B] control: None quotes are excluded (not zero-filled), all-None -> empty")
    q2 = {"m1": (30.0, 24.0), "m2": None, "m3": (None, 22.0)}
    s2 = ensemble_summary(q2)
    check("B", s2["n"] == 1 and abs(s2["high_mean"] - 30.0) < 1e-9,
          f"one valid model -> n=1, mean=30.0 (a zero-fill would drag it to ~{30.0/3:.1f})")
    s3 = ensemble_summary({"m1": None, "m2": None})
    check("B", s3["n"] == 0 and s3["high_mean"] != s3["high_mean"],
          "all-None -> n=0 and NaN mean (no fabricated point)")

    # -- Box C: target extraction picks the right index out of a daily block ----------
    print("\n[C] _pair_for_target indexes the right day, returns None when absent")
    daily = {"time": ["2026-06-09", "2026-06-10", "2026-06-11"],
             "temperature_2m_max": [27.0, 28.3, 27.9],
             "temperature_2m_min": [23.0, 24.0, 25.6]}
    check("C", _pair_for_target(daily, "2026-06-10") == (28.3, 24.0),
          "target 2026-06-10 -> (28.3, 24.0)")
    check("C", _pair_for_target(daily, "2026-07-01") is None,
          "absent target -> None (no guess)")

    # -- Box D: HK settlement-offset corrector (known answer + falsifiable ordering) ---
    print("\n[D] hk settlement-offset corrector: known central, season routing, ordering")
    ch, csd = corrected_hk_high(27.6, 6, dry=True)
    off_dry, sd_dry = STATION_OFFSET_HK["JJA_dry"]
    check("D", abs(ch - (27.6 + off_dry)) < 1e-9 and abs(csd - sd_dry) < 1e-9,
          f"27.6 grid + JJA-dry(+{off_dry:.2f}) -> {ch:.2f}degC sd {csd:.2f} "
          f"(composition = grid + table offset; refit-robust)")
    check("D", _season_key(6, True) == "JJA_dry" and _season_key(6, False) == "JJA_wet"
          and _season_key(1, True) == "DJF" and _season_key(4, True) == "MAM"
          and _season_key(10, True) == "SON",
          "season routing: Jun-dry->JJA_dry, Jun-wet->JJA_wet, Jan->DJF, Apr->MAM, Oct->SON")
    # recovery routing: a dry JJA day flagged recovery routes to the suppressed branch; a dry
    # NON-JJA day ignores the recovery flag (global recovery branch was falsified).
    check("D", _season_key(6, True, recovery=True) == "JJA_dry_recovery"
          and _season_key(4, True, recovery=True) == "MAM",
          "recovery routing: Jun-dry+recovery->JJA_dry_recovery, Apr-dry+recovery->MAM (ignored)")
    # falsifiable ordering #2 (both-halves VERIFIED, so safe to assert): within JJA the
    # recovery offset is SUPPRESSED below the established-dry offset (-0.62, h1 -0.83/h2 -0.42).
    rec_off = corrected_hk_high(27.6, 6, dry=True, recovery=True)[0]
    est_off = corrected_hk_high(27.6, 6, dry=True, recovery=False)[0]
    check("D", rec_off < est_off and (est_off - rec_off) > 0.3,
          f"JJA recovery suppressed: recovery {rec_off:.2f} < established-dry {est_off:.2f} "
          "(both-halves-verified, unlike the dry-vs-wet margin)")
    # falsifiable control: the corrector is NOT a constant. The ROBUST, large-margin order
    # is summer >> winter (JJA +2.60 vs DJF +0.99, n=460/510) — that survives both halves.
    # The dry-vs-wet margin inside summer (+0.38) is smaller than the offset sd (~1.2) and
    # was never both-halves-tested, and drift-adjusting dry to the recent regime nearly ties
    # it to wet — so we assert ONLY the season order, not the fragile within-summer margin.
    jun_dry = corrected_hk_high(27.6, 6, True)[0]
    jun_wet = corrected_hk_high(27.6, 6, False)[0]
    jan = corrected_hk_high(27.6, 1, True)[0]
    check("D", jun_dry > jan and jun_wet > jan and abs(jun_dry - jan) > 1.0,
          f"summer lift > winter: Jun-dry {jun_dry:.1f}, Jun-wet {jun_wet:.1f} > Jan {jan:.1f} "
          "(a constant offset would tie them)")

    # -- Box E: adaptive (drift-aware) offset (known answer + falsifiable controls) -------
    print("\n[E] adaptive offset: drift shrink toward trailing realized + no-drift control")
    # known answer: shrink trailing +1.55 toward static +2.18 with n=30, kappa=10
    #   (30*1.55 + 10*2.18)/40 = 1.7075
    ae = adaptive_offset(2.18, 1.55, 30, kappa=10)
    check("E", abs(ae - 1.7075) < 1e-6,
          f"shrink: trailing +1.55 (n=30) toward static +2.18 -> {ae:.4f} (== 1.7075)")
    # drift direction: when the recent record runs COOLER than the baked offset, adaptive
    # moves the central DOWN toward reality (this is the 2026 drift-correction).
    check("E", ae < 2.18 and ae > 1.55,
          "drift correction: adaptive sits between trailing and static, pulled toward recent")
    # falsifiable control #1: NO drift (trailing == static) MUST leave the offset unchanged —
    # the mechanism cannot manufacture a shift out of nothing.
    check("E", adaptive_offset(2.18, 2.18, 30) == 2.18,
          "no-drift control: trailing == static -> offset unchanged (no spurious shift)")
    # falsifiable control #2: sparse/absent trailing record falls back to static exactly.
    check("E", adaptive_offset(2.18, None, 0) == 2.18 and adaptive_offset(2.18, 1.0, 0) == 2.18,
          "fallback control: no trailing obs -> static (never trusts an empty record)")
    # monotonicity: more trailing obs -> closer to the trailing realized value.
    near = adaptive_offset(2.18, 1.55, 100); far = adaptive_offset(2.18, 1.55, 5)
    check("E", abs(near - 1.55) < abs(far - 1.55),
          f"monotone: n=100 ({near:.2f}) closer to trailing than n=5 ({far:.2f})")

    # -- Box F: London (EGLC) settlement offset (known answer + falsifiable controls) -----
    print("\n[F] London EGLC offset: small positive, season-routed, distinct from HK")
    # known answer: DJF baked pair, and a JJA correction adds +0.59 to the grid high
    check("F", london_settlement_offset(1) == (0.66, 0.77),
          f"DJF offset == (0.66, 0.77) (got {london_settlement_offset(1)})")
    clh = corrected_london_high(20.0, 7)[0]
    check("F", abs(clh - 20.59) < 1e-9,
          f"JJA: grid 20.0 + 0.59 -> {clh:.2f} (== 20.59)")
    # routing: the 12 months collapse to EXACTLY the 4 seasonal offsets (no KeyError, no
    # month silently sharing the wrong season).
    check("F", len({london_settlement_offset(m) for m in range(1, 13)}) == 4,
          "12 months route to exactly 4 distinct seasonal offsets")
    # falsifiable control: London is NOT HK. A copy-paste of the HK lift would FAIL this —
    # EGLC offset is small (<1degC) and >1degC below the HK JJA-dry station lift.
    lon_j = london_settlement_offset(7)[0]
    hk_j = hk_settlement_offset(7, True)[0]
    check("F", lon_j < 1.0 and (hk_j - lon_j) > 1.0,
          f"London ({lon_j:.2f}) small & distinct from HK JJA-dry ({hk_j:.2f}); not copy-pasted")
    # Tier-2 disagreement flag is monotone and triggers only on wide spread (falsifiable: a
    # calm 0.5 spread must NOT raise the HIGH flag).
    check("F", "HIGH" in disagreement_flag(1.6) and "HIGH" not in disagreement_flag(0.5),
          "Tier-2: HIGH flag fires on 1.6 spread, stays quiet on 0.5 (no false alarm)")

    print("\n" + "=" * 84)
    if fails:
        print(f"FAILED {len(fails)} check(s):")
        for f in fails:
            print("   -", f)
        return 1
    print("ALL BOXES GREEN — aggregation is correct, excludes missing models honestly,")
    print("                  and indexes the target day with no fabrication.")
    print("=" * 84)
    return 0


# --------------------------------------------------------------------- live report
def _live(target: dt.date):
    print("\n" + "=" * 92)
    print(f"LIVE — council-model NWP ensemble for {target.isoformat()} "
          "(7 operational models via SafeHTTPClient)")
    print("=" * 92)
    print("Sandboxed: HTTPS-only, host-allowlist (Open-Meteo), SSRF guard, redirect revalidation.")
    print("RECOMMEND-ONLY: forward grid-cell point; raw (pre station-offset). Never trades.\n")
    for key, (lat, lon, tz, label) in STATIONS.items():
        try:
            quotes = fetch_live_ensemble(lat, lon, tz, target)
        except Exception as exc:  # noqa: BLE001 - report, don't fabricate
            print(f"[{label}] LIVE FETCH FAILED: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        # remap model-id keys back to short labels for the print
        s = ensemble_summary(quotes)
        print(f"[{label}]  ({lat},{lon})  n={s['n']}/7 models")
        for _mid, model, mlabel in COUNCIL_MODELS:
            q = quotes.get(model)
            if q is None:
                print(f"    {mlabel:22} ---   (no grid value / error)")
            else:
                print(f"    {mlabel:22} high {q[0]:5.1f}degC   low {q[1]:5.1f}degC")
        print(f"    {'ENSEMBLE MEAN':22} high {s['high_mean']:5.1f}degC "
              f"(+-{s['high_spread']:.2f})   low {s['low_mean']:5.1f}degC "
              f"(+-{s['low_spread']:.2f})")
        full = lambda hs, osd: (hs ** 2 + osd ** 2) ** 0.5
        # HK settles on the HKO Absolute Daily Max, which the grid under-reads — apply the
        # backtested season/precip-conditioned corrector. Precip not fetched here, so show
        # both regimes; pick by the day's forecast rain (<5mm => dry).
        if key == "hong_kong" and s["n"] > 0:
            mo = target.month
            cd, sdd = corrected_hk_high(s["high_mean"], mo, dry=True)
            cw, sdw = corrected_hk_high(s["high_mean"], mo, dry=False)
            print(f"    {'HKO-ABSMAX (settle)':22} DRY high {cd:5.1f}degC "
                  f"(+-{full(s['high_spread'], sdd):.2f})   "
                  f"WET high {cw:5.1f}degC (+-{full(s['high_spread'], sdw):.2f})")
            print(f"    {'':22} (grid+offset; sigma = ensemble-spread (+) offset-sd in quad)")
        # London settles ON EGLC: the grid cell IS the station, so a small season-only offset.
        if key == "london" and s["n"] > 0:
            cl, sdl = corrected_london_high(s["high_mean"], target.month)
            print(f"    {'EGLC (settle)':22} high {cl:5.1f}degC "
                  f"(+-{full(s['high_spread'], sdl):.2f})   "
                  f"({cl * 9 / 5 + 32:.1f}degF)  grid+EGLC offset (season-only)")
        # Tier-2 (both cities): the live cross-model disagreement read.
        if s["n"] > 0:
            print(f"    {'TIER-2 disagree':22} spread {s['high_spread']:.2f} -> "
                  f"{disagreement_flag(s['high_spread'])}")
        print()


if __name__ == "__main__":
    rc = _selftest()
    if rc == 0:
        # default target: tomorrow in HK terms is fine; allow override via argv[1]=YYYY-MM-DD
        if len(sys.argv) > 1:
            tgt = dt.date.fromisoformat(sys.argv[1])
        else:
            tgt = dt.date(2026, 6, 10)
        _live(tgt)
    sys.exit(rc)
