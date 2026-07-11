# Pre-registration — TWC signed-offset cross-reference (frozen before any direction is asserted)

*2026-07-11 (Execution Plan 4). The Weather Company forecast enters as an ADDITIONAL cross-reference
only — the same standing as the IEM/anchor cross-references. Wunderground airport records remain the
settlement oracle. TWC never becomes a truth source, never feeds a vote, never touches settlement.
Its known tendency to run above or below the oracle is measured per city with a signed sign+magnitude
and applied to its DISPLAYED cross-reference reading only — never to the served verdict. Any promotion
of TWC into the blend routes through Plan 3's candidate/shadow/promotion machinery and is out of scope
here by the human's own instruction.*

## kind: PROCESS — Phase 0 endpoint probe evidence (empirical, no remembered API shapes)
Probed live from the accumulator host with the existing `WU_API_KEY` (same public web key as the WU
truth path — no new host, no new key). Evidence committed verbatim to `reports/twc_probe.json`
(2026-07-11T11:59:42Z). Endpoint: `GET https://api.weather.com/v3/wx/forecast/daily/5day`.

- **Field map (verified, not trusted):** calendar-day max/min live in the parallel arrays
  `calendarDayTemperatureMax` / `calendarDayTemperatureMin`, index-aligned to `validTimeLocal`.
- **Timezone / day boundary:** each element of `validTimeLocal` carries the station's LOCAL UTC
  offset (Singapore/Manila `+0800`, London `+0100` BST in July). The "day" is a LOCAL calendar day;
  matching on `validTimeLocal[:10]` (local date) is UTC-independent.
- **Day-alignment demonstrated (Asian tz, max UTC divergence):** at the probe hour, Manila (UTC+8)
  and Singapore both map local-tomorrow to array **index 1** (index 0 = local today) — the same
  index London resolves to — proving the lead-1 pick is correct where UTC-vs-local day divergence is
  maximal. An off-by-one-day mapping would poison every offset; it does not occur.
- **Geocode honored (not city-snapped):** the forecast is requested at the SETTLEMENT ANCHOR's
  coordinates (`fc_lat/fc_lon` of RPLL / WSSS / EGLC), NOT the city centroid, so the offset isolates
  forecast bias vs the oracle rather than smuggling in an urban-gradient location mismatch. The
  differing per-city local offsets in the response confirm the API honors the requested location.
- **Rounding:** values are display-rounded **integers** in both `units=e` (°F) and `units=m` (°C).
  Logged as-is; the estimator handles rounding statistically (never un-rounded by guessing).
- **Unit choice:** fetch `units=e` (whole-°F) and convert once at the edge, matching the WU record's
  own °F-native settlement grain, so TWC and the oracle are compared on the same basis (no silent
  grain mixing). Basket cities (Singapore/Manila/London) settle °C via whole-°F → °C round-half-up.

**Same-company caveat (recorded in code + reports):** TWC and Wunderground are both The Weather
Company properties; WU's displayed *forecasts* are TWC-powered. This pipeline's WU usage is
*observations* (the oracle), so no circularity exists — but any future temptation to treat TWC as an
independent information source must first pass the Phase 5 correlation audit. The quantity measured is
precisely **TWC published forecast − WU-settled station truth**, per city, per lead.

## Frozen design (the offset is earned PROSPECTIVELY — no backfill)
There is NO public archive of TWC's past forecasts (the endpoint serves only the current forecast), so
the no-backfill law applies exactly as it does to market prices and order books. Any tool claiming to
backfill "what TWC forecast last month" would be fabricating. The offset therefore starts at **n=0**
and says so; every delayed logging day is an offset-measurement day permanently lost.

- Instrument: `tools/twc_forecast_logger.py` (already live in `accumulate.py`), logging TWC's lead-1
  daily high/low each day via `storage.log_tracked_forecast(source='twc', …)` into `tracked_forecasts`,
  graded against the identical anchored WU oracle by the existing `settle_tracked_forecasts()`.
- Estimator: `weather_council/twc_offset.py` computes, per (place, attr ∈ {high, low}), the signed
  offset (TWC − actual) in the settlement grain.

## The gate (three mandatory conditions — same certification discipline as edge.py)
A `direction` label (ABOVE / BELOW) is asserted for a (city, attr) cell ONLY when ALL hold:
1. **n ≥ 20** settled paired days in that cell;
2. exact two-sided **binomial sign test p < 0.05** on above/below counts (ties at zero excluded and
   reported);
3. the seeded **bootstrap CI on the median offset excludes zero**.

Otherwise the cell reads `UNMEASURED(n=k)` — and a direction is REVOCABLE: a growing n that pulls the
CI back across zero drops the cell to NEUTRAL/UNMEASURED. Direction labels are live measurements, not
permanent facts. The measured offset adjusts ONLY the displayed TWC cross-reference line (raw AND
adjusted shown side by side with n + CI); it NEVER adjusts the council's numbers.

*Status at pre-registration: n=0 for every cell (accruing). The first MEASURED direction for any city
gets its own ledger entry with the full estimate verbatim — that entry is the deliverable: which way
TWC runs, city by city, with proof.*
