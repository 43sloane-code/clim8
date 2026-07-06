"""The chair: reconcile five votes into one validated verdict.

Pipeline:
  1. Collect each member's Vote (real forecast + backtested skill).
  2. Reject outliers (a member whose corrected number is far from the panel
     median, or that lacks enough backtest history, is set aside).
  3. Blend the survivors' bias-corrected forecasts, weighting each by the
     inverse of its backtested error — accurate centers count more.
  4. Validate the *method itself* on held-out history: train weights on the
     older slice, blend on the newer slice, and compare the council's error
     to a naive equal-weight average. This is what makes the verdict earned
     rather than asserted.
  5. Calibrate confidence from both live agreement and that validation skill.
"""

from __future__ import annotations

__all__ = [
    'CouncilConfig', 'CONFIG', 'ForecastUnavailableError', 'Ensemble',
    'Interpretation', 'Diurnal', 'Records', 'Representativeness', 'Validation',
    'Verdict', 'applied_bias_correction', 'regime_consensus', 'Council'
]

import datetime as dt
import math
import statistics
from dataclasses import dataclass, field
from types import SimpleNamespace

from .agents import (ENSEMBLE_MODELS, MIN_SAMPLES, ForecasterAgent, Skill, Vote,
                     build_council)
from .seasonal import (SEASON_ANALOG_ARCHIVE_FLOOR, SEASON_ANALOG_WINDOW_DAYS,
                       seasonal_skill)
from .scoring import crps_sample, interval_coverage, pit
from .calibration import (conditional_spread_eval, CalibrationEval,
                          coverage_calibration_eval_grouped, CoverageEval)
from .bucket_verdict import bucket_verdict_eval, BucketVerdictEval
from .recency_bias import (recency_weighted_bias, evaluate as recency_bias_evaluate,
                           RecencyBiasEval, RECENCY_HALFLIFE_DAYS)
from .spread_skill import spread_skill_eval, SpreadSkill
from .ensemble_verification import (
    rank_histogram_eval, pit_calibration_eval, RankHistogram, PITCalibration)
from .convergence import ConvergenceInputs, Mechanism
from .observation import Observation, observe
from .security import RateLimitError
from .sources import (DailySeries, Place, Sources, Station, place_today,
                      quantize_to_grain)

# A predictive distribution needs a minimum residual sample before its CRPS or
# coverage means anything; below this we score that held-out day's point error
# only (CRPS still defined, but the *probabilistic* claim is too thin to trust).
# Mirrors compare.py's MIN_RESIDUALS floor so the two never disagree on when the
# empirical distribution is usable.
CRPS_MIN_SAMPLES = 10

# The newest observed day may stand in as a live "persistence" estimate for the
# convergence layer only when it is within this many days of the target. Beyond
# it the archive lag makes persistence a different mechanism than the one its
# backtested MAE scores, so that lineage abstains rather than mislead. (Lead-0
# verdicts on a fresh station feed satisfy this; lagged-archive cities do not.)
CONVERGENCE_PERSIST_MAX_GAP_DAYS = 2


@dataclass(frozen=True)
class CouncilConfig:
    """The tunable knobs the daily health check sweeps and re-checks on fresh data,
    gathered into one validated surface instead of scattered literals. These are the
    ONLY constants a recommend-only tuning pass may ever propose changing; every
    other constant in this module is structural. Frozen + validated at construction,
    so an out-of-range value fails loudly at import — never as a silent bad forecast.
    The module-level WEIGHT_POWER / OUTLIER_FLOOR_C / DISP_NORMAL / DISP_ELEVATED
    names below are kept as aliases onto this object, so existing imports (e.g.
    tools/daily_healthcheck.py) are unchanged and the values can never disagree.
    """
    weight_power: int = 2          # inverse-error exponent; 2 == inverse-variance
    outlier_floor_c: float = 4.0   # °C: never flag a member within this of the median
    disp_normal: float = 2.0       # °C: effective-σ at/below which spread reads normal
    disp_elevated: float = 3.5     # °C: effective-σ above which spread reads elevated

    def validate(self) -> None:
        """Raise (not assert — survives `python -O`) on any out-of-range knob."""
        if not (isinstance(self.weight_power, int) and self.weight_power > 0):
            raise ValueError("weight_power must be a positive int")
        if not self.outlier_floor_c > 0:
            raise ValueError("outlier_floor_c must be positive (°C)")
        if not self.disp_normal > 0:
            raise ValueError("disp_normal must be positive (°C)")
        if not self.disp_elevated > self.disp_normal:
            raise ValueError("disp_elevated must exceed disp_normal (°C)")

    def __post_init__(self) -> None:
        self.validate()


# The single validated source of truth for the tuning surface.
CONFIG = CouncilConfig()


class ForecastUnavailableError(RuntimeError):
    """No council member could produce a usable forecast for an attribute.
    A RuntimeError subclass so existing handlers still catch it, but typed so a
    caller can tell "the council genuinely has no data" apart from a transient
    upstream throttle (RateLimitError), which is retryable."""

ARCHIVE_LAG_DAYS = 2     # ERA5 reanalysis trails real time by ~1-2 days
OUTLIER_FLOOR_C = CONFIG.outlier_floor_c  # alias: never flag a member within this of the median
# Inverse-error weighting exponent: a member's weight is 1/MAE**WEIGHT_POWER, so
# WEIGHT_POWER=2 is inverse-variance weighting — the minimum-variance linear
# combination of independent estimators (statistically principled), not a tuned
# knob. Earned on a held-out, expanding-window walk-forward sweep across 8
# geographically diverse cities: 1/MAE^2 lowered mean held-out MAE versus 1/MAE
# in every city (~1.7% on the basket) with hit-rate(±2°C) unchanged. Applied
# identically in the live blend and the validation pass so they never diverge.
WEIGHT_POWER = CONFIG.weight_power        # alias onto the validated CouncilConfig
CLIMO_START_YEAR = 1991  # standard WMO baseline start for normals/records
RECORD_WINDOW_DAYS = 3   # ± calendar days pooled around the target date
# A station is only trusted as the truth source if it has reported within this
# many days. This keeps the learned bias from going stale and guarantees the
# backtest window overlaps the forecast archive (so each centre's past forecasts
# can actually be paired against the station); an older station is treated as
# defunct and we fall back to the always-fresh ERA5 grid.
MAX_TRUTH_STALE_DAYS = 400
# A backtest window only reflects the regime we're forecasting if it comes within
# ~a month of the target's time of year — the granularity at which standard (WMO)
# monthly climate normals are defined, so a gap larger than this means a different
# climatological normal. Meteostat's bulk daily archive currently lags ~2-3
# months, so for a warm-season target every station's window can sit in the
# previous (cool) season. When it does we KEEP the station anchoring (the
# settlement-vs-airport design depends on it) but flag the mismatch and downgrade
# confidence, because the hit-rate that anchors that confidence was measured
# out-of-season and should not be read at face value for this target.
SEASON_MATCH_DAYS = 31

# Cities whose verdict anchors on a SPECIFIC settlement station rather than the
# nearest reporting one — a user-pinned directive: the record the market actually
# settles on. Keyed by case-insensitive city-name substring -> the anchor's ICAO.
# London weather markets settle on London City Airport (EGLC), ~17 km east of the
# nearest station (the central London Weather Centre), so we prefer EGLC.
PINNED_ANCHOR_ICAO = {"london": "EGLC", "san francisco": "KSFO"}

# ICAO-pinned cities whose anchor is STRICT — the same rule as the HKO anchor:
# when the pinned airport's feed is stale/unavailable, the verdict must NOT
# silently fall through to a *different* nearby station. Measured directly: two
# resolves minutes apart flipped London between EGLC and "London / Abbey Wood" —
# two physical sensors that read differently, making the verdict jump between
# stations and read as model imprecision. So for these cities it is EGLC-or-honest
# -ERA5-grid, never a substitute station. (See _resolve_truth.)
STRICT_ANCHOR_ICAO = {"london", "san francisco"}

# Cities pinned to the Hong Kong Royal Observatory anchor. The Observatory has no
# ICAO (it is not an airport), so it is identified structurally by
# Sources.is_hko_observatory, not a station-id table. The market and the HKO both
# settle on the Observatory; the nearest airport (VHHH, ~6 km away) reads ~1 °C
# different. This pin is STRICT: if the Observatory's modern feed is transiently
# unavailable, the verdict must fall back to the reanalysis grid — never silently
# re-anchor on the airport, which would shift the verdict by a physical-station
# offset and read as model imprecision. (See _resolve_truth.)
PINNED_ANCHOR_HKO = {"hong kong"}

# Cities whose BACKTEST TRUTH is the Wunderground / Weather Company airport feed —
# the exact market settlement oracle. Two wins over the Meteostat bulk archive the
# nearest-station path would otherwise pick: it is CURRENT (no ~91-day lag, so the
# window stays in-season and the out-of-season confidence downgrade disappears),
# and it is settlement-CONSISTENT (members are scored against the very record the
# market resolves on, at the airport's own coordinates). Strict, like the EGLC/HKO
# pins: if WU is unavailable or too thin we fall through to the station/grid path,
# never silently anchoring on a different sensor. Matched by city-name containment.
_WU_TRUTH_STATIONS = {
    "manila": {"icao": "RPLL", "name": "Ninoy Aquino Intl",
               "lat": 14.5086, "lon": 121.0194},
    "singapore": {"icao": "WSSS", "name": "Changi",
                  "lat": 1.3502, "lon": 103.9944},
}


def _wu_truth_station(place) -> dict | None:
    """The Wunderground settlement-oracle station to anchor this city's backtest
    truth on (e.g. Manila -> RPLL), or None to use the nearest-station/grid path."""
    name = (getattr(place, "name", "") or "").strip().lower()
    for key, st in _WU_TRUTH_STATIONS.items():
        if key in name or name in key:
            return st
    return None


def _pinned_anchor_icao(place) -> str | None:
    name = (getattr(place, "name", "") or "").strip().lower()
    for key, icao in PINNED_ANCHOR_ICAO.items():
        if key in name or name in key:
            return icao
    return None


def _strict_anchor_icao(place) -> str | None:
    """The pinned settlement ICAO when that pin is STRICT (no other station may
    substitute for it — mirrors the HKO strict anchor). Upper-cased for the
    case-insensitive station-ICAO comparison in _resolve_truth, else None."""
    name = (getattr(place, "name", "") or "").strip().lower()
    for key, icao in PINNED_ANCHOR_ICAO.items():
        if key in STRICT_ANCHOR_ICAO and (key in name or name in key):
            return icao.upper()
    return None


def _wants_hko_anchor(place) -> bool:
    """True for cities pinned to the Hong Kong Observatory (strict anchor)."""
    name = (getattr(place, "name", "") or "").strip().lower()
    return any(key in name or name in key for key in PINNED_ANCHOR_HKO)


def _served_bias_halflife(place) -> float | None:
    """The recency half-life (days) the SERVED member-bias correction uses at this
    station, or None for the plain trailing-window mean.

    Per-station policy, each entry justified by the leak-free recency-bias gate
    (validation.recency_bias): a station turns recency ON only where that gate
    RECOMMENDED it past the noise floor — a measured decision, not a tuned knob,
    exactly like the measurement-justified PINNED_ANCHOR_HKO settlement pins.
      * Hong Kong Observatory — ON at 30 d. The gate cleared at +6.2σ (held-out
        CRPS +4.6%, whole-degree bucket-hit +2.3pp, point MAE -0.06 °C over 692
        paired days): HKO's high-side bias is genuinely non-stationary and the
        recency weighting tracks it.
      * London City Airport and every other station — OFF (None). The gate found
        no gain past the floor (~+0.9σ for London): the bias is effectively
        stationary on the window and the served residual cloud already absorbs the
        constant part, so plain-mean is correct.
    None for an unknown place, so the conservative plain-mean path is the default
    everywhere a station has not been explicitly, measurably opted in."""
    if place is not None and _wants_hko_anchor(place):
        return RECENCY_HALFLIFE_DAYS
    return None


