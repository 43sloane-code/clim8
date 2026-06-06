"""The council members.

Each member is a deterministic analyst bound to ONE top-band, independent
national weather center. It does no language-model generation: it fetches its
center's real forecast, scores that center's recent track record against
observed temperatures, learns a statistical bias correction, and casts a Vote.

A Vote carries both the raw and bias-corrected numbers plus the backtested
error that justifies how much the chair should trust it. Provenance is explicit
so any figure can be traced back to its source.
"""

from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass, field

from .sources import DailySeries, Place, Sources

# Minimum paired samples before a member is allowed to vote on the blend.
MIN_SAMPLES = 10


@dataclass(frozen=True)
class MemberSpec:
    member_id: str
    model: str           # Open-Meteo model id
    institution: str
    country: str


# The most reputable, mutually independent national NWP centers that offer
# global coverage AND a backtestable historical archive (verified empirically).
# Each is the actual operational model of a different national weather service,
# so the panel is genuinely independent rather than one model reskinned.
# Regional-only models (MET Norway / KNMI / DMI) are deliberately excluded:
# outside their home region their "seamless" feeds fall back to a global model,
# which would double-count and bias the ensemble.
COUNCIL: list[MemberSpec] = [
    MemberSpec("ecmwf", "ecmwf_ifs025", "ECMWF IFS", "EU"),
    MemberSpec("ukmo", "ukmo_seamless", "UK Met Office", "UK"),
    MemberSpec("gfs", "gfs_seamless", "NOAA GFS", "USA"),
    MemberSpec("icon", "icon_seamless", "DWD ICON", "Germany"),
    MemberSpec("arpege", "meteofrance_seamless", "Météo-France ARPEGE", "France"),
    MemberSpec("gem", "gem_seamless", "Env. Canada GEM", "Canada"),
    MemberSpec("jma", "jma_seamless", "JMA", "Japan"),
    MemberSpec("cma", "cma_grapes_global", "China CMA GRAPES", "China"),
]

# Stage-2 ensemble forecasting: each model is run many times with perturbed
# initial conditions. Pooling these members measures the chaotic spread of the
# atmosphere (do the runs agree or diverge?). Verified globally available with
# real daily member values; ECMWF's free tier exposes only a control run and
# BOM only serves hourly ensemble data, so both are excluded here (ECMWF is
# already in the deterministic panel above).
ENSEMBLE_MODELS: list[tuple[str, str]] = [
    ("gfs_seamless", "NOAA GEFS"),
    ("icon_seamless", "DWD ICON-EPS"),
    ("gem_global", "Env. Canada GEPS"),
]


@dataclass
class Skill:
    """Backtested error profile for one variable (high or low)."""
    bias: float          # mean(forecast - observed); + means runs warm
    mae_raw: float       # mean abs error before correction
    mae_corrected: float # mean abs error after removing bias
    n: int


@dataclass
class Vote:
    spec: MemberSpec
    target: str
    raw_high: float | None
    raw_low: float | None
    corrected_high: float | None
    corrected_low: float | None
    skill_high: Skill | None
    skill_low: Skill | None
    eligible: bool                 # enough history + a live number to count
    notes: list[str] = field(default_factory=list)
    # Per-variable historical forecast/observed pairs, kept for the chair's
    # held-out validation pass (date -> (forecast, observed)).
    hist_high: dict[str, tuple[float, float]] = field(default_factory=dict)
    hist_low: dict[str, tuple[float, float]] = field(default_factory=dict)


def _skill(pairs: list[tuple[float, float]]) -> Skill | None:
    """pairs = [(forecast, observed), ...] -> error profile, or None if empty."""
    if not pairs:
        return None
    diffs = [f - o for f, o in pairs]
    bias = statistics.mean(diffs)
    mae_raw = statistics.mean(abs(d) for d in diffs)
    mae_corrected = statistics.mean(abs(d - bias) for d in diffs)
    return Skill(bias=bias, mae_raw=mae_raw, mae_corrected=mae_corrected, n=len(pairs))


class ForecasterAgent:
    """One council member."""

    def __init__(self, spec: MemberSpec, sources: Sources) -> None:
        self.spec = spec
        self.sources = sources

    def analyze(self, place: Place, target: dt.date,
                window_start: dt.date, window_end: dt.date,
                observed: DailySeries) -> Vote:
        notes: list[str] = []

        # 1. This center's track record over the window, paired with truth.
        try:
            hist = self.sources.fetch_history_series(
                self.spec.model, place, window_start, window_end)
        except Exception as exc:
            hist = {}
            notes.append(f"history unavailable: {exc}")

        hist_high = {d: (hist[d][0], observed[d][0]) for d in hist if d in observed}
        hist_low = {d: (hist[d][1], observed[d][1]) for d in hist if d in observed}
        skill_high = _skill(list(hist_high.values()))
        skill_low = _skill(list(hist_low.values()))

        # 2. This center's live forecast for the target day.
        try:
            live = self.sources.fetch_live(self.spec.model, place, target)
        except Exception as exc:
            live = None
            notes.append(f"live forecast unavailable: {exc}")

        raw_high = live[0] if live else None
        raw_low = live[1] if live else None

        # 3. Apply the learned bias correction (statistical post-processing).
        corrected_high = (raw_high - skill_high.bias
                          if raw_high is not None and skill_high else raw_high)
        corrected_low = (raw_low - skill_low.bias
                         if raw_low is not None and skill_low else raw_low)

        n = min(skill_high.n if skill_high else 0,
                skill_low.n if skill_low else 0)
        eligible = (raw_high is not None and raw_low is not None
                    and n >= MIN_SAMPLES)
        if not eligible and n < MIN_SAMPLES:
            notes.append(f"insufficient backtest samples ({n} < {MIN_SAMPLES})")

        return Vote(
            spec=self.spec,
            target=target.isoformat(),
            raw_high=raw_high, raw_low=raw_low,
            corrected_high=corrected_high, corrected_low=corrected_low,
            skill_high=skill_high, skill_low=skill_low,
            eligible=eligible, notes=notes,
            hist_high=hist_high, hist_low=hist_low,
        )


def build_council(sources: Sources) -> list[ForecasterAgent]:
    return [ForecasterAgent(spec, sources) for spec in COUNCIL]
