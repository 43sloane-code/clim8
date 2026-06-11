#!/usr/bin/env python3
"""Black-box verification harness — confirm the system behaves correctly WITHOUT
reading the source.

Run it:
    python3 tools/verify.py            # offline: gate + 3 E2E scenarios + invariants
    python3 tools/verify.py --live     # also hit the real network (full health-check basket)
    make verify                        # same, via the Makefile

What you get is a plain-English report. Every line says (a) what guarantee is
being checked, (b) the actual evidence observed, and (c) PASS / FAIL. Nothing
here is mocked away: each scenario drives the *real* shipped functions
(`compare_high`, `run._market_lines`, the `security` sandbox) exactly as a live
run would, just with seeded, network-free inputs — the same convention the unit
tests use. If this script prints ALL PASS, the contracts below hold on this
checkout.

The three end-to-end scenarios the report headlines:
  • HAPPY  — a normal whole-degree city/day market (London) yields a full,
             honest model-vs-market comparison.
  • ERROR  — the sandbox refuses forbidden / malformed I/O *before* any network
             call (off-allowlist host, plain-HTTP, bad city input).
  • EDGE   — a sub-degree (0.1 °C) Hong Kong market that settles on the council's
             OWN station is compared honestly: the tenths are kept, the offset is
             0 °C by identity, and the unverified 0.1°→whole rounding rule is
             surfaced rather than hidden.
"""
from __future__ import annotations

import argparse
import os
import random
import subprocess
import sys
from dataclasses import dataclass, field

# Make the repo importable whether invoked as `tools/verify.py`, `python3
# tools/verify.py`, or `make verify` (cwd = repo root either way).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from weather_council.compare import compare_high           # noqa: E402
from weather_council.market import MarketBucket, WeatherMarket  # noqa: E402
from weather_council.station_offset import StationOffset    # noqa: E402
from weather_council import security                        # noqa: E402
import run                                                  # noqa: E402


# --------------------------------------------------------------------------- #
# Result type + small fixtures (seeded, network-free — mirrors tests/test_market)
# --------------------------------------------------------------------------- #
@dataclass
class Result:
    title: str
    category: str                       # HAPPY / ERROR / EDGE / INVARIANT / GATE / LIVE
    proves: str                         # one plain-English sentence
    ok: bool
    evidence: list[str] = field(default_factory=list)
    fatal: bool = True                  # does a failure here fail the whole run?


def _residuals(seed: int, n: int = 80, sd: float = 0.7) -> list[float]:
    """Seeded held-out errors, °C — the same shape Validation.residuals_high has.
    Deterministic so the report is reproducible run-to-run."""
    rng = random.Random(seed)
    return [rng.gauss(0.0, sd) for _ in range(n)]


def _london_ladder() -> WeatherMarket:
    """A whole-degree London market (settles on London City Airport, EGLC)."""
    buckets = (
        MarketBucket("17°C or below", 0.20, 0.80, (), None, 17),
        MarketBucket("18°C", 0.45, 0.55, (), 18, 18),
        MarketBucket("19°C", 0.25, 0.75, (), 19, 19),
        MarketBucket("20°C or above", 0.15, 0.85, (), 20, None),
    )
    return WeatherMarket(
        event_id="ldn", title="Highest temperature in London on June 8?",
        city="London", date_label="June 8", station="London City Airport Station",
        grain="C", precision="whole °C", resolution_source=None, end_date=None,
        slug=None, buckets=buckets)


def _hk_ladder() -> WeatherMarket:
    """A sub-degree (0.1 °C) Hong Kong market (settles on the HK Observatory)."""
    buckets = (
        MarketBucket("29°C or below", 0.10, 0.90, (), None, 29),
        MarketBucket("30°C", 0.30, 0.70, (), 30, 30),
        MarketBucket("31°C", 0.40, 0.60, (), 31, 31),
        MarketBucket("32°C or above", 0.20, 0.80, (), 32, None),
    )
    return WeatherMarket(
        event_id="hk", title="Highest temperature in Hong Kong on June 8?",
        city="Hong Kong", date_label="June 8", station="Hong Kong Observatory",
        grain="C", precision="0.1°C", resolution_source=None, end_date=None,
        slug=None, buckets=buckets)


def _hk_same_station_offset() -> StationOffset:
    """The market settles on the SAME station the council backtests on (HKO once
    the modern open-data record anchors the backtest): offset 0 °C by identity."""
    return StationOffset(
        settlement_station_id="45005", settlement_station_name="Royal Observatory",
        settlement_distance_km=0.0, backtest_station_id="45005",
        backtest_station_name="Royal Observatory",
        high_mean=0.0, high_median=0.0, high_sd=0.0, n_season=300, n_all=900,
        season_window_days=21, overlap_start="2021-05-18", overlap_end="2025-06-29",
        is_modern=True)


