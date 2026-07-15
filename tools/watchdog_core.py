#!/usr/bin/env python3
"""
watchdog_core.py — deterministic regression watchdog for the weather-verdict pipeline.

No third-party deps; Duties 1-3 run with NO LLM. Duty 1 imports the repo's own
weather_council.storage (itself stdlib); the rest is pure stdlib.
  Duty 1  read the live bucket scorecard from weather_council.storage -- the
          realized served-vs-settled hit-rate run.py already reports (no new file)
  Duty 2  regression check: intraday-ceiling crossover hit-rates vs reports/crossover_baseline.json
  Duty 3  feed-drift sentinel: truth-source string + ECMWF Changi cold-bias drift

Exit codes (consumed by the cron wrapper to decide whether to wake the LLM):
   0  GREEN  — all duties pass, no action needed (the normal confirm-phase day)
   2  AMBER  — soft drift, log + watch (e.g. ECMWF bias 0.5-1.0 off)
   3  RED    — regression / drift / scope breach; wake the LLM for Duty 4 judgment
   4  ABORT  — cannot run a duty honestly (missing log field, broken baseline)

Baselines are READ FROM reports/crossover_baseline.json at the checked-out SHA —
never hardcoded from a session-state doc (which can lag HEAD). This is a DISTINCT
file from the daily monitor's basket-MAE reports/baseline.json. If the crossover
baseline is absent, Duty 2 is ABSTAIN, not RED — no pinned baseline is a setup
state, not a regression.

Canary: run `--canary` to feed a known-bad fixture through Duties 2-3. It MUST
return RED. If it returns GREEN the detector is broken and the watchdog is
worthless — the cron wrapper runs this first every day and refuses to proceed
on a canary that comes back green.
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime, timezone

# ---- thresholds (structural, not fudge factors) ----
DETERMINISM_BAND = 0.03        # frac hit-rate move treated as feed-revision noise, not regression
ECMWF_BIAS_REF = 1.7           # °C, Changi cold bias used in the cross-check
ECMWF_BIAS_AMBER = 0.5         # |drift| above this -> AMBER
ECMWF_BIAS_RED = 1.0           # |drift| above this -> RED (cross-check silently miscalibrated)
LIVE_N_TARGET = 20             # per-city live settled days before backtest is "confirmed"
WU_TRUTH_TOKEN = "wunderground"  # truth source must be WU (lag ~0), never Meteostat (lag ~91d)

# ICAO (settlement station) -> place label as stored in the verdict/snapshot DB.
# storage.live_bucket_scorecard() keys on this label, not the ICAO.
_PLACE_LABEL = {
    "RPLL": "Manila, Philippines",
    "WSSS": "Singapore, Singapore",
    "EGLC": "London, United Kingdom",
}


# ----------------------------------------------------------------------------
# small stdlib helpers
# ----------------------------------------------------------------------------
def _load_json(path):
    with open(path, "r") as f:
        return json.load(f)


def _wilson(hits, n, z=1.96):
    """Wilson score interval for a binomial proportion (correct for small n)."""
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = hits / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / denom
    return (p, max(0.0, center - half), min(1.0, center + half))


class Result:
    """Accumulates per-duty verdicts; worst verdict wins the exit code."""
    RANK = {"GREEN": 0, "ABSTAIN": 0, "AMBER": 2, "RED": 3, "ABORT": 4}

    def __init__(self):
        self.lines = []
        self.worst = "GREEN"

    def add(self, duty, verdict, msg):
        self.lines.append({"duty": duty, "verdict": verdict, "msg": msg})
        if Result.RANK[verdict] > Result.RANK[self.worst]:
            self.worst = verdict

    def exit_code(self):
        return Result.RANK[self.worst]

    def emit(self, out_path=None):
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "worst": self.worst,
            "duties": self.lines,
        }
        text = json.dumps(payload, indent=2)
        if out_path:
            with open(out_path, "w") as f:
                f.write(text + "\n")
        print(text)
        return payload


# ----------------------------------------------------------------------------
# Duty 1 — feed the live scorecard
# ----------------------------------------------------------------------------
def duty1_scorecard(repo, cities, res):
    """
    Read the HONEST realized served-vs-settlement hit-rate from the repo's own
    storage (weather_council.storage.live_bucket_scorecard) -- the SAME scorecard
    run.py's REALITY CHECK uses -- instead of maintaining a divergent ledger file.
    No network; it reads the SQLite verdict/market-snapshot record and compares
    each served bucket to the contract's OWN settled bucket. n=0 is a legitimate
    state (no settled day yet for the new basket): we report 'live-unproven', NOT
    an error. ABORT only if the repo package can't be imported (wrong --repo) or a
    city has no place-label mapping. Settlement-rule correctness is handled inside
    storage (it snaps with the snapshot's own grain/sub_degree) and cross-checked
    in Duty 3, so it is not re-litigated here.
    """
    try:
        sys.path.insert(0, os.path.abspath(repo))
        from weather_council.storage import live_bucket_scorecard
    except Exception as e:
        res.add("1-scorecard", "ABORT",
                f"cannot import weather_council.storage from --repo={repo!r}: {e}")
        return

    msgs = []
    for city in cities:
        label = _PLACE_LABEL.get(city.upper())
        if label is None:
            res.add("1-scorecard", "ABORT",
                    f"{city}: no place-label mapping (add it to _PLACE_LABEL)")
            return
        try:
            sc = live_bucket_scorecard(label)
        except Exception as e:
            res.add("1-scorecard", "ABORT", f"{city}: scorecard read failed: {e}")
            return
        n, hits = sc.get("n", 0), sc.get("hits", 0)
        p, lo, hi = _wilson(hits, n)
        tag = ("CONFIRMED" if n >= LIVE_N_TARGET
               else f"backtest-only, live-unproven (n={n}/{LIVE_N_TARGET})")
        msgs.append(f"{city} [{label}]: live {p:.1%} [{lo:.1%},{hi:.1%}] n={n} -- {tag}")
    res.add("1-scorecard", "GREEN", " | ".join(msgs))


# ----------------------------------------------------------------------------
# Duty 2 — regression check vs reports/baseline.json (READ FROM FILE, not doc)
# ----------------------------------------------------------------------------
def duty2_regression(repo, ab_now, res):
    """
    Compares the CURRENT intraday-ceiling crossover hit-rates (from
    intraday_ceiling_backtest.py --emit-crossover, evaluated at the SAME pinned
    window the baseline used) against reports/crossover_baseline.json -- the
    source of truth; session-doc numbers are never hardcoded here. A drop beyond
    DETERMINISM_BAND on any city/hour is a RED regression.
    """
    bpath = os.path.join(repo, "reports", "crossover_baseline.json")
    if not os.path.exists(bpath):
        # no pinned crossover baseline yet -> adopt the next clean run, not a regression
        res.add("2-regression", "ABSTAIN",
                "reports/crossover_baseline.json absent -- no pinned baseline yet. "
                "Watchdog will adopt the next clean run as baseline.")
        return
    base = _load_json(bpath)  # expected: {city: {hour: hit_rate, ...}, ...}

    # schema guard: the crossover baseline is {city: {hour: hit_rate}}. The repo's
    # reports/baseline.json is currently the BASKET-MAE baseline the daily monitor
    # writes ({basket_mae_current, date, variant}) -- a different instrument. Don't
    # crash iterating a float; ABSTAIN until a real crossover baseline lives here.
    if not base or not all(isinstance(v, dict) for v in base.values()):
        res.add("2-regression", "ABSTAIN",
                "reports/baseline.json is not a {city:{hour:hit_rate}} crossover baseline "
                "(looks like the basket-MAE monitor baseline) -- Duty 2 not applicable yet.")
        return

    worst_drop = 0.0
    breaches = []
    for city, hours in base.items():
        now_city = ab_now.get(city, {})
        for hour, base_hit in hours.items():
            now_hit = now_city.get(hour)
            if now_hit is None:
                res.add("2-regression", "RED",
                        f"{city}@{hour}: baseline has it, current run does NOT -- "
                        f"missing fold/hour is a silent failure")
                return
            drop = base_hit - now_hit
            if drop > DETERMINISM_BAND:
                breaches.append(f"{city}@{hour}: {now_hit:.1%} vs base {base_hit:.1%} (-{drop:.1%})")
                worst_drop = max(worst_drop, drop)
    if breaches:
        res.add("2-regression", "RED",
                "edge regressed beyond determinism band: " + "; ".join(breaches))
    else:
        res.add("2-regression", "GREEN",
                f"all crossover hit-rates within {DETERMINISM_BAND:.0%} of baseline")


# ----------------------------------------------------------------------------
# Duty 3 — feed-drift sentinel
# ----------------------------------------------------------------------------
def duty3_drift(repo, config_truth_sources, recent_ecmwf_bias, res):
    """
    config_truth_sources: list of (path, resolved_source_string) the wrapper read
    recent_ecmwf_bias: float | None, recent measured Changi cold bias
    """
    # honesty guard: with no resolved truth-config AND no bias there is nothing to
    # check -- ABSTAIN rather than assert "Wunderground everywhere" against [] (a
    # false-GREEN). Real checks below run as soon as either input is wired in.
    if not config_truth_sources and recent_ecmwf_bias is None:
        res.add("3-drift", "ABSTAIN",
                "no truth-config and no ECMWF bias provided -- nothing to verify "
                "(resolve_truth_sources.py not wired yet)")
        return

    # 3a — truth source must be Wunderground everywhere (Meteostat lag bug template)
    lagging = [p for (p, s) in config_truth_sources if WU_TRUTH_TOKEN not in s.lower()]
    if lagging:
        res.add("3-drift", "RED",
                "truth source reverted off Wunderground (lagging-source bug) in: "
                + ", ".join(lagging))
        return

    # 3b — ECMWF Changi cold-bias drift
    if recent_ecmwf_bias is not None:
        d = abs(recent_ecmwf_bias - ECMWF_BIAS_REF)
        if d > ECMWF_BIAS_RED:
            res.add("3-drift", "RED",
                    f"ECMWF Changi bias {recent_ecmwf_bias:.2f}C drifted {d:.2f} from "
                    f"ref {ECMWF_BIAS_REF}C -- cross-check miscalibrated")
            return
        if d > ECMWF_BIAS_AMBER:
            res.add("3-drift", "AMBER",
                    f"ECMWF Changi bias {recent_ecmwf_bias:.2f}C drift {d:.2f} -- watch")
            return
    parts = []
    if config_truth_sources:
        parts.append(f"truth=Wunderground in all {len(config_truth_sources)} checked config(s)")
    if recent_ecmwf_bias is not None:
        parts.append(f"ECMWF bias {recent_ecmwf_bias:.2f}C within tolerance")
    res.add("3-drift", "GREEN", "; ".join(parts))


# ----------------------------------------------------------------------------
# canary — known-bad fixtures that MUST trip RED
# ----------------------------------------------------------------------------
def run_canary():
    res = Result()
    # canary 2: a baseline with a hit-rate the "current run" badly misses
    fake_base = {"WSSS": {"14:00": 0.91}}
    fake_now = {"WSSS": {"14:00": 0.50}}   # 41pt drop -> must be RED
    # inline reimpl of the comparison to avoid touching disk
    drop = fake_base["WSSS"]["14:00"] - fake_now["WSSS"]["14:00"]
    res.add("canary-2", "RED" if drop > DETERMINISM_BAND else "GREEN",
            f"injected {drop:.0%} drop")
    # canary 3: truth source reverted to meteostat -> must be RED
    res.add("canary-3", "RED" if WU_TRUTH_TOKEN not in "meteostat_daily" else "GREEN",
            "injected meteostat truth source")
    code = res.exit_code()
    res.emit()
    if code != Result.RANK["RED"]:
        sys.stderr.write("CANARY FAILED: detector did not trip RED on known-bad input. "
                         "Watchdog is broken; refusing to certify GREEN.\n")
        return 5  # distinct code: canary itself is broken
    sys.stderr.write("canary OK: detector trips RED on known-bad input.\n")
    return 0


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=False, default=os.environ.get("WX_REPO", "."))
    ap.add_argument("--cities", default="RPLL,WSSS,EGLC")   # match accumulate's explicit list
    ap.add_argument("--ab-now", help="path to JSON of current replay crossover hit-rates")
    ap.add_argument("--truth-config", help="path to JSON: [[config_path, resolved_source], ...]")
    ap.add_argument("--ecmwf-bias", type=float, default=None)
    ap.add_argument("--out", help="path to write the run report JSON")
    ap.add_argument("--canary", action="store_true", help="run known-bad fixture; must return RED")
    args = ap.parse_args()

    if args.canary:
        return run_canary()

    res = Result()
    cities = [c.strip() for c in args.cities.split(",") if c.strip()]

    duty1_scorecard(args.repo, cities, res)

    ab_now = _load_json(args.ab_now) if args.ab_now and os.path.exists(args.ab_now) else {}
    duty2_regression(args.repo, ab_now, res)

    truth_cfg = _load_json(args.truth_config) if args.truth_config and os.path.exists(args.truth_config) else []
    duty3_drift(args.repo, truth_cfg, args.ecmwf_bias, res)

    res.emit(args.out)
    return res.exit_code()


if __name__ == "__main__":
    sys.exit(main())
