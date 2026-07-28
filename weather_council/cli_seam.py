"""cli_seam — the measured CLI-vs-obs/WU seam series for CLI-primary (Kalshi)
stations. ONE loader for every reader (run.py verdict render, tools/finegrain_read.py
pattern caveat, tools/mc_verdict_sim.py validation) so the served number can never
drift between surfaces: the seam is read from ledger/<icao>_cli_wu.jsonl, screened
by clean_divergences (±8°F out-of-band bound — the PRELIMINARY-vs-FINAL CLI
correction class, kalshi_sf_seam.md rule 1), last ≤30 rows.

Series shape: one JSON per line, {"date", "cli_high", "cli_time", "wu_max_f",
"divergence"} — written by the kalshi_logger daily duty.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

SEAM_BOUND_F = 8.0
SEAM_WINDOW = 30


def clean_divergences(divs, bound_f: float = SEAM_BOUND_F) -> tuple[list, int]:
    """Keep finite non-bool numbers inside ±bound_f; reject the rest as
    parse/correction artifacts. Returns (kept, n_rejected) in original order."""
    kept, rejected = [], 0
    for x in divs:
        if (isinstance(x, (int, float)) and not isinstance(x, bool)
                and abs(x) <= bound_f):
            kept.append(x)
        else:
            rejected += 1
    return kept, rejected


def load_cli_seam(ledger_dir: Path, icao: str) -> dict | None:
    """{"mean", "n"} over the last ≤SEAM_WINDOW cleaned divergences ("rejected"
    added when the screen dropped rows), or None when no usable series exists."""
    path = Path(ledger_dir) / f"{icao.lower()}_cli_wu.jsonl"
    try:
        raw = [json.loads(l).get("divergence") for l in
               path.read_text().splitlines() if l.strip()]
        divs, rejected = clean_divergences(raw)
        divs = divs[-SEAM_WINDOW:]
        if divs:
            out = {"mean": round(statistics.mean(divs), 2), "n": len(divs)}
            if rejected:
                out["rejected"] = rejected
            return out
    except Exception:
        pass
    return None