def _doy_gap(dates: list[str], target: dt.date) -> int | None:
    """Smallest circular day-of-year distance between the target and any observed
    day in the backtest window — i.e. how far, seasonally, the truth we learned
    the bias/skill on sits from the day we're actually predicting. None if empty."""
    if not dates:
        return None
    t = target.timetuple().tm_yday
    best: int | None = None
    for ds in dates:
        try:
            doy = dt.date.fromisoformat(ds).timetuple().tm_yday
        except ValueError:
            continue
        d = abs(doy - t) % 365
        d = min(d, 365 - d)
        best = d if best is None or d < best else best
    return best


def _circ_dist(a: float, b: float) -> float:
    """Shortest distance between two hours on a 24 h clock (0..12)."""
    d = abs(a - b) % 24
    return min(d, 24 - d)


def _circ_stat(hours: list[int]) -> tuple[float | None, float | None]:
    """Circular mean and spread of clock hours, so a peak/trough that sits near
    midnight isn't split into 23:00 and 01:00 and given a bogus wide σ."""
    if not hours:
        return None, None
    n = len(hours)
    ang = [h / 24.0 * 2 * math.pi for h in hours]
    c = sum(math.cos(a) for a in ang) / n
    s = sum(math.sin(a) for a in ang) / n
    mean_h = (math.atan2(s, c) / (2 * math.pi) * 24) % 24
    r = math.hypot(c, s)                       # resultant length, 0..1
    if r >= 1.0 or n == 1:
        return mean_h, 0.0
    sd_h = math.sqrt(-2 * math.log(r)) / (2 * math.pi) * 24  # circular σ in hours
    return mean_h, sd_h


def _pct(values: list[float], q: float) -> float:
    s = sorted(values)
    if not s:
        return float("nan")
    i = min(len(s) - 1, max(0, round(q * (len(s) - 1))))
    return s[i]


@dataclass
class Ensemble:
    member_count: int
    models: dict[str, int]                # model label -> members contributed
    mean_high: float | None
    mean_low: float | None
    spread_high: float | None             # 1 sigma across members
    spread_low: float | None
    p10_high: float | None
    p90_high: float | None
    p10_low: float | None
    p90_low: float | None
    agreement_high: float | None = None   # frac of members within +/-2C of mean
    agreement_low: float | None = None
    # Can the ensemble mean be backtested here, and may it enter the blend?
    backtest_days: int = 0                 # paired (forecast, ERA5) days available
    blend_eligible: bool = False           # backtest_days >= MIN ⇒ earns a vote
    corrected_mean_high: float | None = None  # bias-corrected mean, if eligible
    corrected_mean_low: float | None = None   # bias-corrected mean low, if eligible


@dataclass
class Interpretation:
    members_used: int
    outliers_set_aside: int
    mean_bias_removed_high: float | None  # avg |bias| correction applied (high)
    mean_bias_removed_low: float | None
    history_days: int                     # backtest pattern-recognition window


@dataclass
class Diurnal:
    """When, within the day, the temperature peaks and bottoms out."""
    peak_temp: float | None        # validated daily maximum (the council high)
    peak_time: str | None          # local clock time of the forecast peak
    trough_temp: float | None      # validated daily minimum (the council low)
    trough_time: str | None
    curve: list[tuple[str, float]]            # hourly consensus (hh:mm, temp)
    obs_peak_hour: float | None    # historically observed mean peak hour
    obs_peak_sd: float | None
    obs_trough_hour: float | None
    obs_trough_sd: float | None
    peak_in_band: bool | None      # forecast peak hour within observed mean±σ?
    trough_in_band: bool | None
    history_days: int


@dataclass
class Records:
    """How the target date compares to the long-term observed archive."""
    since_year: int
    window_days: int
    sample_days: int
    record_high: float | None
    record_high_year: int | None
    record_low: float | None
    record_low_year: int | None
    normal_high: float | None        # mean observed high for this date window
    normal_low: float | None
    peak_percentile: float | None    # forecast peak's rank vs historical highs


@dataclass
class Representativeness:
    """How well the grid-cell figure stands in for a point station.

    The verdict's numbers are the temperature of the ERA5/Open-Meteo grid cell
    at the geocoded coordinates — that is also the "truth" the method is
    backtested against. An official observatory inside that cell can read
    warmer or cooler (urban heat island, coast, terrain). We can't fetch the
    station directly, but we *can* measure how fast the temperature field
    changes across neighbouring grid cells; that across-cell spread bounds how
    far a point inside the cell may diverge, and is folded into confidence."""
    offset_deg: float
    neighbor_points: int
    sample_days: int
    spatial_sigma_high: float | None   # mean across-cell σ of daily high (°C)
    spatial_sigma_low: float | None
    sigma: float | None                # the worse of the two, used downstream


@dataclass
class Validation:
    council_mae_high: float | None
    council_mae_low: float | None
    naive_mae_high: float | None
    naive_mae_low: float | None
    hit_rate_2c: float | None   # fraction of held-out days within +/-2 C
    test_days: int
    # Standard reference forecasts the council must beat to justify itself.
    persistence_mae_high: float | None = None  # "tomorrow = yesterday's value"
    persistence_mae_low: float | None = None
    climatology_mae_high: float | None = None  # "tomorrow = the seasonal normal"
    climatology_mae_low: float | None = None
    # RMSE alongside MAE (forecast-verification: report both — RMSE ≫ MAE is the
    # fingerprint of occasional big busts, i.e. a fat-/regime-dependent tail. That
    # tail is exactly why a single constant band-widening can lift coverage yet hurt
    # CRPS, so the coverage check declines it). Same held-out days as the MAE.
    council_rmse_high: float | None = None
    council_rmse_low: float | None = None
    council_win_rate: float | None = None      # frac of held-out preds beating naive
    # Signed held-out errors (observed − council prediction), in the backtest
    # truth unit (°C). The *empirical* error distribution of the method here —
    # the only earned basis for turning a point verdict into bucket probabilities.
    residuals_high: list[float] = field(default_factory=list)
    residuals_low: list[float] = field(default_factory=list)
    # Per-day leak-free walk-forward stream: ordered (iso_date, served_point,
    # realized) triples, one per held-out day, per attribute (high/low are
    # separate markets). This is the residual cloud WITH the point's fractional
    # offset preserved — exactly what an external bucket-calibration backtest
    # (monte-carlo/backtest_mc.py walkforward) needs, since round_half_up(point+r)
    # depends on the point's fraction. Measure-only; never feeds the live verdict.
    wf_high: list[tuple[str, float, float]] = field(default_factory=list)
    wf_low: list[tuple[str, float, float]] = field(default_factory=list)
    # Per-day leak-free CRPS stream: ordered (iso_date, attr, crps_council_day,
    # crps_climatology_day) tuples, one per held-out day per attribute. The mean
    # of crps_council_day over all entries reproduces crps_council exactly; the
    # per-day grain lets an A/B split the held-out window into DISJOINT folds and
    # test sign-stability of a config change below the run-to-run noise floor
    # (ledger candidate 47). Measure-only; never feeds the live verdict.
    wf_crps: list[tuple[str, str, float, float]] = field(default_factory=list)
    # Probabilistic skill, scored on the SAME held-out days with a strictly
    # proper rule (CRPS, °C) so the predictive distribution the council sells as
    # bucket probabilities is itself verified — not just its point error. Each
    # held-out day is dressed only with residuals from STRICTLY EARLIER held-out
    # days (no leakage), exactly the empirical distribution compare.py resamples.
    crps_council: float | None = None       # mean CRPS of the council predictive
    crps_climatology: float | None = None   # same, for a dressed-climatology ref
    crps_skill: float | None = None         # 1 − council/climatology (>0 = better)
    coverage_80: float | None = None        # empirical hit-rate of the 80% interval
    sharpness_80: float | None = None       # mean width of that interval (°C)
    crps_n: int = 0                          # held-out days scored probabilistically
    # Recommend-only ML check: does scaling the predictive spread by per-day member
    # dispersion (a conditional/heteroscedastic distribution) beat the single
    # residual cloud on held-out CRPS, past the noise floor? None when too few days
    # to judge. This NEVER changes the verdict — it surfaces a finding for review.
    calibration: CalibrationEval | None = None
    # Recommend-only spread–skill diagnostic over the SAME leak-free walk-forward
    # (signed_residual, member_dispersion) pairs: after removing the global
    # averaging scale, does member dispersion track the blend's error with the
    # right shape across regimes (a reliable per-day uncertainty signal), or is it
    # flat? This is the verification that makes the bucket probabilities' SPREAD
    # trustworthy. None when too few days. Never moves the verdict.
    spread_skill: SpreadSkill | None = None
    # Recommend-only ensemble-calibration companions to spread_skill, over the
    # SAME leak-free walk-forward. rank_histogram (Talagrand) asks whether the raw
    # member panel's dispersion is the right SIZE (U = under-dispersed, the classic
    # deterministic-panel failure that justifies serving the wider residual cloud).
    # pit_calibration asks whether the SERVED distribution — the residual cloud
    # compare.py resamples into bucket probabilities — is itself calibrated
    # (uniform PIT). None when too few days. Neither moves the verdict.
    rank_histogram: RankHistogram | None = None
    pit_calibration: PITCalibration | None = None
    # Recommend-only coverage calibration over the SAME leak-free walk-forward
    # residual stream: is the ONE served cloud the right WIDTH on average? Learns a
    # single online-conformal inflation factor from the council's realized
    # out-of-sample coverage and asks whether widening the cloud by it beats the
    # incumbent on CRPS past the noise floor while dragging coverage toward nominal.
    # Complements pit_calibration (shape) with a scale check. None when too few
    # days. RECOMMEND-ONLY — never widens the cloud the council actually serves.
    coverage_calibration: CoverageEval | None = None
    # Bucket-verdict simulation: the council scored on the object the MARKET pays
    # on — the whole-degree settlement bucket — over the SAME leak-free
    # walk-forward. The modal bucket (point verdict dressed with the prior
    # residual cloud, rounded to the settlement integer) is compared to the
    # realized bucket; reports hit-rate, directional off-by-one bias, and
    # edge-distance fragility (misses near a boundary vs gross errors). Scored per
    # attribute because high and low settle as separate markets. None when too few
    # days. MEASURE-ONLY — never moves the served verdict. See bucket_verdict.py.
    bucket_verdict_high: BucketVerdictEval | None = None
    bucket_verdict_low: BucketVerdictEval | None = None
    # Recency-weighted-bias candidate evaluation: does recency-weighting each
    # member's bias (vs the served plain training mean) sharpen the held-out
    # distribution? Scored leak-free on paired CRPS (SE-gated) and bucket-hit, per
    # attribute then pooled. None when too few paired days. RECOMMEND-ONLY — the
    # served blend always uses the plain-mean bias. See recency_bias.py.
    recency_bias: RecencyBiasEval | None = None
    # The SAME recency-bias audit, split PER ATTRIBUTE (high and low are separate
    # markets). The pooled `recency_bias` above can recommend off a gain that lives
    # entirely in one attribute; these localize it. A per-station served policy
    # that turns recency on for the pool is only justified on an attribute whose
    # own audit clears the gate — so these are the evidence that keeps the served
    # _served_bias_halflife honest at the attribute grain. Recommend-only.
    recency_bias_high: RecencyBiasEval | None = None
    recency_bias_low: RecencyBiasEval | None = None
    # The recency half-life (days) this station's SERVED bias correction uses, or
    # None for the plain trailing mean. When set, the headline MAE/CRPS/bucket
    # above already reflect the recency-weighted bias (it is APPLIED, not just
    # recommended). Set by _served_bias_halflife — see recency_bias gate.
    bias_halflife_served: float | None = None


