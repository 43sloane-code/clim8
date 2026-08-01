"""Corroboration engine — Gate 1's judgement: is a cur_f lead CORROBORATED?

Frozen design (ledger/preregistered/cur_f_corroboration_guard_v2.md — binding):

    CORROBORATED = fresh ∧ (sustained ∨ converging)

  * fresh      — the current read's v3 obs stamp (valid_local) is no older than the
                 per-city ADAPTIVE freshness window (D4):
                 clamp(1.5 × trailing-median inter-obs interval, 10 min, 45 min);
                 fewer than 12 intervals → 45 min ceiling, basis="fallback".
  * sustained  — D2 liveness: ≥2 corroborating reads (cur_f at-or-above the lead's
                 whole-°F level), receipt-separated by ≥ min_separation_min (5 min),
                 DIFFERING on ≥1 secondary field. A stale value re-served with a fresh
                 timestamp carries IDENTICAL secondaries and fails — the London 07-11 /
                 KSFO 07-31 spoof class.
  * converging — D3, bounded: |cur_f − recorded| ≤ convergence_gap_max_f (2°F) AND
                 pre-peak per the city's frozen peak_window_local; UNAVAILABLE
                 post-peak (a post-peak "nowcast" cannot be converging on a peak that
                 has already formed — the Jeddah 07-09 phantom class).

PURE and deterministic: every input (the ObsLog read-sequence, the recorded floor,
the clock) is supplied by the caller. Fail-closed is the caller's wrapper
(guard.evaluate_cur_f_lead); the predicates here simply return False when the
evidence cannot prove the positive (unparseable stamps, missing reads, thin
interval history). KAT: tests/test_cur_f_guard.py.
"""
from __future__ import annotations

__all__ = ["CityGuardConfig", "Decision", "load_city_config",
           "freshness_window_min", "is_fresh", "sustained", "pre_peak",
           "converging", "decide", "f2c"]

import datetime as dt
import json
from dataclasses import dataclass, field

CONFIG_PATH = "config/guard_cities.json"
# D4 clamps (minutes) — frozen in config/guard_cities.json _meta.
FRESH_MIN_MIN = 10.0
FRESH_MAX_MIN = 45.0
FRESH_MIN_INTERVALS = 12


def f2c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


@dataclass(frozen=True)
class CityGuardConfig:
    """One city's frozen guard config (config/guard_cities.json)."""
    key: str
    tz: str
    peak_start_h: float
    peak_end_h: float
    convergence_gap_max_f: float
    min_separation_min: float
    secondary_fields: tuple[str, ...] = field(default_factory=tuple)


