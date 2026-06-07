"""Stage 1 of the council pipeline: assemble the live "observation" view.

Extracted verbatim from Council._observe so the observation step is a small,
independently-readable unit with one job — gather the current surface conditions
(overriding the live "now" temperature from the *settlement instrument* itself
for settlement-anchored cities), the last few observed days, and a provenance
"backbone" string. It depends only on a Sources handle, never on council state,
so it can be read, tested, and reasoned about without the 1.6k-line council.

No behaviour change: Council._observe now delegates here.
"""
from __future__ import annotations

__all__ = [
    'Observation', 'observe'
]

from dataclasses import dataclass

from .sources import DailySeries, Place, Sources

RECENT_OBS_DAYS = 3      # observed days surfaced in the observation step


@dataclass
class Observation:
    current: dict                         # current assimilated surface conditions
    recent: list[tuple[str, float, float]]  # (date, observed high, observed low)
    backbone: str


def observe(sources: Sources, place: Place, observed: DailySeries,
            truth_source: dict) -> Observation:
    """Gather the live observation view for one place.

    A settlement-anchored city's live "now" must come from the settlement
    instrument itself, not an Open-Meteo grid cell that can sit ~2 °C away. Hong
    Kong overrides temperature (and humidity, which HKO reports at the HQ) from
    the live HKO feed; London overrides temperature from the live EGLC METAR (the
    airport gauge the market resolves on). Wind/pressure stay from the grid and
    the temperature's source is surfaced so the provenance is never silently
    mixed."""
    try:
        current = sources.fetch_current(place)
    except Exception:
        current = {}
    if truth_source.get("data_source") == "hko_opendata":
        try:
            live = sources.hko_current()
        except Exception:
            live = None
        if live and live.get("temperature_2m") is not None:
            current = dict(current)
            current["temperature_2m"] = live["temperature_2m"]
            if live.get("relative_humidity_2m") is not None:
                current["relative_humidity_2m"] = live["relative_humidity_2m"]
            current["temperature_source"] = (
                live.get("temperature_source")
                or "Hong Kong Observatory (live rhrread)")
            current["temperature_record_time"] = live.get("record_time")
    elif truth_source.get("data_source") == "iem_metar":
        try:
            live = sources.eglc_current()
        except Exception:
            live = None
        if live and live.get("temperature_2m") is not None:
            current = dict(current)
            current["temperature_2m"] = live["temperature_2m"]
            current["temperature_source"] = (
                "London City Airport EGLC (live IEM METAR, whole-degree)")
            current["temperature_record_time"] = live.get("record_time")
    recent = [(d, observed[d][0], observed[d][1])
              for d in sorted(observed)[-RECENT_OBS_DAYS:]]
    if truth_source["kind"] == "station":
        st = truth_source["station"]
        if truth_source.get("data_source") == "hko_opendata":
            backbone = (f"{st['name']} surface observations via Hong Kong "
                        f"Observatory open data (the Observatory's own gauge — "
                        f"the point the HK temperature record settles on; live "
                        f"'now' reading from the HKO rhrread feed)")
        elif truth_source.get("data_source") == "iem_metar":
            backbone = (f"{st['name']} daily extremes reconstructed from raw "
                        f"IEM ASOS METAR (London City Airport's own EGLC sensor "
                        f"— the point the London market settles on)")
        else:
            backbone = (f"{st['name']} surface observations via Meteostat "
                        f"(aggregated METAR/SYNOP gauge readings — the point a "
                        f"temperature record settles on)")
    else:
        backbone = ("ERA5 reanalysis (assimilates satellite, radiosonde, "
                    "radar, ocean-buoy and ground-station observations)")
    return Observation(
        current=current,
        recent=recent,
        backbone=backbone,
    )