@dataclass
class Verdict:
    place: Place
    target: str
    high: float
    low: float
    high_spread: float
    low_spread: float
    confidence: str
    confidence_detail: dict
    votes: list[Vote]
    included_high: list[str]
    included_low: list[str]
    weights_high: dict[str, float]
    weights_low: dict[str, float]
    validation: Validation
    observation: Observation
    ensemble: Ensemble
    interpretation: Interpretation
    diurnal: Diurnal
    records: Records
    representativeness: Representativeness
    truth_source: dict         # which observations the verdict is anchored/backtested on
    target_basis: str          # exactly what point/quantity the numbers describe
    target_status: str         # "forecast" (day unfinished) vs "recorded" (ERA5)
    qc: dict
    requests_made: int
    naive_high: float | None = None
    naive_low: float | None = None
    settlement: dict | None = None  # native-grain quantization + Meteostat-vs-METAR check
    convergence: dict | None = None  # recommend-only mechanism-convergence inputs ({"high","low"})


def _mae(v: Validation, mechanism: str, attr: str) -> float | None:
    """The held-out MAE the walk-forward backtest produced for one mechanism and
    quantity ('high'/'low'). Used by the convergence layer to score each
    mechanism on its own proper score — never a fabricated number."""
    return getattr(v, f"{mechanism}_mae_{attr}", None)


def applied_bias_correction(v: Verdict, attr: str = "high") -> float | None:
    """The net signed °C bias correction the council baked into the headline
    number: the weighted blend of each member's bias-corrected value minus the
    weighted blend of their RAW values, over the members actually included and
    their final blend weights. Algebraically Σ wᵢ·(correctedᵢ − rawᵢ) =
    −Σ wᵢ·biasᵢ.

    Positive ⇒ the verdict was pushed up (the panel ran cold here); negative ⇒
    pushed down (the panel ran warm). This isolates the *learned, backtested*
    shift from the raw multi-model consensus, so a divergence from a market can
    be attributed to earned signal rather than hedged. None when no included
    member carried both a live raw value and a learned bias.

    The bias each member removed is itself fit on that member's paired
    forecast/observation history and held out in the walk-forward validation
    (Validation.residuals_*), so this figure is earned, not asserted."""
    included = v.included_high if attr == "high" else v.included_low
    weights = v.weights_high if attr == "high" else v.weights_low
    by_id = {vote.spec.member_id: vote for vote in v.votes}
    total = 0.0
    seen = False
    for m in included:
        vote = by_id.get(m)
        if vote is None:
            continue
        raw = vote.raw_high if attr == "high" else vote.raw_low
        cor = vote.corrected_high if attr == "high" else vote.corrected_low
        w = weights.get(m)
        if raw is None or cor is None or w is None:
            continue
        total += w * (cor - raw)
        seen = True
    return total if seen else None


# Consensus bands (interpretation layer only — never moves the number). Each
# independent point estimator's distance from the headline verdict is measured
# in units of the *effective* σ the engine already computed: agreement within
# 1σ is a matched verdict, beyond 1.5σ is a genuine split worth flagging.
CONSENSUS_MATCH_SIGMA = 1.0
CONSENSUS_SPLIT_SIGMA = 1.5
# σ fallback (°C) when effective uncertainty is unavailable, so consensus can
# still be judged on an absolute scale rather than silently going unscored.
CONSENSUS_SIGMA_FLOOR = 1.0


def _consensus_read(v: Verdict, sigma: float) -> dict:
    """Job 3 of regime_consensus, isolated: measure whether the independent point
    estimators (naive equal-weight average, raw perturbed-ensemble mean) agree with
    the headline verdict within the stated effective σ, on the worse of high/low.
    Pure — reads only finished Verdict fields and never moves the number."""
    en = v.ensemble
    estimators = {
        "high": {"verdict": v.high, "naive": v.naive_high, "ensemble_mean": en.mean_high},
        "low": {"verdict": v.low, "naive": v.naive_low, "ensemble_mean": en.mean_low},
    }
    worst_ratio, worst_axis = 0.0, "high"
    for axis, headline in (("high", v.high), ("low", v.low)):
        for key in ("naive", "ensemble_mean"):
            est = estimators[axis][key]
            if est is None:
                continue
            ratio = abs(est - headline) / sigma
            if ratio > worst_ratio:
                worst_ratio, worst_axis = ratio, axis
    if worst_ratio <= CONSENSUS_MATCH_SIGMA:
        status = "matched"
    elif worst_ratio <= CONSENSUS_SPLIT_SIGMA:
        status = "loose"
    else:
        status = "split"
    return {"estimators": estimators, "worst_ratio": worst_ratio,
            "worst_axis": worst_axis, "status": status}


def _classify_regime(v: Verdict) -> dict:
    """Job 1 of regime_consensus, isolated: name the regime from already-computed,
    already-backtested signals on the Verdict — seasonality, ensemble data depth,
    today's volatility (effective-σ tier) and the spatial gradient. Pure summary."""
    cd = v.confidence_detail or {}
    en = v.ensemble
    eff = cd.get("effective_uncertainty")
    gap = cd.get("season_gap_days")
    out_of_season = bool(gap is not None and gap > SEASON_MATCH_DAYS)
    season = "out-of-season" if out_of_season else "in-season"
    thin = not en.blend_eligible
    data = "thin" if thin else "rich"
    if eff is None or eff <= DISP_NORMAL:
        volatility = "calm"
    elif eff <= DISP_ELEVATED:
        volatility = "elevated"
    else:
        volatility = "high"
    sp = cd.get("representativeness_sigma")
    if sp is None or sp < 0.5:
        spatial = "flat"
    elif sp < 1.0:
        spatial = "moderate"
    else:
        spatial = "steep"
    label = f"{season} · {data} ensemble · {volatility} · {spatial} field"
    return {"label": label, "season": season, "gap": gap,
            "out_of_season": out_of_season, "data": data, "thin": thin,
            "volatility": volatility, "eff": eff, "spatial": spatial, "sp": sp}


def _regime_trusted_notes(r: dict, en: Ensemble) -> list[str]:
    """Job 2 of regime_consensus, isolated: state which validation is load-bearing
    in this regime (and which to distrust), making the confidence-tier reasoning
    explicit instead of scattered across caveats."""
    gap, eff, sp = r["gap"], r["eff"], r["sp"]
    trusted: list[str] = []
    if r["out_of_season"]:
        trusted.append(
            f"Trailing-window hit-rate is from a climate regime ~{gap}d off the "
            f"target day-of-year — lean on the seasonal-analog backtest, not the raw hit-rate.")
    if r["thin"]:
        trusted.append(
            f"Perturbed ensemble has only {en.backtest_days} backtestable day(s) "
            f"(<{MIN_SAMPLES}); it bounds confidence but cannot move the number.")
    if r["volatility"] != "calm" and eff is not None:
        trusted.append(
            f"Effective σ {eff:.1f} °C is {r['volatility']} — widen the bucket "
            f"probabilities; today is harder than the backtest baseline.")
    if r["spatial"] == "steep" and sp is not None:
        trusted.append(
            f"Across-cell σ {sp:.1f} °C is steep — a point station may diverge "
            f"from the grid-cell verdict.")
    if not trusted:
        trusted.append(
            "All regime axes benign — the backtested hit-rate applies at face value.")
    return trusted


def _consensus_takeaway(status: str, worst_ratio: float, worst_axis: str,
                        label: str) -> str:
    """One-line plain-English read of the consensus status, in regime context."""
    if status == "matched":
        return (f"Matched verdict: deterministic blend, naive average and ensemble "
                f"mean agree within {worst_ratio:.2f}σ. Regime: {label}.")
    if status == "loose":
        return (f"Loose agreement: estimators sit within {worst_ratio:.2f}σ on the "
                f"{worst_axis} — within tolerance but not tight. Regime: {label}.")
    return (f"Split verdict: an estimator sits {worst_ratio:.2f}σ from the "
            f"headline {worst_axis} — read the bucket probabilities wider. Regime: {label}.")


def regime_consensus(v: Verdict) -> dict:
    """Consolidated regime read + cross-mechanism consensus for a finished
    Verdict. Pure post-hoc summary, exactly like applied_bias_correction: it
    reads only what the engine already computed and backtested and NEVER changes
    the headline number.

    Three jobs, one block:
      1. Name the *regime* from signals already on the Verdict — seasonality
         (in/out-of-season), data depth (rich/thin ensemble backtest), today's
         volatility (effective-σ tier) and the spatial gradient.
      2. State which validation is load-bearing in that regime (and which to
         distrust) — making the confidence-tier logic explicit instead of
         scattered across caveats.
      3. Measure whether the independent point estimators — the deterministic
         backtested blend (the verdict), the naive equal-weight average and the
         raw perturbed-ensemble mean — agree within the stated effective σ
         ("matched verdict") or split, on the worse of high/low.
    """
    cd = v.confidence_detail or {}
    eff = cd.get("effective_uncertainty")
    sigma = eff if (eff is not None and eff > 0) else CONSENSUS_SIGMA_FLOOR
    scaled = eff is not None and eff > 0

    con = _consensus_read(v, sigma)              # job 3: estimators vs headline, in σ
    reg = _classify_regime(v)                    # job 1: name the regime
    trusted = _regime_trusted_notes(reg, v.ensemble)   # job 2: what to trust here
    takeaway = _consensus_takeaway(
        con["status"], con["worst_ratio"], con["worst_axis"], reg["label"])

    return {
        "regime": {
            "label": reg["label"],
            "season": reg["season"],
            "season_gap_days": reg["gap"],
            "data": reg["data"],
            "ensemble_backtest_days": v.ensemble.backtest_days,
            "test_days": v.validation.test_days,
            "volatility": reg["volatility"],
            "effective_sigma": reg["eff"],
            "spatial": reg["spatial"],
            "spatial_sigma": reg["sp"],
        },
        "consensus": {
            "status": con["status"],
            "worst_ratio": round(con["worst_ratio"], 2),
            "worst_axis": con["worst_axis"],
            "scaled_by_effective_sigma": scaled,
            "sigma_used": round(sigma, 2),
            "estimators": con["estimators"],
        },
        "trusted_validation": trusted,
        "takeaway": takeaway,
    }


_SEVERITY = {"high": 3, "medium": 2, "low": 1}
_TIER = {3: "high", 2: "medium", 1: "low"}

# Effective-uncertainty thresholds (°C). At a 1-day lead, operational 2 m-temperature
# ensemble 1σ and inter-model weighted σ are typically 1–2 °C. We treat the combined
# uncertainty up to NORMAL as routine (no penalty), up to ELEVATED as a regime harder
# than the backtest baseline (one tier down), and beyond ELEVATED as genuinely
# uncertain (forced to low regardless of past skill). The combined figure adds two
# *independent* axes in quadrature: forecast disagreement (does the atmosphere's
# evolution agree across models/members?) and spatial representativeness (does the
# grid cell we backtest against actually stand in for the point a reader cares
# about?). A city can have models in tight agreement yet a steep local gradient —
# e.g. a coastal observatory — and should not be sold as high-confidence about a
# point the data never resolved.
DISP_NORMAL = CONFIG.disp_normal          # alias onto the validated CouncilConfig
DISP_ELEVATED = CONFIG.disp_elevated      # alias onto the validated CouncilConfig


def _weighted_std(pairs: list[tuple[float, float]]) -> float | None:
    """Population σ of values weighted by `pairs` of (value, weight).
    Reflects the dispersion of what actually enters the blend — unlike range,
    it is not set by a single extreme member and does not grow with count."""
    if not pairs:
        return None
    wsum = sum(w for _, w in pairs) or 1.0
    mean = sum(v * w for v, w in pairs) / wsum
    var = sum(w * (v - mean) ** 2 for v, w in pairs) / wsum
    return var ** 0.5


def _backtest_tier(v: Validation) -> str:
    """Earned base tier: the only signal replayed against real outcomes.
    None means we never tested this method on held-out days here — we cannot
    claim confidence we have not measured, so it floors at low."""
    if v.hit_rate_2c is None:
        return "low"
    if v.hit_rate_2c >= 0.85:
        return "high"
    if v.hit_rate_2c >= 0.65:
        return "medium"
    return "low"