def load_city_config(icao: str, path: str = CONFIG_PATH) -> CityGuardConfig | None:
    """The frozen per-city config, matched on the ICAO suffix of the config key
    (e.g. KSFO -> SANFRANCISCO_KSFO). None when the city/config is unknown or the
    file is unreadable — the caller treats None as fail-closed."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    icao_u = (icao or "").strip().upper()
    for key, cfg in data.items():
        if key.startswith("_") or not isinstance(cfg, dict):
            continue
        if key.rsplit("_", 1)[-1].upper() != icao_u:
            continue
        try:
            start, end = cfg["peak_window_local"]
            return CityGuardConfig(
                key=key, tz=cfg["tz"],
                peak_start_h=float(str(start).split(":")[0])
                + float(str(start).split(":")[1]) / 60.0,
                peak_end_h=float(str(end).split(":")[0])
                + float(str(end).split(":")[1]) / 60.0,
                convergence_gap_max_f=float(cfg["convergence_gap_max_f"]),
                min_separation_min=float(cfg["min_separation_min"]),
                secondary_fields=tuple(cfg.get("secondary_fields") or ()))
        except (KeyError, TypeError, ValueError, IndexError):
            return None
    return None


def _parse_ts(s) -> dt.datetime | None:
    """Parse an ISO-8601 stamp (WU valid_local carries its own offset; ObsLog
    ts_utc is UTC). None on anything unparseable — a read whose time cannot be
    proven can corroborate nothing."""
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        t = dt.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        return None            # naive stamp: offset unprovable -> fail-closed
    return t


def freshness_window_min(inter_obs_min: list[float] | None) -> tuple[float, str]:
    """D4: clamp(1.5 × trailing-median inter-obs interval, 10, 45 min). Fewer than
    12 usable intervals -> the 45-min ceiling with basis="fallback" (the cadence is
    unproven, so the most permissive frozen window applies). Returns (window, basis)."""
    vals = sorted(v for v in (inter_obs_min or [])
                  if isinstance(v, (int, float)) and v > 0)
    if len(vals) < FRESH_MIN_INTERVALS:
        return FRESH_MAX_MIN, "fallback"
    mid = len(vals) // 2
    median = vals[mid] if len(vals) % 2 else 0.5 * (vals[mid - 1] + vals[mid])
    return min(FRESH_MAX_MIN, max(FRESH_MIN_MIN, 1.5 * median)), "adaptive"


def is_fresh(now_local: dt.datetime, valid_local: str | None,
             window_min: float) -> bool:
    """The current read's obs stamp is at most `window_min` old. A frozen
    valid_local (the stale-cur_f signature) ages out and fails; a missing or
    unparseable stamp fails closed."""
    stamp = _parse_ts(valid_local)
    if stamp is None or now_local is None or now_local.tzinfo is None:
        return False
    age_min = (now_local - stamp).total_seconds() / 60.0
    return 0.0 <= age_min <= window_min + 1e-9


def sustained(reads: list[dict], *, min_separation_min: float,
              secondary_fields) -> bool:
    """D2 liveness: ≥2 corroborating reads — cur_f at-or-above the latest read's
    whole-°F level — receipt-separated by ≥ min_separation_min AND differing on ≥1
    secondary field. A re-served stale payload (identical cur_f AND identical
    secondaries, however fresh its arrival) is ONE observation, not two."""
    defined = [r for r in (reads or [])
               if isinstance(r, dict) and isinstance(r.get("cur_f"), (int, float))]
    if len(defined) < 2:
        return False
    latest = round(defined[-1]["cur_f"])
    support = [r for r in defined if round(r["cur_f"]) >= latest]
    if len(support) < 2:
        return False
    # Liveness is proven by the secondaries, so a read that CARRIES none can prove
    # nothing: an empty/missing secondary payload never counts as a corroborating
    # read (else a stripped payload would trivially "differ" — the same spoof).
    def _live_payload(r):
        sec = r.get("secondaries") or {}
        return any(sec.get(k) is not None for k in secondary_fields)
    support = [r for r in support if _live_payload(r)]
    if len(support) < 2:
        return False
    first, last = support[0], support[-1]
    t0, t1 = _parse_ts(first.get("ts_utc")), _parse_ts(last.get("ts_utc"))
    if t0 is None or t1 is None:
        return False
    if (t1 - t0).total_seconds() < min_separation_min * 60.0 - 1e-9:
        return False
    s0, s1 = first.get("secondaries") or {}, last.get("secondaries") or {}
    return any(s0.get(k) != s1.get(k) for k in secondary_fields)


def pre_peak(now_local: dt.datetime, cfg: CityGuardConfig) -> bool:
    """Pre-peak per the frozen peak_window_local: the peak is still climatologically
    open (the window END covers the observed re-heat tail — a genuine late peak stays
    pre-peak). At/after the window end the day is post-peak and converging is
    UNAVAILABLE (D3)."""
    hour = now_local.hour + now_local.minute / 60.0 + now_local.second / 3600.0
    return hour < cfg.peak_end_h - 1e-9


def converging(cur_f: float, recorded_max_c: float, gap_max_f: float,
               pre_peak_now: bool) -> bool:
    """D3: |cur_f − recorded| ≤ gap_max_f AND pre-peak. Post-peak it is refused
    outright (bounded — a nowcast far above the record is never 'converging')."""
    if not pre_peak_now:
        return False
    if recorded_max_c is None:
        return False
    recorded_f = recorded_max_c * 9.0 / 5.0 + 32.0
    return abs(cur_f - recorded_f) <= gap_max_f + 1e-9


@dataclass(frozen=True)
class Decision:
    """Gate 1's judgement on the current read. `corroborated` is the ONLY field the
    banking gate consults; the rest are the audit trail (shadow log, KATs)."""
    corroborated: bool
    fresh: bool
    sustained: bool
    converging: bool
    pre_peak: bool
    freshness_window_min: float | None
    freshness_basis: str | None
    cur_f: float | None


def decide(reads: list[dict], *, now_local: dt.datetime,
           inter_obs_min: list[float] | None,
           recorded_max_c: float | None,
           cfg: CityGuardConfig) -> Decision:
    """CORROBORATED = fresh ∧ (sustained ∨ converging) over this city/day's
    read-sequence (the LATEST row is the read being judged). Every sub-predicate
    fails closed on missing/unprovable evidence."""
    defined = [r for r in (reads or [])
               if isinstance(r, dict) and isinstance(r.get("cur_f"), (int, float))]
    cur_f = defined[-1]["cur_f"] if defined else None
    window, basis = freshness_window_min(inter_obs_min)
    if cur_f is None:
        return Decision(corroborated=False, fresh=False, sustained=False,
                        converging=False, pre_peak=False,
                        freshness_window_min=window, freshness_basis=basis,
                        cur_f=None)
    latest = defined[-1]
    fresh = is_fresh(now_local, latest.get("valid_local"), window)
    sust = sustained(defined, min_separation_min=cfg.min_separation_min,
                     secondary_fields=cfg.secondary_fields)
    pp = pre_peak(now_local, cfg)
    conv = converging(cur_f, recorded_max_c, cfg.convergence_gap_max_f, pp)
    return Decision(corroborated=bool(fresh and (sust or conv)),
                    fresh=fresh, sustained=sust, converging=conv, pre_peak=pp,
                    freshness_window_min=window, freshness_basis=basis, cur_f=cur_f)
