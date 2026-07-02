"""singapore_pop_logger.py — POINT-IN-TIME PoP for the Singapore regime-split (pre-registered).

Logs, once per settlement day, the DAY-AHEAD daytime precipitation probability for WSSS from the one
frozen source (TWC v3 daypart precipChance), plus the squall-proxy qpf, tagged DRY/CONVECTIVE at the
pre-registered PoP≥40% threshold. Written the day BEFORE the target — never retro-fetched — so the
regime tag is leak-free. Recommend-only: this ledger never moves a served verdict; it exists solely
to give the [D14]-deferred regime split a leak-free record to be gated on in ~30 days.

Contract frozen in ledger/preregistered/singapore_pop_regime_split.md (commit hash = the freeze).
Run daily (wired into accumulate):  PYTHONPATH=. python3 tools/singapore_pop_logger.py
Self-test:                          PYTHONPATH=. python3 tools/singapore_pop_logger.py --selftest
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from weather_council.sources import Sources, WU_API_KEY

TWC_URL = "https://api.weather.com/v3/wx/forecast/daily/5day"
GEOCODE = "1.3502,103.994"                 # WSSS Changi — the settlement station
TZ = "Asia/Singapore"
THRESHOLD = 40.0                           # FROZEN: PoP >= 40% -> convective (pre-registered)
LOG = Path(__file__).resolve().parent.parent / "ledger" / "singapore_pop.jsonl"


def _pick_pop(valid, dpc, dqpf, target):
    """Daytime PoP + qpf for `target` date. TWC daypart is day/night-interleaved (2 entries per
    calendar day), so calendar-day index k -> daypart index 2k (the DAYTIME part). Returns
    (pop, qpf) or None (target absent, or its daytime part already elapsed -> None). PURE, KAT'd."""
    if not valid or not dpc:
        return None
    for k, v in enumerate(valid):
        if isinstance(v, str) and v[:10] == target:
            j = 2 * k
            if 0 <= j < len(dpc) and isinstance(dpc[j], (int, float)):
                q = dqpf[j] if (dqpf and j < len(dqpf) and isinstance(dqpf[j], (int, float))) else None
                return float(dpc[j]), q
            return None
    return None


def _already_logged(target):
    if not LOG.exists():
        return False
    with open(LOG) as f:
        return any(line.strip() and json.loads(line).get("target_date") == target for line in f)


def _selftest() -> int:
    valid = ["2026-07-02T07:00:00+0800", "2026-07-03T07:00:00+0800"]
    dpc = [None, 13, 62, 39]                # [today-day(past), tonight, tomorrow-day, tomorrow-night]
    dqpf = [None, 0.0, 0.35, 0.1]
    assert _pick_pop(valid, dpc, dqpf, "2026-07-03") == (62.0, 0.35)   # k=1 -> daypart index 2
    assert _pick_pop(valid, dpc, dqpf, "2026-07-02") is None           # k=0 -> index 0 = None (past)
    assert _pick_pop(valid, dpc, dqpf, "2026-07-09") is None           # absent
    assert _pick_pop([], dpc, dqpf, "2026-07-03") is None
    assert (62.0 >= THRESHOLD)                                         # 62% -> convective at frozen 40%
    print("singapore_pop_logger selftest PASS (2k daytime indexing; None/past/absent guards; threshold)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lead", type=int, default=1)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()

    src = Sources()
    now = _dt.datetime.now(ZoneInfo(TZ))
    target = (now.date() + _dt.timedelta(days=args.lead)).isoformat()
    if _already_logged(target):
        print(f"  Singapore PoP for {target} already logged (idempotent)")
        return 0
    try:
        d = src.http.get_json(TWC_URL, {"geocode": GEOCODE, "format": "json", "units": "e",
                                        "language": "en-US", "apiKey": WU_API_KEY})
    except Exception as e:
        print(f"  TWC PoP fetch failed ({type(e).__name__}: {str(e)[:60]})")
        return 0
    dp = (d.get("daypart") or [{}])[0]
    pp = _pick_pop(d.get("validTimeLocal"), dp.get("precipChance"), dp.get("qpf"), target)
    if not pp:
        print(f"  no daytime PoP for {target} yet")
        return 0
    pop, qpf = pp
    rec = {"target_date": target, "issued_ts": now.isoformat(timespec="seconds"),
           "source": "twc-v3-daypart-precipChance", "lead_days": args.lead,
           "pop": pop, "qpf": qpf, "regime": "convective" if pop >= THRESHOLD else "dry"}
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"  logged Singapore PoP {target}: {pop:.0f}% qpf={qpf} -> {rec['regime']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