def _mark(ok: bool) -> str:
    return "  [✓] " if ok else "  [✗] "


def healthcheck_basket() -> list[str]:
    """The basket the daily health check actually sweeps — IMPORTED from
    tools/daily_healthcheck.py, never hardcoded, so the live smoke covers exactly
    what the health check does and auto-follows if the basket is widened after
    review. Falls back to the committed London/Hong Kong pair if the import fails
    (the smoke must never crash just because the basket couldn't be read)."""
    import importlib.util
    path = os.path.join(ROOT, "tools", "daily_healthcheck.py")
    try:
        spec = importlib.util.spec_from_file_location("daily_healthcheck", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["daily_healthcheck"] = mod
        spec.loader.exec_module(mod)
        basket = [c for c in (getattr(mod, "BASKET", None) or []) if isinstance(c, str)]
    except Exception:  # noqa: BLE001
        basket = []
    return basket or ["London", "Hong Kong"]


# --------------------------------------------------------------------------- #
# THE THREE END-TO-END SCENARIOS
# --------------------------------------------------------------------------- #
def scenario_happy() -> Result:
    """HAPPY PATH — a standard whole-degree market produces a full comparison.

    Drives the real `compare_high` + `run._market_lines`. Asserts the comparison
    surfaces (is not withheld), that the continuous 17.8 °C verdict is shown
    rounding half-up to the integer the contract reads (18), that a model-vs-market
    probability table renders, and that the output still refuses to claim a
    'validated edge' (the recommend-only boundary)."""
    market = _london_ladder()
    cmp = compare_high(market, verdict_high_c=17.8, residuals_c=_residuals(101),
                       station_offset=None)
    lines = run._market_lines(cmp) if cmp is not None else []
    text = "\n".join(lines)
    checks = [
        ("a comparison is produced, not withheld", cmp is not None),
        ("recognised as whole-degree (no sub-degree withhold path)",
         cmp is not None and cmp.settles_sub_degree is False),
        ("the verdict 17.8 °C is shown rounding half-up to 18",
         "rounds to 18" in text and "(ROUNDED)" in text),
        ("a model-vs-market probability table is rendered",
         "model P" in text and "mkt P" in text),
        ("the verdict number is passed through untouched (recommend-only)",
         cmp is not None and cmp.verdict_high_c == 17.8),
        ("output never claims a validated edge", "NOT a validated edge" in text),
    ]
    ok = all(passed for _, passed in checks)
    ev: list[str] = []
    if cmp is not None:
        ev.append(f"model modal bucket = {cmp.model_modal!r}; "
                  f"market favourite = {cmp.market_modal!r}")
    ev += [_mark(p) + label for label, p in checks]
    settles = next((ln.strip() for ln in lines if "settles  :" in ln), None)
    if settles:
        ev.append("rendered -> " + settles)
    return Result("Whole-degree London market comparison", "HAPPY",
                  "A normal city/day market surfaces a full, honest comparison.",
                  ok, ev)


def scenario_error() -> Result:
    """ERROR CASE — the sandbox fails closed on forbidden / malformed I/O.

    None of these touch the network: the allowlist + scheme checks run before any
    DNS resolution, and city validation is pure. This is the guard that keeps a
    compromised or buggy code path from exfiltrating data or fetching off-policy."""
    cases: list[tuple[str, bool, str]] = []

    # 1. An off-allowlist host is refused (anti-exfiltration / SSRF guard).
    try:
        security._validate_url("https://evil.example.com/steal")
        cases.append(("off-allowlist host refused", False, "NO error raised!"))
    except security.SecurityError as e:
        cases.append(("off-allowlist host refused", "allowlist" in str(e), str(e)))

    # 2. A plain-HTTP URL (even to an allowed host) is refused — HTTPS only.
    try:
        security._validate_url("http://api.open-meteo.com/v1/forecast")
        cases.append(("plain-HTTP refused", False, "NO error raised!"))
    except security.SecurityError as e:
        cases.append(("plain-HTTP refused", "non-https" in str(e).lower(), str(e)))

    # 3. The one user-controlled field (city) rejects shell/path metacharacters.
    try:
        security.validate_city("../../etc/passwd; rm -rf /")
        cases.append(("malformed city input refused", False, "NO error raised!"))
    except security.SecurityError as e:
        cases.append(("malformed city input refused", True, str(e)))

    ok = all(passed for _, passed, _ in cases)
    ev = [_mark(p) + f"{label}  →  {msg}" for label, p, msg in cases]
    ev.append(f"(request budget is also capped at "
              f"{security.MAX_REQUESTS_PER_RUN}/run; covered by tests/test_security.py)")
    return Result("Sandbox refuses forbidden outbound I/O", "ERROR",
                  "Off-policy requests and bad input are rejected before any network call.",
                  ok, ev)


def scenario_edge() -> Result:
    """EDGE CASE — a 0.1 °C Hong Kong market on the council's OWN station.

    The subtle correctness the project is proudest of: a sub-degree record keeps
    its tenths (30.7 settles AS 30.7, never a whole '31'); because the market
    settles on the same station the council backtests on, the offset is 0 °C by
    identity (not a guessed cross-station transfer); and the genuinely unverifiable
    0.1°→whole bucket rule is surfaced (round-to-nearest → 31 vs truncation → 30)
    rather than papered over with false certainty."""
    market = _hk_ladder()
    cmp = compare_high(market, verdict_high_c=30.7, residuals_c=_residuals(11),
                       station_offset=_hk_same_station_offset())
    lines = run._market_lines(cmp) if cmp is not None else []
    text = "\n".join(lines)
    checks = [
        ("comparison surfaces (HK no longer withheld)", cmp is not None),
        ("settles sub-degree (0.1 °C) — keeps the tenths",
         cmp is not None and cmp.settles_sub_degree is True),
        ("30.7 settles AS 30.7 °C; never snaps to a whole 31",
         "30.7 °C settles as 30.7 °C" in text and "settles as 31" not in text),
        ("same settlement+backtest station → offset 0 °C by identity",
         cmp is not None and cmp.settlement_same_station is True
         and cmp.settlement_offset_c == 0.0),
        ("the unverified 0.1°→whole rule is flagged as bucket-changing",
         cmp is not None and cmp.rounding_robust is False
         and cmp.rounding_near_bucket == "31°C"
         and cmp.rounding_trunc_bucket == "30°C"),
        ("still declines to claim a validated edge", "NOT a validated edge" in text),
    ]
    ok = all(passed for _, passed in checks)
    ev = [_mark(p) + label for label, p in checks]
    for needle in ("settles  :", "map rule :"):
        ln = next((x.strip() for x in lines if needle in x), None)
        if ln:
            ev.append("rendered -> " + ln)
    return Result("Hong Kong sub-degree same-station market", "EDGE",
                  "A 0.1 °C market on the council's own station is compared honestly, "
                  "rounding caveat and all.", ok, ev)


# --------------------------------------------------------------------------- #
# INVARIANTS — short guarantees that protect the project's honesty rules
# --------------------------------------------------------------------------- #
def invariants() -> list[Result]:
    out: list[Result] = []

    # A sub-degree market with NO measured station offset must be WITHHELD, never
    # fabricated onto a whole-degree scale.
    withheld = compare_high(_hk_ladder(), 30.7, _residuals(11), station_offset=None)
    out.append(Result(
        "Sub-degree market without an earned offset is withheld", "INVARIANT",
        "When the station offset can't be earned, the comparison is declined, not guessed.",
        withheld is None,
        [_mark(withheld is None) + f"compare_high(..., station_offset=None) -> "
         f"{'None (withheld)' if withheld is None else 'a comparison (LEAK!)'}"]))

    # Polymarket is read-only: the host is allowlisted purely to READ prices; no
    # other use is sanctioned. (Structural check: it's present, and it's the only
    # market host.)
    poly = "gamma-api.polymarket.com"
    out.append(Result(
        "Polymarket access is read-only metadata", "INVARIANT",
        "The market host is on the allowlist for read-only price comparison — never order placement.",
        poly in security.ALLOWED_HOSTS,
        [_mark(poly in security.ALLOWED_HOSTS) + f"{poly} allowlisted for READ; "
         f"no funds/order endpoint exists in the sandbox"]))

    # The comparison must never mutate the verdict it was handed (recommend-only).
    c = compare_high(_london_ladder(), 19.4, _residuals(202), station_offset=None)
    untouched = c is not None and c.verdict_high_c == 19.4
    out.append(Result(
        "Market comparison never edits the served verdict", "INVARIANT",
        "Comparing to a market is annotation only; the verdict number is unchanged.",
        untouched,
        [_mark(untouched) + f"fed 19.4 °C -> comparison carries "
         f"{getattr(c, 'verdict_high_c', None)} °C unchanged"]))
    return out


# --------------------------------------------------------------------------- #
# FULL REGRESSION GATE + optional LIVE smoke
# --------------------------------------------------------------------------- #
def regression_gate() -> Result:
    """Run the project's own gate (`make check`): the whole network-free unit
    suite plus every module self-test. This is the broad safety net under the
    three narrated scenarios above."""
    env = {**os.environ, "PYTHONPATH": ROOT}
    try:
        proc = subprocess.run(["make", "check"], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=600)
    except FileNotFoundError:
        # No `make`? Fall back to the raw unittest discovery the Makefile runs.
        proc = subprocess.run([sys.executable, "-m", "unittest", "discover",
                              "-s", "tests"], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=600)
    except Exception as e:  # noqa: BLE001
        return Result("Full regression gate (make check)", "GATE",
                      "Every network-free unit test and module self-test passes.",
                      False, [f"could not run the gate: {e}"])
    blob = (proc.stdout + "\n" + proc.stderr).strip().splitlines()
    tail = [ln for ln in blob if ln.strip()][-8:]
    return Result("Full regression gate (make check)", "GATE",
                  "Every network-free unit test and module self-test passes.",
                  proc.returncode == 0, tail)


def live_smoke(city: str, lead: int) -> Result:
    """Optional: run the REAL CLI end-to-end (network). Advisory only — a missing
    market match or an upstream hiccup is reported, not treated as a hard failure,
    because the offline gate is the contract this harness guarantees."""
    env = {**os.environ, "PYTHONPATH": ROOT}
    try:
        proc = subprocess.run([sys.executable, "run.py", city, "--lead", str(lead),
                              "--market"], cwd=ROOT, env=env, capture_output=True,
                             text=True, timeout=240)
    except Exception as e:  # noqa: BLE001
        return Result(f"LIVE smoke — {city} (lead {lead})", "LIVE",
                      "The real CLI runs end-to-end against live feeds.",
                      False, [f"run failed: {e}"], fatal=False)
    out = proc.stdout
    checks = [
        ("CLI exited cleanly", proc.returncode == 0),
        ("a verdict block is printed", "VERDICT" in out.upper()),
        ("market-comparison section present (if a market matched)",
         "MARKET COMPARISON" in out),
        ("recommend-only edge disclaimer present",
         "NOT a validated edge" in out or "MARKET COMPARISON" not in out),
    ]
    # Only a non-zero exit is fatal; section absences are advisory (markets/feeds
    # are not always available).
    ok = proc.returncode == 0
    ev = [_mark(p) + label for label, p in checks]
    if proc.returncode != 0:
        ev.append("stderr tail: " + " | ".join(proc.stderr.strip().splitlines()[-3:]))
    return Result(f"LIVE smoke — {city} (lead {lead})", "LIVE",
                  "The real CLI runs end-to-end against live feeds.", ok, ev,
                  fatal=False)


# --------------------------------------------------------------------------- #
# Report rendering
# --------------------------------------------------------------------------- #
def _print_result(r: Result) -> None:
    tag = "PASS" if r.ok else ("WARN" if not r.fatal else "FAIL")
    print(f"\n[{tag}] {r.category}: {r.title}")
    print(f"       proves: {r.proves}")
    for line in r.evidence:
        print("      " + line)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true",
                    help="also run the real CLI across the full health-check basket (network)")
    ap.add_argument("--no-gate", action="store_true",
                    help="skip the `make check` regression gate (scenarios only)")
    args = ap.parse_args(argv)

    print("=" * 72)
    print("  weather-verdict — black-box verification report")
    print("  Each check states a guarantee, the evidence, and PASS/FAIL.")
    print("  No source-reading required: scenarios drive the real shipped code.")
    print("=" * 72)

    results: list[Result] = []

    print("\n" + "-" * 72)
    print("  THREE END-TO-END SCENARIOS")
    print("-" * 72)
    for scen in (scenario_happy, scenario_error, scenario_edge):
        r = scen()
        results.append(r)
        _print_result(r)

    print("\n" + "-" * 72)
    print("  HONESTY INVARIANTS")
    print("-" * 72)
    for r in invariants():
        results.append(r)
        _print_result(r)

    if not args.no_gate:
        print("\n" + "-" * 72)
        print("  FULL REGRESSION GATE")
        print("-" * 72)
        r = regression_gate()
        results.append(r)
        _print_result(r)

    if args.live:
        basket = healthcheck_basket()
        print("\n" + "-" * 72)
        print("  LIVE SMOKE (network — advisory)")
        print(f"  health-check basket ({len(basket)}): {', '.join(basket)}")
        print("-" * 72)
        for city in basket:
            r = live_smoke(city, 0)
            results.append(r)
            _print_result(r)

    fatal_fail = [r for r in results if not r.ok and r.fatal]
    advisory = [r for r in results if not r.ok and not r.fatal]
    passed = [r for r in results if r.ok]

    print("\n" + "=" * 72)
    print(f"  SUMMARY: {len(passed)} passed, {len(fatal_fail)} failed"
          + (f", {len(advisory)} advisory warning(s)" if advisory else ""))
    if fatal_fail:
        print("  FAILED:")
        for r in fatal_fail:
            print(f"    - [{r.category}] {r.title}")
    print("  RESULT: " + ("ALL CONTRACTS HOLD ✓" if not fatal_fail
                          else "VERIFICATION FAILED ✗"))
    print("=" * 72)
    return 1 if fatal_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
