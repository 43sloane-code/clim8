"""Emit the resolved truth-source per tracked city for watchdog_core Duty 3.

Duty 3's failure mode is a SILENT regression off the Wunderground settlement
oracle back to a lagging source (the Meteostat ~91-day lag bug). This resolver
reads the wiring that actually decides truth — council._WU_TRUTH_STATIONS — and
prints, per tracked city, [config_path, resolved_source_string]. If a city is no
longer wired to its WU airport, the string won't contain "wunderground" and
Duty 3 trips RED. Deterministic, no network.

Output: JSON  [[config_path, source_string], ...]  (the --truth-config Duty 3 reads)
Usage:  PYTHONPATH=. python3 tools/resolve_truth_sources.py
"""
from __future__ import annotations
import json
import sys

from weather_council.council import _WU_TRUTH_STATIONS

# ICAO each tracked city MUST still resolve to (mirror of the watchdog basket).
TRACKED = {"manila": "RPLL", "singapore": "WSSS"}


def resolve() -> list[list[str]]:
    out: list[list[str]] = []
    for city, icao in TRACKED.items():
        st = _WU_TRUTH_STATIONS.get(city) or {}
        if st.get("icao") == icao:
            src = f"Wunderground / Weather Company settlement oracle ({icao})"
        else:
            # drift: city dropped from WU wiring or re-pointed -> no "wunderground"
            src = f"NON-WU truth for {city} (wired icao={st.get('icao')!r}, expected {icao}) -- DRIFT"
        out.append([f"council._WU_TRUTH_STATIONS[{city}]", src])
    return out


if __name__ == "__main__":
    json.dump(resolve(), sys.stdout)
    sys.stdout.write("\n")