def _calibrate_confidence(
    validation: Validation, det_std: float | None, ens_sigma: float | None,
    cross_system: float | None, repr_sigma: float | None,
    season_gap_days: int | None = None,
) -> tuple[str, dict]:
    """Anchor on the backtested hit-rate, then let today's *effective*
    uncertainty only *lower* it — never invent confidence the backtest did not
    earn. Effective uncertainty combines three independent axes in quadrature:

      * within-system spread — how far members/perturbations scatter (max of the
        deterministic weighted σ and the ensemble σ, two views of one axis);
      * cross-system disagreement — how far the bias-corrected deterministic
        control blend and the perturbed-ensemble mean sit apart on the headline
        number itself. Tight internal spread tells us a single system is sure of
        itself; it says nothing about whether the perturbed runs land elsewhere.
        A 1–2 °C gap is real epistemic uncertainty that within-spread cannot see.
        These two views share lineage (the ensemble's families are EPS versions
        of three panel members), so this term is used one-directionally — a wide
        gap downgrades, a narrow one is never booked as independent corroboration;
      * spatial representativeness — how far a point station may sit from the
        grid cell we actually backtest against.

    These are largely independent sources, so they add in quadrature rather than
    one vetoing the rest."""
    base = _backtest_tier(validation)
    within = [d for d in (det_std, ens_sigma) if d is not None]
    within_sigma = max(within) if within else None

    components = [c for c in (within_sigma, cross_system, repr_sigma)
                 if c is not None]
    effective = math.sqrt(sum(c * c for c in components)) if components else None

    if effective is None or effective <= DISP_NORMAL:
        penalty = 0
    elif effective <= DISP_ELEVATED:
        penalty = 1
    else:
        penalty = 2

    # Out-of-season backtest: the earned hit-rate was measured on a window whose
    # time of year is >a month from the target, so it is evidence about a
    # different climatological regime. Don't trust it at face value — knock one
    # tier off. This is independent of today's dispersion (it's about *when* the
    # truth is from, not how the models scatter), so it adds to the penalty.
    seasonal_penalty = (1 if season_gap_days is not None
                        and season_gap_days > SEASON_MATCH_DAYS else 0)

    level = max(1, _SEVERITY[base] - penalty - seasonal_penalty)
    final = _TIER[level]
    detail = {
        "final": final,
        "backtest_tier": base,
        "hit_rate_within_2c": validation.hit_rate_2c,
        "deterministic_weighted_sigma": det_std,
        "ensemble_sigma": ens_sigma,
        "within_system_sigma": within_sigma,
        "cross_system_disagreement": cross_system,
        "representativeness_sigma": repr_sigma,
        "effective_uncertainty": effective,
        "tiers_downgraded": penalty,
        "season_gap_days": season_gap_days,
        "seasonal_downgrade": seasonal_penalty,
    }
    return final, detail


class Council:
    def __init__(self, sources: Sources | None = None) -> None:
        self.sources = sources or Sources()
        self.members: list[ForecasterAgent] = build_council(self.sources)

    # -- public ----------------------------------------------------------- #
    def deliberate(self, place: Place, target: dt.date, window: int) -> Verdict:
        # Anchor the verdict on a real point observation (an airport/observatory
        # the records actually settle on) when one is near and current; otherwise
        # fall back to the always-fresh ERA5 grid at the city centroid. `fp` is
        # the point every forecast and backtest below is queried at.
        fp, observed, w_start, w_end, truth_source = self._resolve_truth(
            place, target, window)

        # Stage 1 — Observation.
        observation = self._observe(fp, observed, truth_source)

        # Stage 2 — Computation: deterministic panel + perturbed ensemble.
        votes = [m.analyze(fp, target, w_start, w_end, observed)
                 for m in self.members]
        # If the backtest window is out of season (Meteostat lag), re-learn each
        # member's bias from same-day-of-year analog days in prior years — a
        # winter-trained correction mis-corrects a summer target. In-season
        # verdicts are untouched (the method returns early).
        self._apply_seasonal_analog(votes, fp, target, w_start, truth_source)
        # In season, at stations the leak-free recency-bias gate recommended
        # (today: Hong Kong), upgrade each member's trailing-window bias to an
        # exponentially recency-weighted one so the correction tracks a drifting
        # regime. SERVED — moves the verdict; a no-op everywhere else and out of
        # season. _validate(fp=...) measures this same served method.
        self._apply_recency_bias(votes, fp, target, truth_source)
        ensemble = self._ensemble(fp, target, observed, w_start, w_end)

        # Stage 3 — Interpretation: bias correction, weighting, outlier screen.
        high, inc_h, spread_h, wts_h = self._blend(votes, "high")
        low, inc_l, spread_l, wts_l = self._blend(votes, "low")
        naive_h = self._naive(votes, "high")
        naive_l = self._naive(votes, "low")
        validation = self._validate(votes, observed, fp)
        interpretation = self._interpret(votes, inc_h, window)
        diurnal = self._diurnal(fp, target, w_start, w_end,
                                round(high, 1), round(low, 1), window)
        records = self._records(fp, target, round(high, 1))
        representativeness = self._representativeness(fp, w_start, w_end)

        det_std = self._panel_sigma(votes, inc_h, wts_h, inc_l, wts_l)
        ens_sigma = self._ensemble_sigma(ensemble)
        cross_system = self._cross_system(high, low, ensemble)
        confidence, conf_detail = _calibrate_confidence(
            validation, det_std, ens_sigma, cross_system, representativeness.sigma,
            season_gap_days=truth_source.get("season_gap_days"))

        status = "forecast" if target >= place_today(place) else "recorded"
        basis = self._basis(fp, truth_source)
        settlement = self._settlement(fp, truth_source, observed,
                                      high, low, w_start, w_end)
        convergence = self._convergence(
            observed, high, low, naive_h, naive_l, records, validation, target)

        return Verdict(
            place=fp, target=target.isoformat(),
            high=round(high, 1), low=round(low, 1),
            high_spread=round(spread_h, 1), low_spread=round(spread_l, 1),
            confidence=confidence, confidence_detail=conf_detail, votes=votes,
            included_high=inc_h, included_low=inc_l,
            weights_high=wts_h, weights_low=wts_l,
            validation=validation,
            observation=observation, ensemble=ensemble,
            interpretation=interpretation, diurnal=diurnal, records=records,
            representativeness=representativeness,
            truth_source=truth_source,
            target_basis=basis, target_status=status,
            qc=dict(self.sources.qc),
            requests_made=self.sources.http.requests_made,
            naive_high=round(naive_h, 1) if naive_h is not None else None,
            naive_low=round(naive_l, 1) if naive_l is not None else None,
            settlement=settlement,
            convergence=convergence,
        )

    # -- truth resolution: station observations, else the ERA5 grid ---------- #
    def _station_provenance(self, st: Station) -> tuple[str, str]:
        """Honest provenance for an anchor station: (data_source, human feed label).
        The Hong Kong Observatory anchor is served from the HKO open-data API (its
        Meteostat file ends 1992); EGLC's extremes come from the IEM ASOS METAR
        archive overlaid on Meteostat; everything else is plain Meteostat."""
        if self.sources.is_hko_observatory(st):
            return "hko_opendata", "Hong Kong Observatory open-data daily observations"
        if self.sources.is_london_eglc(st):
            return "iem_metar", ("IEM ASOS METAR daily extremes at the EGLC sensor "
                                 "(overlaid on Meteostat for older days)")
        return "meteostat", "Meteostat daily observations"

    def _resolve_truth(self, place: Place, target: dt.date, window: int):
        """Pick the observations the verdict is anchored on and backtested
        against. Prefer the nearest, still-reporting surface station (the point a
        record/market settles on); forecasts are then queried at that station's
        own coordinates and each centre's skill is scored against the station's
        own readings. If no current station is near enough, fall back to the
        ERA5 reanalysis grid at the city centroid. Returns
        (forecast_place, observed, w_start, w_end, truth_source).

        `target` is used only to annotate each truth source with its seasonal
        gap (how far the window sits, in day-of-year, from the day we predict);
        this never changes which source is chosen — it drives the out-of-season
        confidence downgrade so a station-anchored verdict stays anchored."""
        default_end = place_today(place) - dt.timedelta(days=ARCHIVE_LAG_DAYS)

        # Wunderground settlement-oracle truth (e.g. Manila -> RPLL): the market
        # settles on this feed and, unlike the Meteostat bulk archive the station
        # loop below would pick, it is CURRENT — so the window stays IN-SEASON and
        # members are scored against the exact record the contract resolves on, at
        # the airport's own coordinates. STRICT: if WU is unavailable or too thin we
        # fall through to the station/grid path (never silently re-anchor).
        wu = _wu_truth_station(place)
        if wu:
            wu_end = place_today(place) - dt.timedelta(days=1)   # WU is current; complete days only
            wu_start = wu_end - dt.timedelta(days=window + 5)    # buffer for dropped partial days
            try:
                series = self.sources.wunderground_daily_series(
                    wu["icao"], wu_start, wu_end, place.timezone)
            except Exception:
                series = {}
            obs = dict(sorted(series.items())[-(window + 1):])   # most-recent `window` days
            if len(obs) >= MIN_SAMPLES:
                w_start = dt.date.fromisoformat(min(obs))
                w_end = dt.date.fromisoformat(max(obs))
                fp = Place(place.name, place.country, wu["lat"], wu["lon"],
                           place.timezone)
                truth_source = {
                    "kind": "station",
                    "data_source": "Wunderground / Weather Company (settlement oracle)",
                    "station": {
                        "id": wu["icao"], "name": wu["name"], "icao": wu["icao"],
                        "wmo": None, "latitude": wu["lat"], "longitude": wu["lon"],
                        "elevation": None, "distance_km": 0.0,
                    },
                    "label": (f"{wu['name']} ({wu['icao']}) — Wunderground "
                              f"settlement record (the market's own oracle, current)"),
                    "window_start": w_start.isoformat(),
                    "window_end": w_end.isoformat(),
                    "sample_days": len(obs),
                    "lag_days": (wu_end - w_end).days,
                    "season_gap_days": _doy_gap(list(obs.keys()), target),
                }
                return fp, obs, w_start, w_end, truth_source
            # else: fall through to the nearest-station / ERA5-grid truth below.

        try:
            candidates = self.sources.nearest_stations(place)
        except Exception:
            candidates = []

        # User-pinned anchor: for cities that settle on a specific station (e.g.
        # London -> London City Airport, EGLC), try that station first regardless
        # of distance. Stable sort keeps the others in distance order.
        pinned = _pinned_anchor_icao(place)
        if pinned:
            candidates = sorted(
                candidates, key=lambda s: (s.icao or "").upper() != pinned)
        # STRICT ICAO anchor (e.g. London -> EGLC): like the HKO anchor below, no
        # other physical station may substitute. If EGLC's feed is stale/thin we
        # must NOT fall through to a different station (which reads differently and
        # makes the verdict jump between sensors); we let the loop exhaust and drop
        # to the honest ERA5 grid instead. Enforced in the candidate loop.
        strict_icao = _strict_anchor_icao(place)

        # Hong Kong is pinned to the Royal Observatory (no ICAO; matched
        # structurally). This anchor is STRICT: the Observatory is tried first,
        # and NO other physical station may substitute for it. The airport (VHHH)
        # sits ~6 km away and reads ~1 °C different, so silently falling through to
        # it when the Observatory feed hiccups makes the verdict jump between two
        # stations — exactly the instability we must not present as precision. If
        # the Observatory is transiently unavailable we let the loop exhaust and
        # drop to the honest ERA5-grid fallback instead.
        wants_hko = _wants_hko_anchor(place)
        if wants_hko:
            candidates = sorted(
                candidates, key=lambda s: not self.sources.is_hko_observatory(s))

        for st in candidates:
            if wants_hko and not self.sources.is_hko_observatory(st):
                continue                          # strict anchor: never the airport
            if strict_icao and (st.icao or "").upper() != strict_icao:
                continue                          # strict ICAO anchor: EGLC only, else grid
            try:
                series = self.sources.fetch_station_daily(st)
            except Exception:
                continue
            usable = {d: v for d, v in series.items()
                      if d <= default_end.isoformat()}
            if not usable:
                continue
            station_last = dt.date.fromisoformat(max(usable))
            if (default_end - station_last).days > MAX_TRUTH_STALE_DAYS:
                continue                      # defunct — not a current reference
            w_end = station_last
            w_start = w_end - dt.timedelta(days=window)
            obs = {d: v for d, v in usable.items()
                   if w_start.isoformat() <= d <= w_end.isoformat()}
            if len(obs) < MIN_SAMPLES:
                continue
            fp = Place(place.name, place.country,
                       st.latitude, st.longitude, place.timezone)
            data_source, feed = self._station_provenance(st)
            truth_source = {
                "kind": "station",
                "data_source": data_source,
                "station": {
                    "id": st.id, "name": st.name, "icao": st.icao, "wmo": st.wmo,
                    "latitude": st.latitude, "longitude": st.longitude,
                    "elevation": st.elevation,
                    "distance_km": round(st.distance_km, 1),
                },
                "label": (f"{st.label()} — {feed}, "
                          f"{st.distance_km:.0f} km from city centre"),
                "window_start": w_start.isoformat(),
                "window_end": w_end.isoformat(),
                "sample_days": len(obs),
                "lag_days": (default_end - station_last).days,
                "season_gap_days": _doy_gap(list(obs.keys()), target),
            }
            return fp, obs, w_start, w_end, truth_source

        # Fallback — ERA5 reanalysis grid at the city centroid (fresh to ~2 days).
        w_end = default_end
        w_start = w_end - dt.timedelta(days=window)
        obs = self.sources.fetch_archive_series(place, w_start, w_end)
        truth_source = {
            "kind": "era5_grid",
            "station": None,
            "label": (f"ERA5 reanalysis grid cell at "
                      f"{place.latitude:.3f}, {place.longitude:.3f}"),
            "window_start": w_start.isoformat(),
            "window_end": w_end.isoformat(),
            "sample_days": len(obs),
            "lag_days": ARCHIVE_LAG_DAYS,
            "season_gap_days": _doy_gap(list(obs.keys()), target),
        }
        return place, obs, w_start, w_end, truth_source

    # -- seasonal-analog bias correction (out-of-season targets only) -------- #
    def _analog_observed(self, fp: Place, truth_source: dict,
                         w_start: dt.date) -> DailySeries:
        """Observed truth over the analog reach [archive floor, window_start−1d],
        from the SAME source the verdict is anchored on (the settlement station,
        else the ERA5 grid). Capped strictly before the live window so the
        trailing-window bias can never leak into the analog estimate."""
        floor = SEASON_ANALOG_ARCHIVE_FLOOR
        cap = (w_start - dt.timedelta(days=1)).isoformat()
        ts = truth_source or {}
        if ts.get("kind") == "station" and (ts.get("station") or {}).get("id"):
            st_d = ts["station"]
            station = Station(
                id=str(st_d["id"]), name=st_d.get("name", ""),
                wmo=st_d.get("wmo"), icao=st_d.get("icao"),
                latitude=st_d.get("latitude", fp.latitude),
                longitude=st_d.get("longitude", fp.longitude),
                elevation=st_d.get("elevation"),
                distance_km=st_d.get("distance_km", 0.0),
            )
            try:
                series = self.sources.fetch_station_daily(station)
            except Exception:
                series = {}
        else:
            try:
                series = self.sources.fetch_archive_series(
                    fp, floor, w_start - dt.timedelta(days=1))
            except Exception:
                series = {}
        return {d: v for d, v in series.items()
                if floor.isoformat() <= d <= cap}

    def _apply_seasonal_analog(self, votes: list[Vote], fp: Place,
                               target: dt.date, w_start: dt.date,
                               truth_source: dict) -> None:
        """Out-of-season only: replace each member's trailing-window bias/skill
        with one learned from same-day-of-year analog days across prior years,
        and recompute its bias-corrected number. Purely data-derived — a member
        with too few analog pairs (seasonal_skill -> None) keeps its original
        correction. Records what it changed on truth_source for disclosure."""
        season_gap = (truth_source or {}).get("season_gap_days")
        if season_gap is None or season_gap <= SEASON_MATCH_DAYS:
            return                                  # in season — nothing to do

        analog_obs = self._analog_observed(fp, truth_source, w_start)
        if len(analog_obs) < MIN_SAMPLES:
            return                                  # archive too thin to re-learn

        a_start = SEASON_ANALOG_ARCHIVE_FLOOR
        a_end = w_start - dt.timedelta(days=1)
        members_corrected = 0
        for v in votes:
            try:
                hist = self.sources.fetch_history_series(
                    v.spec.model, fp, a_start, a_end)
            except Exception as exc:
                v.notes.append(f"seasonal-analog history unavailable: {exc}")
                continue
            changed = False
            for attr, raw, set_skill, set_corr in (
                ("high", v.raw_high,
                 lambda s: setattr(v, "skill_high", s),
                 lambda c: setattr(v, "corrected_high", c)),
                ("low", v.raw_low,
                 lambda s: setattr(v, "skill_low", s),
                 lambda c: setattr(v, "corrected_low", c)),
            ):
                sk = seasonal_skill(hist, analog_obs, target, attr)
                if sk is None or raw is None:
                    continue
                set_skill(sk)
                set_corr(raw - sk.bias)
                changed = True
            if changed:
                members_corrected += 1
                v.notes.append(
                    f"seasonal-analog bias: re-learned from ±{SEASON_ANALOG_WINDOW_DAYS}d "
                    f"day-of-year analog days {a_start.isoformat()}..{a_end.isoformat()} "
                    f"(out-of-season trailing window, gap ~{season_gap}d)")

        truth_source["seasonal_analog"] = {
            "applied": members_corrected > 0,
            "members_corrected": members_corrected,
            "window_days": SEASON_ANALOG_WINDOW_DAYS,
            "analog_start": a_start.isoformat(),
            "analog_end": a_end.isoformat(),
            "analog_obs_days": len(analog_obs),
        }

    def _apply_recency_bias(self, votes: list[Vote], fp: Place, target: dt.date,
                            truth_source: dict) -> None:
        """In-season SERVED bias upgrade: replace each member's plain trailing-window
        bias with an exponentially recency-weighted one (half-life from
        _served_bias_halflife), so the correction tracks a drifting in-season regime.

        This MOVES THE VERDICT and runs only where the leak-free recency-bias gate
        recommended it (today: Hong Kong) — a no-op for every other station. It is
        also a no-op OUT of season: seasonal-analog owns that regime (it re-learns
        the bias from day-of-year analogs), and double-correcting would be wrong, so
        recency defers whenever the trailing window is seasonally far from the
        target. Purely data-derived from each member's own paired history (the same
        hist_high/hist_low the trailing-mean correction already used); recomputes
        skill_high/low with the recency-weighted bias and weighted MAD and resets
        corrected_high/low so the live _blend serves the recency-corrected number.

        Coherence: Council._validate(fp=...) reproduces this exact bias per held-out
        day (its _blend_on_date headline runs at the SAME served half-life), so the
        reported MAE/CRPS/bucket/confidence measure what is actually served."""
        halflife = _served_bias_halflife(fp)
        if halflife is None:
            return                                   # station not opted in
        season_gap = (truth_source or {}).get("season_gap_days")
        if season_gap is not None and season_gap > SEASON_MATCH_DAYS:
            return                                   # out of season — seasonal-analog owns it
        target_iso = target.isoformat()
        members_corrected = 0
        for v in votes:
            changed = False
            for raw, hist, set_skill, set_corr in (
                (v.raw_high, v.hist_high,
                 lambda s: setattr(v, "skill_high", s),
                 lambda c: setattr(v, "corrected_high", c)),
                (v.raw_low, v.hist_low,
                 lambda s: setattr(v, "skill_low", s),
                 lambda c: setattr(v, "corrected_low", c)),
            ):
                if raw is None or len(hist) < MIN_SAMPLES:
                    continue
                dated = [(d, f - o) for d, (f, o) in hist.items()]
                bias, mad = recency_weighted_bias(dated, target_iso, halflife)
                mae_raw = statistics.mean(abs(e) for _, e in dated)
                set_skill(Skill(bias=bias, mae_raw=mae_raw, mae_corrected=mad,
                                n=len(dated)))
                set_corr(raw - bias)
                changed = True
            if changed:
                members_corrected += 1
                v.notes.append(
                    f"recency-weighted bias: {halflife:.0f}-day exponential weighting "
                    f"of the trailing window (served — tracks the station's drifting "
                    f"in-season bias; recency-bias gate recommended)")
        truth_source["recency_bias_applied"] = {
            "applied": members_corrected > 0,
            "members_corrected": members_corrected,
            "halflife_days": halflife,
        }

    # -- settlement alignment: native-grain quantization + source check ------ #
    def _settlement(self, fp: Place, truth_source: dict, observed: DailySeries,
                    high: float, low: float,
                    w_start: dt.date, w_end: dt.date) -> dict | None:
        """Map the verdict onto the form the record actually settles in.

        #2  Quantize the continuous verdict onto the airport sensor's *native*
            integer grain (whole °F for a US ASOS, whole °C internationally),
            detected from the data — that integer is what a whole-degree
            contract reads, and it can differ from the rounded continuous value.
        #4  Score how well the Meteostat daily truth the verdict is backtested
            against agrees with the raw METAR the public record derives from,
            so the proxy's reliability is visible (it tends to clip hot-day
            peaks). Station-anchored verdicts only; None when there is no
            airport METAR to settle against."""
        if (truth_source or {}).get("kind") != "station":
            return None
        icao = (truth_source.get("station") or {}).get("icao")
        if not icao:
            return None
        try:
            md = self.sources.fetch_metar_daily(icao, w_start, w_end, fp.timezone)
        except Exception:
            return None

        grain = md["grain"]
        high_settle, high_native = quantize_to_grain(high, grain)
        low_settle, low_native = quantize_to_grain(low, grain)

        metar = md["daily"]
        common = sorted(set(metar) & set(observed))
        check = None
        if common:
            dh = [metar[d][0] - observed[d][0] for d in common]
            dl = [metar[d][1] - observed[d][1] for d in common]
            check = {
                "n": len(common),
                "high_mean": round(statistics.mean(dh), 2),
                "high_median": round(statistics.median(dh), 2),
                "high_max": round(max(dh, key=abs), 2),
                "low_mean": round(statistics.mean(dl), 2),
                "low_median": round(statistics.median(dl), 2),
                "tail_days_ge3": sum(1 for x in dh if abs(x) >= 3.0),
            }
        return {
            "source": "raw airport METAR (IEM ASOS archive)",
            "grain": grain,
            "grain_evidence": md["grain_evidence"],
            "high_settle": round(high_settle, 1),
            "low_settle": round(low_settle, 1),
            "high_native": high_native,
            "low_native": low_native,
            "metar_window_days": len(metar),
            "source_check": check,
        }

    # -- mechanism convergence: independent corroboration (recommend-only) --- #
    def _convergence(self, observed: DailySeries, high: float, low: float,
                     naive_h: float | None, naive_l: float | None,
                     records: Records, v: Validation,
                     target: dt.date) -> dict | None:
        """Gather each verdict-forming mechanism's LIVE estimate of the day's
        high/low alongside its OWN held-out MAE, for the recommend-only
        convergence layer (see convergence.py). Returns prepared inputs, not a
        decision — the C7 gate is applied by the caller. None when the backtest
        produced no usable held-out scores.

        Mechanisms and lineages:
          * council     — skill-weighted, bias-corrected blend (the headline)
          * naive avg   — equal-weight multi-model mean
            ^ council and naive are BOTH functions of the same NWP forecasts, so
              they share the 'nwp' lineage and cannot count as independent
              corroboration of each other (convergence.py guardrail 1).
          * climatology — the seasonal normal for the date (independent lineage)
          * persistence — the most recent observed day (independent lineage)
        """
        if v is None or not observed:
            return None
        # Persistence is only an HONEST live mechanism when the latest observation
        # genuinely precedes the target (its backtested MAE is for true
        # day-over-day persistence). With the Meteostat archive lagging real time
        # by ~ARCHIVE_LAG_DAYS, the newest observed day is usually days stale — a
        # "5-days-ago" reading carries none of the 1-day MAE it would be scored
        # against, so we drop it rather than score a different mechanism against
        # the wrong error bar (the lineage simply abstains).
        last_date = max(observed)
        gap = (target - dt.date.fromisoformat(last_date)).days
        last = observed.get(last_date)
        if last is not None and 0 < gap <= CONVERGENCE_PERSIST_MAX_GAP_DAYS:
            persist_h, persist_l = last[0], last[1]
        else:
            persist_h = persist_l = None

        def build(attr: str, headline: float, naive: float | None,
                  normal: float | None, persist: float | None,
                  residuals: list[float]) -> ConvergenceInputs | None:
            n = len(residuals)
            specs = [
                ("council", "nwp", headline, _mae(v, "council", attr), n),
                ("naive avg", "nwp", naive, _mae(v, "naive", attr), n),
                ("climatology", "climatology", normal, _mae(v, "climatology", attr), n),
                ("persistence", "persistence", persist, _mae(v, "persistence", attr), n),
            ]
            mechs = tuple(
                Mechanism(name=nm, lineage=lin, estimate_c=float(est),
                          mae_c=float(mae), n=cnt)
                for (nm, lin, est, mae, cnt) in specs
                if est is not None and mae is not None
            )
            if len(mechs) < 2:
                return None
            spread = statistics.stdev(residuals) if len(residuals) >= 2 else None
            return ConvergenceInputs(
                quantity=attr, headline_c=float(headline), mechanisms=mechs,
                residual_spread_c=spread, n_resid=n)

        ch = build("high", high, naive_h, records.normal_high, persist_h,
                   v.residuals_high or [])
        cl = build("low", low, naive_l, records.normal_low, persist_l,
                   v.residuals_low or [])
        if ch is None and cl is None:
            return None
        return {"high": ch, "low": cl}

    def _basis(self, place: Place, truth_source: dict) -> str:
        if truth_source["kind"] == "station":
            st = truth_source["station"]
            return (
                f"daily max/min at surface station {st['name']}"
                + (f" ({st['icao']})" if st.get("icao") else "")
                + f", {st['distance_km']:.0f} km from the city centre. Every "
                f"centre's forecast is queried at the station coordinates "
                f"{place.latitude:.3f}, {place.longitude:.3f} and its skill is "
                f"backtested against this station's own observations "
                f"({truth_source['window_start']} → {truth_source['window_end']}, "
                f"{truth_source['sample_days']} days; station data lags real time "
                f"by ~{truth_source['lag_days']} days)."
            )
        return (
            f"daily max/min of the ERA5 / Open-Meteo grid cell at "
            f"{place.latitude:.3f}, {place.longitude:.3f} — the same quantity "
            f"the method is backtested against. No current station was near "
            f"enough to anchor on; a point observatory inside the cell can read "
            f"warmer or cooler (see representativeness)."
        )

    # -- stage builders --------------------------------------------------- #
    def _observe(self, place: Place, observed: DailySeries,
                 truth_source: dict) -> Observation:
        # Stage 1 lives in observation.observe — a Sources-only unit, no council
        # state. Kept as a thin method so call sites and tests are unchanged.
        return observe(self.sources, place, observed, truth_source)

    def _ensemble(self, place: Place, target: dt.date,
                  observed: DailySeries, w_start: dt.date,
                  w_end: dt.date) -> Ensemble:
        all_high: list[float] = []
        all_low: list[float] = []
        contributed: dict[str, int] = {}
        for model, label in ENSEMBLE_MODELS:
            try:
                highs, lows = self.sources.fetch_ensemble_members(model, place, target)
            except Exception:
                highs, lows = [], []
            if highs and lows:
                contributed[label] = min(len(highs), len(lows))
                all_high.extend(highs)
                all_low.extend(lows)

        def stats(xs: list[float]):
            if not xs:
                return (None, None, None, None, None)
            mean = statistics.mean(xs)
            sd = statistics.pstdev(xs) if len(xs) > 1 else 0.0
            agree = sum(1 for x in xs if abs(x - mean) <= 2.0) / len(xs)
            return (mean, sd, _pct(xs, 0.10), _pct(xs, 0.90), agree)

        mh, sh, p10h, p90h, ah = stats(all_high)
        ml, sl, p10l, p90l, al = stats(all_low)

        # Can the ensemble mean be backtested here? Pool every ensemble model's
        # historical mean against ERA5 truth; it must clear the same paired-day
        # bar as a deterministic member before it is allowed near the number.
        # We learn the bias on BOTH high and low so the ensemble mean can be
        # compared to the (already bias-corrected) deterministic blend on equal
        # footing — see _cross_system.
        pairs_h: list[tuple[float, float]] = []
        pairs_l: list[tuple[float, float]] = []
        for model, _ in ENSEMBLE_MODELS:
            try:
                hist = self.sources.fetch_ensemble_history_means(
                    model, place, w_start, w_end)
            except Exception:
                hist = {}
            for d, (fh, fl) in hist.items():
                if d in observed:
                    pairs_h.append((fh, observed[d][0]))
                    pairs_l.append((fl, observed[d][1]))
        backtest_days = len(pairs_h)
        eligible = backtest_days >= MIN_SAMPLES
        corrected_mean_high = corrected_mean_low = None
        if eligible:
            if mh is not None:
                corrected_mean_high = mh - statistics.mean(f - o for f, o in pairs_h)
            if ml is not None and pairs_l:
                corrected_mean_low = ml - statistics.mean(f - o for f, o in pairs_l)

        return Ensemble(
            member_count=len(all_high),
            models=contributed,
            mean_high=mh, mean_low=ml,
            spread_high=sh, spread_low=sl,
            p10_high=p10h, p90_high=p90h, p10_low=p10l, p90_low=p90l,
            agreement_high=ah, agreement_low=al,
            backtest_days=backtest_days,
            blend_eligible=eligible,
            corrected_mean_high=corrected_mean_high,
            corrected_mean_low=corrected_mean_low,
        )

    def _interpret(self, votes: list[Vote], included_high: list[str],
                   window: int) -> Interpretation:
        used = [v for v in votes if v.eligible]
        outliers = sum(1 for n in (note for v in votes for note in v.notes)
                       if "outlier" in n)
        biases_h = [abs(v.skill_high.bias) for v in used if v.skill_high]
        biases_l = [abs(v.skill_low.bias) for v in used if v.skill_low]
        return Interpretation(
            members_used=len(used),
            outliers_set_aside=outliers,
            mean_bias_removed_high=statistics.mean(biases_h) if biases_h else None,
            mean_bias_removed_low=statistics.mean(biases_l) if biases_l else None,
            history_days=window,
        )

    def _diurnal(self, place, target, w_start, w_end,
                 high: float, low: float, window: int) -> Diurnal:
        # Forecast: multi-model hourly consensus for the target day (local time).
        try:
            curve_full = self.sources.fetch_hourly_consensus(
                place, target, [m.spec.model for m in self.members])
        except Exception:
            curve_full = []

        peak_time = trough_time = None
        if curve_full:
            peak_t, _ = max(curve_full, key=lambda p: p[1])
            trough_t, _ = min(curve_full, key=lambda p: p[1])
            peak_time = peak_t[11:16]      # "HH:MM" local
            trough_time = trough_t[11:16]

        # Backtest: when did peaks/troughs actually land here, historically?
        try:
            archive = self.sources.fetch_hourly_archive(place, w_start, w_end)
        except Exception:
            archive = {}
        peak_hours, trough_hours = [], []
        for _day, pairs in archive.items():
            if len(pairs) < 20:            # need a near-complete day to be fair
                continue
            peak_hours.append(max(pairs, key=lambda p: p[1])[0])
            trough_hours.append(min(pairs, key=lambda p: p[1])[0])

        obs_peak, obs_peak_sd = _circ_stat(peak_hours)
        obs_trough, obs_trough_sd = _circ_stat(trough_hours)

        def in_band(clock, mean, sd):
            if clock is None or mean is None:
                return None
            tol = max(2.0, (sd or 0.0) * 2)   # within 2σ, never tighter than ±2h
            return _circ_dist(int(clock[:2]), mean) <= tol

        return Diurnal(
            peak_temp=high, peak_time=peak_time,
            trough_temp=low, trough_time=trough_time,
            curve=[(t[11:16], round(v, 1)) for t, v in curve_full],
            obs_peak_hour=obs_peak, obs_peak_sd=obs_peak_sd,
            obs_trough_hour=obs_trough, obs_trough_sd=obs_trough_sd,
            peak_in_band=in_band(peak_time, obs_peak, obs_peak_sd),
            trough_in_band=in_band(trough_time, obs_trough, obs_trough_sd),
            history_days=window,
        )

    def _records(self, place, target, peak_temp: float) -> Records:
        """Compare the target date to the long-term observed archive: the
        highest/lowest ever recorded around this calendar date, the normal, and
        where the forecast peak ranks. Pooled over ±RECORD_WINDOW_DAYS so the
        sample isn't a single day per year."""
        start = dt.date(CLIMO_START_YEAR, 1, 1)
        end = place_today(place) - dt.timedelta(days=ARCHIVE_LAG_DAYS)
        try:
            series = self.sources.fetch_climatology(place, start, end)
        except Exception:
            series = {}

        tdoy = target.timetuple().tm_yday
        highs: list[float] = []
        lows: list[float] = []
        rec_hi: tuple[float, int] | None = None
        rec_lo: tuple[float, int] | None = None
        for ds, (h, l) in series.items():
            try:
                d = dt.date.fromisoformat(ds)
            except ValueError:
                continue
            doy = d.timetuple().tm_yday
            if min(abs(doy - tdoy), 365 - abs(doy - tdoy)) > RECORD_WINDOW_DAYS:
                continue
            highs.append(h)
            lows.append(l)
            if rec_hi is None or h > rec_hi[0]:
                rec_hi = (h, d.year)
            if rec_lo is None or l < rec_lo[0]:
                rec_lo = (l, d.year)

        pctl = (sum(1 for x in highs if x <= peak_temp) / len(highs)
                if highs else None)
        return Records(
            since_year=CLIMO_START_YEAR,
            window_days=RECORD_WINDOW_DAYS,
            sample_days=len(highs),
            record_high=rec_hi[0] if rec_hi else None,
            record_high_year=rec_hi[1] if rec_hi else None,
            record_low=rec_lo[0] if rec_lo else None,
            record_low_year=rec_lo[1] if rec_lo else None,
            normal_high=statistics.mean(highs) if highs else None,
            normal_low=statistics.mean(lows) if lows else None,
            peak_percentile=pctl,
        )

    def _representativeness(self, place, w_start, w_end) -> Representativeness:
        """Measure how much the temperature field varies across the grid cells
        surrounding the target point. For each day we take the spread (σ) of the
        daily high/low across the centre cell and its neighbours, then average
        that across the window. A homogeneous location → near-zero σ (the grid
        value is a faithful stand-in for any point in it); a coastal/urban/
        mountain edge → large σ (a specific station may diverge by that much).
        This σ is an *independent* uncertainty axis from model disagreement."""
        offset = 0.25
        try:
            grids = self.sources.fetch_grid_neighbors(place, w_start, w_end, offset)
        except Exception:
            grids = []

        days = set()
        for g in grids:
            days.update(g.keys())

        sig_h, sig_l = [], []
        for d in days:
            highs = [g[d][0] for g in grids if d in g]
            lows = [g[d][1] for g in grids if d in g]
            if len(highs) >= 2:
                sig_h.append(statistics.pstdev(highs))
            if len(lows) >= 2:
                sig_l.append(statistics.pstdev(lows))

        mean_h = statistics.mean(sig_h) if sig_h else None
        mean_l = statistics.mean(sig_l) if sig_l else None
        worst = max([s for s in (mean_h, mean_l) if s is not None], default=None)
        return Representativeness(
            offset_deg=offset,
            neighbor_points=len(grids),
            sample_days=max(len(sig_h), len(sig_l)),
            spatial_sigma_high=mean_h,
            spatial_sigma_low=mean_l,
            sigma=worst,
        )

    # -- internals -------------------------------------------------------- #
    def _panel_sigma(self, votes, inc_h, wts_h, inc_l, wts_l) -> float | None:
        """Weighted σ of the deterministic panel around its blend, worst of H/L."""
        by_id = {v.spec.member_id: v for v in votes}

        def one(included, weights, attr):
            pairs = [(self._corrected(by_id[m], attr), weights[m])
                     for m in included if self._corrected(by_id[m], attr) is not None]
            return _weighted_std(pairs)

        sigmas = [s for s in (one(inc_h, wts_h, "high"),
                              one(inc_l, wts_l, "low")) if s is not None]
        return max(sigmas) if sigmas else None

    @staticmethod
    def _ensemble_sigma(e: Ensemble) -> float | None:
        sigmas = [s for s in (e.spread_high, e.spread_low) if s is not None]
        return max(sigmas) if sigmas else None

    @staticmethod
    def _cross_system(high: float, low: float, e: Ensemble) -> float | None:
        """Disagreement between two *views* of the same day: the deterministic
        control-run blend (all eight national centers) and the perturbed-ensemble
        mean. Worst of H/L.

        These two are NOT fully independent — the ensemble's three families
        (GEFS / ICON-EPS / GEPS) are the perturbed versions of GFS / ICON / GEM,
        which also sit in the deterministic panel — so a *small* gap is partly
        mechanical (shared lineage) and is deliberately never read as positive
        evidence of robustness. A *large* gap is still informative: it means
        initial-condition perturbations move the answer away from the control
        blend, i.e. genuine day-specific uncertainty. Accordingly this feeds the
        confidence calc one-directionally — it can only *raise* effective
        uncertainty (downgrade a tier), never manufacture confidence.

        The deterministic `high`/`low` are bias-corrected, so we compare against
        the ensemble's *bias-corrected* mean when we have earned that correction
        (its history cleared the paired-day bar). Comparing a corrected estimator
        to a raw one would book a removable systematic offset as genuine
        cross-system uncertainty and spuriously downgrade confidence; we fall
        back to the raw mean only when no correction was earned."""
        gaps = []
        eh = e.corrected_mean_high if e.corrected_mean_high is not None else e.mean_high
        el = e.corrected_mean_low if e.corrected_mean_low is not None else e.mean_low
        if eh is not None:
            gaps.append(abs(high - eh))
        if el is not None:
            gaps.append(abs(low - el))
        return max(gaps) if gaps else None

    @staticmethod
    def _corrected(vote: Vote, attr: str) -> float | None:
        return vote.corrected_high if attr == "high" else vote.corrected_low

    @staticmethod
    def _skill(vote: Vote, attr: str):
        return vote.skill_high if attr == "high" else vote.skill_low

    def _blend(self, votes: list[Vote], attr: str):
        usable = [v for v in votes
                  if v.eligible and self._corrected(v, attr) is not None]
        if not usable:
            # Distinguish a transient throttle (every member hit the same
            # rate-limited endpoint — retry shortly) from a genuine data gap, so
            # the caller doesn't treat a temporary 429 as "this city is
            # unforecastable". The retry/backoff in SafeHTTPClient already
            # absorbs isolated 429s; this only fires when the throttle outlasts
            # the whole retry budget for the entire council at once.
            throttled = any("rate-limited" in n for v in votes for n in v.notes)
            if throttled:
                raise RateLimitError(
                    f"no {attr} forecast: every council member's data source was "
                    f"rate-limited beyond the retry budget — transient, retry shortly"
                )
            raise ForecastUnavailableError(
                f"no eligible council member produced a {attr} forecast")

        vals = [self._corrected(v, attr) for v in usable]
        median = statistics.median(vals)
        mad = statistics.median([abs(x - median) for x in vals]) or 0.0
        thresh = max(OUTLIER_FLOOR_C, 3 * mad)

        included: list[Vote] = []
        for v in usable:
            x = self._corrected(v, attr)
            if abs(x - median) > thresh:
                v.notes.append(f"{attr} outlier ({x:.1f} vs median {median:.1f}) — excluded")
            else:
                included.append(v)
        if not included:                      # everyone disagreed wildly; keep all
            included = usable

        weights: dict[str, float] = {}
        for v in included:
            sk = self._skill(v, attr)
            mae = sk.mae_corrected if sk else 1.0
            weights[v.spec.member_id] = 1.0 / max(mae, 0.1) ** WEIGHT_POWER
        wsum = sum(weights.values()) or 1.0
        weights = {k: w / wsum for k, w in weights.items()}

        blended = sum(self._corrected(v, attr) * weights[v.spec.member_id]
                      for v in included)
        inc_vals = [self._corrected(v, attr) for v in included]
        spread = max(inc_vals) - min(inc_vals)
        return blended, [v.spec.member_id for v in included], spread, weights

    def _naive(self, votes: list[Vote], attr: str) -> float | None:
        raws = [(v.raw_high if attr == "high" else v.raw_low)
                for v in votes if v.eligible]
        raws = [r for r in raws if r is not None]
        return statistics.mean(raws) if raws else None

    def _wf_step(self, blend: tuple[float, float, float, tuple[float, ...]],
                 obs_v: float, clim: float, prev_v: float | None,
                 acc: SimpleNamespace) -> dict:
        """One held-out walk-forward step for a SINGLE attribute (high OR low).

        Records this day's per-attribute errors/residuals into `acc` (the six
        expanding lists for this attribute) and returns the shared-counter
        contributions for the caller to fold into the pooled high+low totals.
        Behavior-preserving extraction of the previously-duplicated high/low
        blocks: the CRPS cloud is still scored against ONLY strictly-earlier
        residuals — acc.resid / acc.prior_clim are read for the gate and the
        score BEFORE today's residual is appended."""
        council, naive, disp, members = blend
        err = abs(council - obs_v)
        r = obs_v - council
        acc.council_err.append(err)
        acc.naive_err.append(abs(naive - obs_v))
        acc.clim_err.append(abs(clim - obs_v))
        if prev_v is not None:
            acc.persist_err.append(abs(prev_v - obs_v))
        out = {
            "r": r,
            "disp": disp,
            "members": members,        # raw bias-corrected panel, for the rank histogram
            "pit": None,               # PIT of obs through the strictly-earlier cloud
            "win": 1 if err <= abs(naive - obs_v) else 0,
            "hit": 1 if err <= 2.0 else 0,
            "crps_c": None, "crps_cl": None, "covered": None, "width": None,
        }
        if len(acc.resid) >= CRPS_MIN_SAMPLES and len(acc.prior_clim) >= CRPS_MIN_SAMPLES:
            out["crps_c"] = crps_sample(acc.resid, r)
            out["crps_cl"] = crps_sample(acc.prior_clim, obs_v - clim)
            covered, width = interval_coverage(acc.resid, r)
            out["covered"] = covered
            out["width"] = width
            # PIT of today's residual through ONLY the strictly-earlier residual
            # cloud — the SAME leak-free distribution scored by CRPS and resampled
            # into bucket probabilities. Uniform PIT == that distribution is honest.
            out["pit"] = pit(acc.resid, r)
        acc.resid.append(r)
        acc.prior_clim.append(obs_v - clim)
        return out

    def _validate(self, votes: list[Vote], observed: DailySeries,
                  fp: Place | None = None) -> Validation:
        dates = sorted(observed.keys())
        if len(dates) < 15:
            return Validation(None, None, None, None, None, 0)
        # The bias half-life this station actually SERVES (None == plain mean).
        # The headline walk-forward (MAE/CRPS/bucket/confidence) is scored on this
        # served method so it measures what the verdict uses; the recency-bias
        # evaluation below stays a fixed plain-vs-recency A/B that keeps auditing
        # the choice regardless of what is served. fp=None (e.g. unit tests) keeps
        # the conservative plain-mean path.
        served_hl = _served_bias_halflife(fp)

        # Walk-forward (rolling-origin) evaluation. For every day after an
        # initial warmup, learn bias + weights ONLY from strictly-earlier
        # observed days and predict that held-out day. This scores far more
        # held-out days than a single 60/40 split (every day past the warmup,
        # not just the last 40%), so the hit-rate that anchors confidence is more
        # stable and the signed-residual distribution that drives the bucket
        # probabilities (C5) is much richer — without ever using the future to
        # predict the past. The warmup is MIN_SAMPLES, the same paired-sample
        # floor a member needs to vote at all, not a newly invented constant.
        warmup = MIN_SAMPLES
        test = dates[warmup:]
        if len(test) < 5:
            return Validation(None, None, None, None, None, 0)

        council_err_h, council_err_l = [], []
        naive_err_h, naive_err_l = [], []
        persist_err_h, persist_err_l = [], []
        clim_err_h, clim_err_l = [], []
        resid_h, resid_l = [], []           # signed: observed − council prediction
        prior_clim_h, prior_clim_l = [], []  # signed: observed − climatology, in order
        # Bundle each attribute's six expanding lists so one _wf_step handles
        # high and low identically. These reference the SAME list objects named
        # above, so the means computed at the return are unchanged.
        acc_h = SimpleNamespace(council_err=council_err_h, naive_err=naive_err_h,
                                clim_err=clim_err_h, persist_err=persist_err_h,
                                resid=resid_h, prior_clim=prior_clim_h)
        acc_l = SimpleNamespace(council_err=council_err_l, naive_err=naive_err_l,
                                clim_err=clim_err_l, persist_err=persist_err_l,
                                resid=resid_l, prior_clim=prior_clim_l)
        wins = comparisons = 0
        hits = 0
        hit_total = 0
        # Proper-scoring accumulators. Both council and climatology are dressed
        # with their OWN earlier held-out residuals, so a positive skill score
        # means the council's *distribution* (not just its point) beats the
        # naive baseline's distribution on identical days.
        crps_c_sum = crps_clim_sum = 0.0
        crps_count = cover_hits = cover_count = 0
        width_sum = 0.0
        # (signed residual, member dispersion) pairs for the conditional-spread
        # calibration check — pooled high+low, in walk-forward order. Recommend-only.
        calib_pairs: list[tuple[float, float]] = []
        # (member panel, observation) for the rank histogram, and leak-free PIT
        # values for the served-distribution calibration check — both pooled
        # high+low over the same walk-forward, both recommend-only.
        rank_inputs: list[tuple[tuple[float, ...], float]] = []
        pit_values: list[float] = []
        # Ordered (council point verdict, observed) pairs per attribute, for the
        # whole-degree bucket-verdict simulation — the object the market settles
        # on. Kept separate (high/low are separate markets) and scored leak-free
        # against each attribute's own expanding residual cloud. Measure-only.
        bucket_pairs_h: list[tuple[float, float]] = []
        bucket_pairs_l: list[tuple[float, float]] = []
        # Same pairs WITH the held-out date kept, per attribute — the surfaced
        # per-day stream for an external bucket-calibration backtest. Measure-only.
        wf_high: list[tuple[str, float, float]] = []
        wf_low: list[tuple[str, float, float]] = []
        wf_crps: list[tuple[str, str, float, float]] = []
        # Per-attribute ordered (incumbent_point, candidate_point, observed) triples
        # for the recency-weighted-bias evaluation. The candidate re-runs the SAME
        # leak-free blend with an exponential recency half-life on each member's
        # bias; the incumbent is the served plain-mean bias. Recommend-only — the
        # served verdict is always the incumbent. See recency_bias.evaluate.
        recency_streams: dict[str, list[tuple[float, float, float]]] = {
            "high": [], "low": []}

        for i, d in enumerate(test):
            obs = observed.get(d)
            if obs is None:
                continue
            # All strictly-earlier observed days are the training set for this
            # origin (expanding window).
            train = set(dates[:warmup + i])
            # Reference forecast #1 — climatology: the expanding mean of every
            # prior observed day (tracks the season as the window advances,
            # rather than freezing one training-window mean).
            clim_h = statistics.mean(observed[t][0] for t in train)
            clim_l = statistics.mean(observed[t][1] for t in train)
            # Reference forecast #2 — persistence: predict the previous day.
            prev = (dt.date.fromisoformat(d) - dt.timedelta(days=1)).isoformat()
            prev_obs = observed.get(prev)
            # Two leak-free blends per attribute: the PLAIN training-mean bias
            # (incumbent) and the RECENCY-weighted bias (candidate). The SERVED
            # headline uses whichever this station actually serves; the recency
            # evaluation always pairs incumbent-vs-candidate so it keeps auditing
            # that choice on fresh data.
            inc_h = self._blend_on_date(votes, "high", d, train)
            inc_l = self._blend_on_date(votes, "low", d, train)
            cand_h = self._blend_on_date(votes, "high", d, train,
                                         bias_halflife=RECENCY_HALFLIFE_DAYS)
            cand_l = self._blend_on_date(votes, "low", d, train,
                                         bias_halflife=RECENCY_HALFLIFE_DAYS)
            if served_hl is None:
                ch, cl = inc_h, inc_l
            elif served_hl == RECENCY_HALFLIFE_DAYS:
                ch, cl = cand_h, cand_l
            else:                                    # any other served half-life
                ch = self._blend_on_date(votes, "high", d, train, bias_halflife=served_hl)
                cl = self._blend_on_date(votes, "low", d, train, bias_halflife=served_hl)
            # High then low, scored identically. CRPS is translation-invariant,
            # so scoring the prior residual cloud against today's residual r is
            # identical to dressing the point forecast and scoring against the
            # observation — and acc.resid/acc.prior_clim hold ONLY earlier days.
            for blend, inc, cand, obs_v, clim, prev_v, acc in (
                (ch, inc_h, cand_h, obs[0], clim_h, prev_obs[0] if prev_obs else None, acc_h),
                (cl, inc_l, cand_l, obs[1], clim_l, prev_obs[1] if prev_obs else None, acc_l),
            ):
                if blend is None:
                    continue
                step = self._wf_step(blend, obs_v, clim, prev_v, acc)
                # blend[0] is the SERVED council point verdict for this held-out
                # day; obs_v is the realized value. Route to the right attribute's
                # stream for the bucket-verdict simulation (high vs low markets).
                (bucket_pairs_h if acc is acc_h else bucket_pairs_l).append(
                    (blend[0], obs_v))
                # Same routing, date retained, for the surfaced per-day stream.
                (wf_high if acc is acc_h else wf_low).append(
                    (d, blend[0], obs_v))
                # Recency-bias audit: pair the plain-bias point against the
                # recency-bias point for the same held-out day so the post-loop
                # evaluation can score whether recency-weighting sharpens the
                # distribution — independent of which one this station serves.
                attr = "high" if acc is acc_h else "low"
                if inc is not None and cand is not None:
                    recency_streams[attr].append((inc[0], cand[0], obs_v))
                calib_pairs.append((step["r"], step["disp"]))
                if step["members"] and len(step["members"]) >= 2:
                    rank_inputs.append((step["members"], obs_v))
                if step["pit"] is not None:
                    pit_values.append(step["pit"])
                comparisons += 1
                wins += step["win"]
                hits += step["hit"]
                hit_total += 1
                if step["crps_c"] is not None:
                    crps_c_sum += step["crps_c"]
                    crps_clim_sum += step["crps_cl"]
                    crps_count += 1
                    wf_crps.append((d, attr, step["crps_c"], step["crps_cl"]))
                    cover_hits += 1 if step["covered"] else 0
                    width_sum += step["width"]
                    cover_count += 1

        calibration = conditional_spread_eval(calib_pairs)
        # Same pairs, a complementary property measurement: the spread–skill
        # reliability of the member-dispersion signal (recommend-only).
        spread_skill = spread_skill_eval(calib_pairs)
        # Ensemble-calibration companions, also recommend-only: is the raw panel's
        # dispersion the right SIZE (rank histogram), and is the SERVED residual
        # cloud calibrated (PIT)? Together with spread_skill they verify the
        # bucket probabilities' spread end-to-end.
        rank_histogram = rank_histogram_eval(rank_inputs)
        pit_calibration = pit_calibration_eval(pit_values)
        # Scale companion to the shape checks above: is the served cloud the right
        # WIDTH? Scored PER ATTRIBUTE — high residuals against the served high cloud,
        # low against the served low cloud — because compare_high/compare_low dress
        # the buckets with residuals_high/residuals_low separately; pooling them would
        # measure a mixture the council never emits. Leak-free, CRPS-gated,
        # recommend-only — see calibration.coverage_calibration_eval_grouped.
        coverage_calibration = coverage_calibration_eval_grouped([resid_h, resid_l])
        # Bucket-verdict simulation: the economically-relevant score the market
        # settles on. Per attribute (high and low are separate markets), each
        # scored leak-free against its own expanding residual cloud. Measure-only.
        bucket_verdict_high = bucket_verdict_eval(bucket_pairs_h)
        bucket_verdict_low = bucket_verdict_eval(bucket_pairs_l)
        # Recency-weighted-bias candidate vs the served plain-mean bias, scored
        # leak-free on paired per-day CRPS (with a standard-error gate) AND the
        # whole-degree bucket-hit rate. Recommend-only: a positive verdict here is
        # a lever to pull, never an automatic change to the served blend.
        recency_bias = recency_bias_evaluate(recency_streams)
        # The SAME audit split per attribute, so a served per-station policy can be
        # justified (or refused) at the grain it is actually applied — each market
        # settles separately, and a pooled recommend can hide an attribute on which
        # recency does nothing. Leak-free (same triples), recommend-only.
        recency_bias_high = recency_bias_evaluate({"high": recency_streams["high"]})
        recency_bias_low = recency_bias_evaluate({"low": recency_streams["low"]})
        mean = lambda xs: statistics.mean(xs) if xs else None
        rmse = lambda xs: math.sqrt(statistics.mean(e * e for e in xs)) if xs else None
        crps_c = (crps_c_sum / crps_count) if crps_count else None
        crps_clim = (crps_clim_sum / crps_count) if crps_count else None
        crps_skill = (1.0 - crps_c / crps_clim
                      if crps_c is not None and crps_clim and crps_clim > 0 else None)
        return Validation(
            council_mae_high=mean(council_err_h),
            council_mae_low=mean(council_err_l),
            naive_mae_high=mean(naive_err_h),
            naive_mae_low=mean(naive_err_l),
            hit_rate_2c=(hits / hit_total) if hit_total else None,
            test_days=len(test),
            persistence_mae_high=mean(persist_err_h),
            persistence_mae_low=mean(persist_err_l),
            climatology_mae_high=mean(clim_err_h),
            climatology_mae_low=mean(clim_err_l),
            council_rmse_high=rmse(council_err_h),
            council_rmse_low=rmse(council_err_l),
            council_win_rate=(wins / comparisons) if comparisons else None,
            residuals_high=resid_h,
            residuals_low=resid_l,
            wf_high=wf_high,
            wf_low=wf_low,
            wf_crps=wf_crps,
            crps_council=crps_c,
            crps_climatology=crps_clim,
            crps_skill=crps_skill,
            coverage_80=(cover_hits / cover_count) if cover_count else None,
            sharpness_80=(width_sum / cover_count) if cover_count else None,
            crps_n=crps_count,
            calibration=calibration,
            spread_skill=spread_skill,
            rank_histogram=rank_histogram,
            pit_calibration=pit_calibration,
            coverage_calibration=coverage_calibration,
            bucket_verdict_high=bucket_verdict_high,
            bucket_verdict_low=bucket_verdict_low,
            recency_bias=recency_bias,
            recency_bias_high=recency_bias_high,
            recency_bias_low=recency_bias_low,
            bias_halflife_served=served_hl,
        )

    def _blend_on_date(self, votes: list[Vote], attr: str, day: str,
                       train: set[str], *, bias_halflife: float | None = None,
                       ) -> tuple[float, float, float, tuple[float, ...]] | None:
        """Return (council_blend, naive_mean, dispersion, members) for one held-out
        day, using bias and weights learned only from `train` dates. None if too
        sparse.

        `bias_halflife` is the recommend-only recency seam: when None (default,
        and the ONLY path the served verdict uses) each member's bias is the plain
        mean of its training errors. When set, the bias is recency-weighted with
        that exponential half-life (recency_bias.recency_weighted_bias) — used
        solely by the leak-free recency-bias evaluation, never by the live blend.

        The blend mirrors the LIVE `_blend` exactly: bias-correct each member from
        its own `train` history, apply the SAME MAD outlier screen
        (thresh = max(OUTLIER_FLOOR_C, 3*MAD) about the per-day median, with the
        keep-all fallback when every member trips it), then take the skill-weighted
        (1/MAE^WEIGHT_POWER) mean of the survivors. Validating the *served* method
        rather than an all-members proxy is the whole point — the held-out
        MAE/residual/PIT must measure what the council actually serves.

        `dispersion` is the spread of the SURVIVING bias-corrected members (the
        leak-free, per-day analog of the live ensemble spread); `members` is that
        same survivor set, surfaced so the walk-forward can build the rank
        histogram. Both are recommend-only and never move the verdict.
        `naive_mean` stays over ALL eligible members, matching the unscreened live
        `_naive` baseline."""
        naive_vals = []
        panel: list[tuple[float, float]] = []   # (bias-corrected forecast, weight)
        for v in votes:
            series = v.hist_high if attr == "high" else v.hist_low
            if day not in series:
                continue
            train_pairs = [series[d] for d in series if d in train]
            if len(train_pairs) < 5:
                continue
            if bias_halflife is None:
                bias = statistics.mean(f - o for f, o in train_pairs)
                mae_c = statistics.mean(abs(f - o - bias) for f, o in train_pairs)
            else:
                # Recommend-only recency seam: weight recent training days more so
                # the bias tracks a drifting regime. Pure helper; default path above
                # is untouched.
                bias, mae_c = recency_weighted_bias(
                    [(d, series[d][0] - series[d][1]) for d in series if d in train],
                    day, bias_halflife)
            w = 1.0 / max(mae_c, 0.1) ** WEIGHT_POWER
            f_day = series[day][0]
            naive_vals.append(f_day)
            panel.append((f_day - bias, w))
        if not panel or not naive_vals:
            return None

        # Same MAD outlier screen the live _blend serves, so the backtest measures
        # the served method. `or panel` reproduces live's keep-all fallback when
        # every member would be excluded.
        corrected_all = [c for c, _ in panel]
        median = statistics.median(corrected_all)
        mad = statistics.median([abs(c - median) for c in corrected_all]) or 0.0
        thresh = max(OUTLIER_FLOOR_C, 3 * mad)
        included = [(c, w) for c, w in panel if abs(c - median) <= thresh] or panel

        den = sum(w for _, w in included)
        if den <= 0:
            return None
        num = sum(w * c for c, w in included)
        corrected = [c for c, _ in included]
        disp = statistics.pstdev(corrected) if len(corrected) > 1 else 0.0
        return num / den, statistics.mean(naive_vals), disp, tuple(corrected)
