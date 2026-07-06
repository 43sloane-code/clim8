"""Network-free tests for the Council pipeline: leak-free CRPS validation, no-holdout diagnosis, degraded-run persistence, settlement-anchor resolution (cross-reference, pinned, strict HKO/EGLC), and the live EGLC METAR observation overlay.

Stdlib unittest only. Run with:
    PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import datetime as dt
import math
import random
import statistics as st
import unittest

from weather_council.agents import Vote, MemberSpec, Skill
from weather_council.council import (
    Council, _served_bias_halflife, RECENCY_HALFLIFE_DAYS)
from weather_council.recency_bias import recency_weighted_bias
from weather_council.sources import Place


class TestValidateCRPS(unittest.TestCase):
    def _synthetic(self, seed=7, N=140, noise_scale=1.0):
        rng = random.Random(seed)
        start = dt.date(2025, 1, 1)
        dates = [(start + dt.timedelta(days=i)).isoformat() for i in range(N)]
        observed = {}
        for i, d in enumerate(dates):
            h = 18.0 + 9.0 * math.sin(2 * math.pi * (i + 10) / 365.0) + rng.gauss(0, 2.2)
            l = 9.0 + 7.0 * math.sin(2 * math.pi * (i + 10) / 365.0) + rng.gauss(0, 1.8)
            observed[d] = (round(h, 1), round(min(l, h - 1), 1))
        votes = []
        for mid, bias, nz in (("ecmwf", 0.4, 1.4), ("gfs", -0.8, 2.0),
                              ("icon", 1.1, 1.7), ("gem", 0.0, 2.4)):
            hh, hl = {}, {}
            for d in dates:
                oh, ol = observed[d]
                hh[d] = (round(oh + bias + rng.gauss(0, nz * noise_scale), 1), oh)
                hl[d] = (round(ol + bias + rng.gauss(0, nz * noise_scale), 1), ol)
            spec = MemberSpec(mid, mid, mid, "")
            votes.append(Vote(spec, dates[-1], hh[dates[-1]][0], hl[dates[-1]][0],
                              hh[dates[-1]][0], hl[dates[-1]][0],
                              Skill(bias, nz, nz, N), Skill(bias, nz, nz, N),
                              True, hist_high=hh, hist_low=hl))
        return votes, observed

    def test_crps_fields_populated_and_sane(self):
        votes, observed = self._synthetic()
        val = Council.__new__(Council)._validate(votes, observed)
        self.assertIsNotNone(val.crps_council)
        self.assertIsNotNone(val.crps_climatology)
        self.assertGreater(val.crps_n, 0)
        self.assertTrue(0.0 <= val.coverage_80 <= 1.0)
        # On this well-behaved synthetic the council must beat climatology.
        self.assertGreater(val.crps_skill, 0.0)
        self.assertGreater(val.crps_council, 0.0)

    def test_determinism(self):
        v1, o1 = self._synthetic()
        v2, o2 = self._synthetic()
        a = Council.__new__(Council)._validate(v1, o1)
        b = Council.__new__(Council)._validate(v2, o2)
        self.assertEqual(a.crps_council, b.crps_council)
        self.assertEqual(a.coverage_80, b.coverage_80)

    def test_sharper_forecast_scores_lower_crps(self):
        # A lower-noise panel should earn a smaller (better) CRPS — the score
        # genuinely rewards sharpness, not just calibration.
        v_sharp, o_sharp = self._synthetic(noise_scale=0.4)
        v_dull, o_dull = self._synthetic(noise_scale=2.0)
        sharp = Council.__new__(Council)._validate(v_sharp, o_sharp)
        dull = Council.__new__(Council)._validate(v_dull, o_dull)
        self.assertLess(sharp.crps_council, dull.crps_council)

    def test_spread_skill_wired_and_detects_averaging(self):
        # _validate must attach the recommend-only spread–skill diagnostic on the
        # SAME leak-free pairs it builds for calibration. Two earned properties on
        # this fixture: (a) the global averaging factor 1/α exceeds 1 — the blend's
        # error is smaller than raw member dispersion (it must NOT be branded
        # over-dispersed for averaging); (b) with each member at a CONSTANT noise
        # scale there is no flow-dependence, so the honest read is FLAT.
        votes, observed = self._synthetic()
        val = Council.__new__(Council)._validate(votes, observed)
        ss = val.spread_skill
        self.assertIsNotNone(ss)
        self.assertGreater(ss.n, 0)
        self.assertTrue(ss.label)
        self.assertIsInstance(ss.tracks_error, bool)
        self.assertIsInstance(ss.reliable, bool)
        # Averaging detected (blend error < member spread): 1/α ≈ √(effective members).
        self.assertGreater(ss.avg_members_factor, 1.0)
        # Constant per-member noise ⇒ dispersion does not track error ⇒ FLAT.
        self.assertFalse(ss.tracks_error)
        self.assertTrue(ss.label.startswith("FLAT"))

    def test_spread_skill_is_deterministic(self):
        a = Council.__new__(Council)._validate(*self._synthetic()).spread_skill
        b = Council.__new__(Council)._validate(*self._synthetic()).spread_skill
        self.assertEqual((a.label, a.n, a.alpha, a.consistency),
                         (b.label, b.n, b.alpha, b.consistency))

    def test_rank_histogram_and_pit_wired(self):
        # _validate must also attach the two recommend-only ensemble-calibration
        # companions built from the SAME leak-free walk-forward: the rank histogram
        # (raw panel dispersion) over the per-day member panels, and the PIT
        # histogram (served residual cloud) over the leak-free PIT values. Both must
        # populate, carry a real sample, and read a known verdict word.
        votes, observed = self._synthetic()
        val = Council.__new__(Council)._validate(votes, observed)
        rh, pc = val.rank_histogram, val.pit_calibration
        self.assertIsNotNone(rh)
        self.assertIsNotNone(pc)
        self.assertGreater(rh.n, 0)
        self.assertGreater(pc.n, 0)
        # Verdict words come from the fixed diagnostic vocabulary, never invented.
        self.assertIn(rh.verdict, {"CALIBRATED", "UNDER-DISPERSED", "OVER-DISPERSED",
                                   "BIASED COLD", "BIASED WARM"})
        self.assertIn(pc.verdict, {"CALIBRATED", "OVER-CONFIDENT", "UNDER-CONFIDENT",
                                   "BIASED COLD", "BIASED WARM"})
        # The PIT sample is the leak-free CRPS-gated count: one per scored day.
        self.assertEqual(pc.n, val.crps_n)

    def test_rank_histogram_and_pit_deterministic(self):
        # The randomized rank uses a SEEDED rng, so two independent validations of
        # the same fixture must return byte-identical histograms and verdicts.
        a = Council.__new__(Council)._validate(*self._synthetic())
        b = Council.__new__(Council)._validate(*self._synthetic())
        self.assertEqual((a.rank_histogram.verdict, a.rank_histogram.diag.bins,
                          a.rank_histogram.diag.z),
                         (b.rank_histogram.verdict, b.rank_histogram.diag.bins,
                          b.rank_histogram.diag.z))
        self.assertEqual((a.pit_calibration.verdict, a.pit_calibration.diag.bins),
                         (b.pit_calibration.verdict, b.pit_calibration.diag.bins))

class TestNoHoldoutDiagnosis(unittest.TestCase):
    """The healthcheck must explain WHY a city scored n=0, distinguishing a
    transient fetch failure from a genuine archive-overlap gap from a defect."""

    def _vote(self, hist_high, hist_low, notes=()):
        spec = MemberSpec("m", "m", "m", "")
        return Vote(spec, "2025-06-01", None, None, None, None, None, None,
                    True, notes=list(notes), hist_high=hist_high, hist_low=hist_low)

    def test_throttled_fetch_is_named_as_transient(self):
        from tools.daily_healthcheck import _diagnose_no_holdout
        votes = [self._vote({}, {}, notes=["history unavailable: rate-limited"])]
        observed = {f"2025-01-{d:02d}": (5.0, 1.0) for d in range(1, 20)}
        reason = _diagnose_no_holdout(votes, observed)
        self.assertIn("transient", reason)

    def test_thin_overlap_is_named_as_archive_gap(self):
        from tools.daily_healthcheck import _diagnose_no_holdout, WARMUP
        observed = {f"2025-01-{d:02d}": (5.0, 1.0) for d in range(1, 25)}
        # Member has only 3 paired days — below the held-out floor.
        few = {f"2025-01-{d:02d}": (4.0, 5.0) for d in range(1, 4)}
        reason = _diagnose_no_holdout([self._vote(few, few)], observed)
        self.assertIn("overlap", reason)
        self.assertLess(3, WARMUP + 5)

    def test_no_history_no_notes(self):
        from tools.daily_healthcheck import _diagnose_no_holdout
        observed = {f"2025-01-{d:02d}": (5.0, 1.0) for d in range(1, 20)}
        reason = _diagnose_no_holdout([self._vote({}, {})], observed)
        self.assertIn("no paired forecast history", reason)

class TestDegradedRunPersistence(unittest.TestCase):
    """A network-outage run (no usable city) must never erase the operator's
    last known-good signal — latest.txt and the baseline are preserved."""

    def _dirs(self):
        import tempfile
        from pathlib import Path
        d = Path(tempfile.mkdtemp())
        return d, d / "baseline.json"

    def test_degraded_run_preserves_latest_and_baseline(self):
        from tools.daily_healthcheck import _persist
        reports, base = self._dirs()
        (reports / "latest.txt").write_text("GOOD RUN\n")
        base.write_text('{"basket_mae_current": 0.79, "date": "2026-06-01"}')
        today = dt.date(2026, 6, 6)
        persisted = _persist("ALL THROTTLED", today, usable_cities=0,
                             cur_mae=None, cur=("mean", 2), baseline_absent=False,
                             reports_dir=reports, baseline_path=base)
        self.assertFalse(persisted)
        # The good signal is untouched...
        self.assertEqual((reports / "latest.txt").read_text(), "GOOD RUN\n")
        self.assertIn("0.79", base.read_text())
        # ...but the outage is still recorded in the dated audit file.
        self.assertTrue((reports / "healthcheck_2026-06-06.txt").exists())

    def test_degraded_run_does_not_overwrite_good_same_day_report(self):
        from tools.daily_healthcheck import _persist
        reports, base = self._dirs()
        today = dt.date(2026, 6, 6)
        dated = reports / f"healthcheck_{today.isoformat()}.txt"
        dated.write_text("GOOD EARLIER RUN\n")
        _persist("THROTTLED LATER", today, usable_cities=0, cur_mae=None,
                 cur=("mean", 2), baseline_absent=False,
                 reports_dir=reports, baseline_path=base)
        self.assertEqual(dated.read_text(), "GOOD EARLIER RUN\n")

    def test_good_run_writes_latest_and_first_baseline(self):
        from tools.daily_healthcheck import _persist
        reports, base = self._dirs()
        today = dt.date(2026, 6, 6)
        persisted = _persist("HEALTHY", today, usable_cities=8, cur_mae=0.80,
                             cur=("mean", 2), baseline_absent=True,
                             reports_dir=reports, baseline_path=base)
        self.assertTrue(persisted)
        self.assertEqual((reports / "latest.txt").read_text(), "HEALTHY\n")
        self.assertIn("0.8", base.read_text())

    def test_good_run_never_moves_existing_baseline(self):
        from tools.daily_healthcheck import _persist
        reports, base = self._dirs()
        base.write_text('{"basket_mae_current": 0.79, "date": "2026-06-01"}')
        _persist("HEALTHY", dt.date(2026, 6, 6), usable_cities=8, cur_mae=0.85,
                 cur=("mean", 2), baseline_absent=False,
                 reports_dir=reports, baseline_path=base)
        # Baseline stays at the original 0.79 so drift remains measurable.
        self.assertIn("0.79", base.read_text())

class TestAnchorCrossReference(unittest.TestCase):
    """A Hong Kong verdict anchors on the Observatory and shows the airport only as
    a measured cross-reference. The cross-reference is never fabricated: when it
    can't be earned it returns an {error} dict, not a number or a silent vanish."""

    def _place(self, name):
        from types import SimpleNamespace
        return SimpleNamespace(name=name)

    def _verdict(self, truth_source, high=30.7, low=27.6):
        from types import SimpleNamespace
        return SimpleNamespace(truth_source=truth_source, high=high, low=low)

    def _station(self, sid, name):
        from types import SimpleNamespace
        return SimpleNamespace(id=sid, name=name)

    def _offset(self, high_mean):
        from weather_council.station_offset import StationOffset
        return StationOffset(
            settlement_station_id="45007",
            settlement_station_name="Hong Kong Inter-National Airport",
            settlement_distance_km=6.2, backtest_station_id="45005",
            backtest_station_name="Royal Observatory",
            high_mean=high_mean, high_median=0.0, high_sd=0.95, n_season=583, n_all=900,
            season_window_days=21, overlap_start="2023-05-18", overlap_end="2026-05-18",
            is_modern=True)

    def _sources_with_airport(self):
        from types import SimpleNamespace
        air = self._station("45007", "Hong Kong Inter-National Airport")
        hko = self._station("45005", "Royal Observatory")
        return SimpleNamespace(nearest_stations=lambda place: [hko, air])

    def test_unpinned_city_returns_none(self):
        import run
        out = run._anchor_cross_reference(
            None, self._place("Tokyo"), dt.date(2026, 6, 8),
            self._verdict({"kind": "station", "station": {"id": "47662",
                                                          "name": "Tokyo"}}))
        self.assertIsNone(out)

    def test_non_station_truth_source_is_error(self):
        import run
        out = run._anchor_cross_reference(
            None, self._place("Hong Kong"), dt.date(2026, 6, 8),
            self._verdict({"kind": "reanalysis"}))
        self.assertIn("error", out)
        self.assertNotIn("high_mean", out)

    def test_unearnable_cross_reference_is_error_not_number(self):
        import run
        orig = run.measure_settlement_offset
        run.measure_settlement_offset = lambda *a, **k: None
        try:
            out = run._anchor_cross_reference(
                self._sources_with_airport(), self._place("Hong Kong"),
                dt.date(2026, 6, 8),
                self._verdict({"kind": "station",
                               "station": {"id": "45005", "name": "Royal Observatory"},
                               "data_source": "hko_opendata"}))
        finally:
            run.measure_settlement_offset = orig
        self.assertIn("error", out)
        self.assertNotIn("high_mean", out)

    def test_airport_measured_as_cross_reference_to_anchor(self):
        import run
        orig = run.measure_settlement_offset
        run.measure_settlement_offset = lambda *a, **k: self._offset(-0.08)
        try:
            out = run._anchor_cross_reference(
                self._sources_with_airport(), self._place("Hong Kong"),
                dt.date(2026, 6, 8),
                self._verdict({"kind": "station",
                               "station": {"id": "45005", "name": "Royal Observatory"},
                               "data_source": "hko_opendata"}, high=30.7))
        finally:
            run.measure_settlement_offset = orig
        self.assertNotIn("error", out)
        # The verdict high is NOT shifted — the anchor IS the settlement station now.
        self.assertEqual(out["verdict_high"], 30.7)
        self.assertEqual(out["anchor_station"], "Royal Observatory")
        self.assertEqual(out["cross_ref_station"], "Hong Kong Inter-National Airport")
        self.assertAlmostEqual(out["high_mean"], -0.08, places=2)
        self.assertEqual(out["data_source"], "hko_opendata")

class TestPinnedAnchor(unittest.TestCase):
    """The user-pinned anchor directive picks a specific settlement station by
    ICAO (London -> London City Airport, EGLC) instead of the nearest reporting
    one, and reorders the candidate list so that station is tried first."""

    def _place(self, name):
        from types import SimpleNamespace as NS
        return NS(name=name)

    def test_london_pins_to_eglc(self):
        from weather_council.council import _pinned_anchor_icao
        self.assertEqual(_pinned_anchor_icao(self._place("London")), "EGLC")
        self.assertEqual(_pinned_anchor_icao(self._place("London, GB")), "EGLC")

    def test_unpinned_city_returns_none(self):
        from weather_council.council import _pinned_anchor_icao
        self.assertIsNone(_pinned_anchor_icao(self._place("Paris")))

    def test_reorder_puts_pinned_station_first(self):
        # Mirror the stable-sort used in _resolve_truth: the EGLC station must
        # jump ahead of the nearer non-pinned ones, others keep distance order.
        from types import SimpleNamespace as NS
        cands = [NS(icao="EGRB", distance_km=1.1), NS(icao="EGLC", distance_km=16.8),
                 NS(icao="EGLL", distance_km=22.6)]
        pinned = "EGLC"
        ordered = sorted(cands, key=lambda s: (s.icao or "").upper() != pinned)
        self.assertEqual(ordered[0].icao, "EGLC")
        self.assertEqual([s.icao for s in ordered[1:]], ["EGRB", "EGLL"])

class TestStrictHKOAnchor(unittest.TestCase):
    """Hong Kong is pinned to the Royal Observatory as a STRICT anchor: it is
    tried first, and no other physical station (notably the VHHH airport ~6 km
    away, which reads ~1 °C different) may ever substitute for it. If the
    Observatory's modern feed is transiently down, the verdict falls back to the
    ERA5 grid — it must NOT silently re-anchor on the airport, which is what made
    the logged HK verdict jump between 30 and 31 across runs."""

    def _places(self):
        from weather_council.sources import Place
        return Place("Hong Kong", "HK", 22.278, 114.175, "Asia/Hong_Kong")

    def _stations(self):
        from weather_council.sources import Station
        # Airport listed FIRST on purpose, to prove the reorder brings the
        # Observatory to the front even when it is not the nearest in the list.
        air = Station(id="45007", name="Hong Kong Inter-National Airport", wmo=None,
                      icao="VHHH", latitude=22.31, longitude=113.92,
                      elevation=None, distance_km=6.2)
        hko = Station(id="45005", name="Royal Observatory", wmo=None, icao=None,
                      latitude=22.302, longitude=114.174, elevation=None,
                      distance_km=2.5)
        return air, hko

    def _recent_series(self, base):
        """~80 fresh daily (high, low) days ending yesterday, enough to clear the
        freshness and MIN_SAMPLES gates."""
        out = {}
        today = dt.date.today()
        for k in range(1, 81):
            d = (today - dt.timedelta(days=k)).isoformat()
            out[d] = (base, base - 4.0)
        return out

    def _council(self, hko_series_for_45005):
        from weather_council.sources import Sources
        from weather_council.council import Council
        s = Sources()
        air, hko = self._stations()
        s.nearest_stations = lambda place: [air, hko]
        s.is_hko_observatory = lambda st: st.id == "45005"
        air_series = self._recent_series(33.0)        # airport noticeably warmer
        s.fetch_station_daily = (
            lambda st: dict(hko_series_for_45005) if st.id == "45005"
            else dict(air_series))
        s.fetch_archive_series = lambda place, ws, we: self._recent_series(30.0)
        return Council(s)

    def test_wants_hko_anchor_only_for_hong_kong(self):
        from weather_council.council import _wants_hko_anchor
        from types import SimpleNamespace as NS
        self.assertTrue(_wants_hko_anchor(NS(name="Hong Kong")))
        self.assertTrue(_wants_hko_anchor(NS(name="Hong Kong, HK")))
        self.assertFalse(_wants_hko_anchor(NS(name="Tokyo")))

    def test_anchors_on_observatory_even_when_airport_listed_first(self):
        c = self._council(self._recent_series(31.0))
        target = dt.date.today() + dt.timedelta(days=1)
        _fp, _obs, _ws, _we, truth = c._resolve_truth(self._places(), target, 60)
        self.assertEqual(truth["kind"], "station")
        self.assertEqual(truth["station"]["id"], "45005")
        self.assertEqual(truth["data_source"], "hko_opendata")

    def test_observatory_feed_down_falls_to_era5_never_airport(self):
        # Observatory daily comes back empty (modern feed hiccup). Strict anchor
        # must skip the airport entirely and drop to the ERA5 grid.
        c = self._council({})
        target = dt.date.today() + dt.timedelta(days=1)
        _fp, _obs, _ws, _we, truth = c._resolve_truth(self._places(), target, 60)
        self.assertEqual(truth["kind"], "era5_grid")
        self.assertIsNone(truth["station"])
        # And emphatically NOT the airport.
        self.assertNotEqual((truth.get("station") or {}).get("id"), "45007")

class TestStrictEGLCAnchor(unittest.TestCase):
    """London is pinned to London City Airport (EGLC) as a STRICT anchor — the
    same rule as the Hong Kong Observatory. It is tried first, and no other
    physical station (e.g. the nearer "London / Abbey Wood", which reads
    differently) may substitute. If EGLC's feed is down, the verdict falls back to
    the ERA5 grid — it must NOT silently re-anchor on Abbey Wood, which is what
    made a London resolve flip between two stations across runs."""

    def _place(self):
        from weather_council.sources import Place
        return Place("London, United Kingdom", "GB", 51.505, -0.055, "Europe/London")

    def _stations(self):
        from weather_council.sources import Station
        # Abbey Wood listed FIRST and NEARER on purpose, to prove the strict skip
        # keeps it out even though the reorder/distance would otherwise pick it.
        abbey = Station(id="EGLC0", name="London / Abbey Wood", wmo=None,
                        icao=None, latitude=51.487, longitude=0.114,
                        elevation=None, distance_km=11.4)
        eglc = Station(id="03779", name="London City Airport", wmo=None,
                       icao="EGLC", latitude=51.505, longitude=0.055,
                       elevation=None, distance_km=16.8)
        return abbey, eglc

    def _recent_series(self, base):
        out = {}
        today = dt.date.today()
        for k in range(1, 81):
            d = (today - dt.timedelta(days=k)).isoformat()
            out[d] = (base, base - 4.0)
        return out

    def _council(self, eglc_series):
        from weather_council.sources import Sources
        from weather_council.council import Council
        s = Sources()
        abbey, eglc = self._stations()
        s.nearest_stations = lambda place: [abbey, eglc]
        s.is_hko_observatory = lambda st: False
        abbey_series = self._recent_series(18.0)      # Abbey Wood noticeably different
        s.fetch_station_daily = (
            lambda st: dict(eglc_series) if st.icao == "EGLC"
            else dict(abbey_series))
        s.fetch_archive_series = lambda place, ws, we: self._recent_series(20.0)
        return Council(s)

    def test_strict_anchor_icao_only_for_london(self):
        from weather_council.council import _strict_anchor_icao
        from types import SimpleNamespace as NS
        self.assertEqual(_strict_anchor_icao(NS(name="London")), "EGLC")
        self.assertEqual(_strict_anchor_icao(NS(name="London, GB")), "EGLC")
        self.assertIsNone(_strict_anchor_icao(NS(name="Paris")))

    def test_anchors_on_eglc_even_when_abbey_listed_first(self):
        c = self._council(self._recent_series(21.0))
        target = dt.date.today() + dt.timedelta(days=1)
        _fp, _obs, _ws, _we, truth = c._resolve_truth(self._place(), target, 60)
        self.assertEqual(truth["kind"], "station")
        self.assertEqual(truth["station"]["icao"], "EGLC")

    def test_eglc_feed_down_falls_to_era5_never_substitute_station(self):
        # EGLC daily comes back empty (feed hiccup). Strict anchor must skip Abbey
        # Wood entirely and drop to the ERA5 grid — never a different station.
        c = self._council({})
        target = dt.date.today() + dt.timedelta(days=1)
        _fp, _obs, _ws, _we, truth = c._resolve_truth(self._place(), target, 60)
        self.assertEqual(truth["kind"], "era5_grid")
        self.assertIsNone(truth["station"])

class TestLondonEGLCMetarOverlay(unittest.TestCase):
    """London City Airport's Meteostat 'EGLC0' file is the Abbey Wood gauge ~17 km
    away and weeks/months stale. fetch_station_daily must overlay the modern IEM
    ASOS METAR record (the same settlement-grade sensor run.py references and the
    market resolves on) on top of the stale Meteostat base — recent METAR days
    winning, older days kept — and _resolve_truth must label the provenance
    honestly as iem_metar. This is the EGLC analogue of the HKO open-data overlay."""

    def _station(self, icao):
        from weather_council.sources import Station
        return Station(id="EGLC0", name="London / Abbey Wood", wmo=None,
                       icao=icao, latitude=51.487, longitude=0.114,
                       elevation=None, distance_km=16.8)

    def test_is_london_eglc_matches_by_icao_only(self):
        from weather_council.sources import Sources
        s = Sources()
        self.assertTrue(s.is_london_eglc(self._station("EGLC")))
        self.assertTrue(s.is_london_eglc(self._station("eglc")))   # case-insensitive
        self.assertFalse(s.is_london_eglc(self._station("EGLL")))  # Heathrow, not us
        self.assertFalse(s.is_london_eglc(self._station(None)))

    def test_overlay_replaces_recent_days_keeps_old(self):
        from types import SimpleNamespace
        from weather_council.sources import Sources
        s = Sources()
        old_day, recent_day = "2024-01-01", "2026-05-01"
        # Real Meteostat bulk CSV: date,tavg,tmin,tmax. Recent day reads a
        # deliberately wrong (but plausible) 30/20 so we can prove the METAR
        # overlay wins; an implausible value would be screened out instead.
        csv = f"{old_day},3,1,5\n{recent_day},25,20,30\n"
        s.http = SimpleNamespace(get_gzip_text=lambda url: csv)
        s.is_hko_observatory = lambda st: False
        # Fresh METAR for the recent day only; older day stays Meteostat. Stubs the
        # generic IEM-overlay method (fires for every _IEM_OVERLAY_TZ icao: EGLC, KSFO).
        s.iem_overlay_truth_series = lambda icao, timezone, target, back_years=2: {
            recent_day: (14.0, 8.0)}
        out = s.fetch_station_daily(self._station("EGLC"))   # real method, real overlay
        self.assertEqual(out[old_day], (5.0, 1.0))     # old Meteostat day untouched
        self.assertEqual(out[recent_day], (14.0, 8.0)) # recent day = METAR, not 99
        # KSFO (San Francisco) anchors on its live Wunderground oracle, NOT an IEM overlay —
        # so fetch_station_daily does NOT overlay it; the wrong 30/20 survives here.
        out_sf = s.fetch_station_daily(self._station("KSFO"))
        self.assertEqual(out_sf[recent_day], (30.0, 20.0))
        # A non-overlay station gets no overlay — the wrong 30/20 survives.
        out2 = s.fetch_station_daily(self._station("EGLL"))
        self.assertEqual(out2[recent_day], (30.0, 20.0))

    def test_truth_source_labels_iem_metar(self):
        from weather_council.sources import Sources, Place
        from weather_council.council import Council
        eglc = self._station("EGLC")
        s = Sources()
        s.nearest_stations = lambda place: [eglc]
        s.is_hko_observatory = lambda st: False
        recent = {}
        today = dt.date.today()
        for k in range(1, 81):
            recent[(today - dt.timedelta(days=k)).isoformat()] = (14.0, 8.0)
        s.fetch_station_daily = lambda st: dict(recent)
        place = Place("London, United Kingdom", "GB", 51.505, -0.055, "Europe/London")
        c = Council(s)
        target = dt.date.today() + dt.timedelta(days=1)
        _fp, _obs, _ws, _we, truth = c._resolve_truth(place, target, 60)
        self.assertEqual(truth["kind"], "station")
        self.assertEqual(truth["data_source"], "iem_metar")
        self.assertEqual((truth["station"] or {}).get("icao"), "EGLC")


class TestServedRecencyBias(unittest.TestCase):
    """The recency-weighted bias is now SERVED (not just recommended) at the
    stations the leak-free gate cleared — today Hong Kong. These tests pin the
    per-station policy, the live re-correction, and validation coherence."""

    def _hk(self):
        return Place("Hong Kong", "HK", 22.30, 114.17, "Asia/Hong_Kong")

    def _ldn(self):
        return Place("London, United Kingdom", "GB", 51.505, -0.055, "Europe/London")

    def test_policy_on_for_hk_off_elsewhere(self):
        self.assertEqual(_served_bias_halflife(self._hk()), RECENCY_HALFLIFE_DAYS)
        self.assertIsNone(_served_bias_halflife(self._ldn()))
        self.assertIsNone(_served_bias_halflife(None))

    def _drifting_votes(self, target):
        # One member whose forecast bias DRIFTS warm over the window, so the
        # recency-weighted bias (leans on recent, larger errors) differs clearly
        # from the plain trailing mean. observed is constant; forecast = obs + bias.
        start = target - dt.timedelta(days=60)
        hh, hl = {}, {}
        for i in range(60):
            d = (start + dt.timedelta(days=i)).isoformat()
            bias = 0.5 + 0.05 * i          # 0.5 °C warming to ~3.5 °C
            hh[d] = (round(20.0 + bias, 2), 20.0)
            hl[d] = (round(10.0 + bias, 2), 10.0)
        spec = MemberSpec("ecmwf", "ecmwf", "ecmwf", "")
        # Live raw forecast for the target day.
        raw_h, raw_l = 23.5, 13.5
        v = Vote(spec, target.isoformat(), raw_h, raw_l, raw_h, raw_l,
                 Skill(0.0, 1.0, 1.0, 60), Skill(0.0, 1.0, 1.0, 60),
                 True, hist_high=hh, hist_low=hl)
        return [v]

    def test_apply_recency_bias_recorrects_hk_member(self):
        target = dt.date(2025, 7, 1)
        votes = self._drifting_votes(target)
        v = votes[0]
        dated_h = [(d, f - o) for d, (f, o) in v.hist_high.items()]
        plain_bias = st.mean(e for _, e in dated_h)
        rec_bias, _ = recency_weighted_bias(dated_h, target.isoformat(),
                                            RECENCY_HALFLIFE_DAYS)
        c = Council.__new__(Council)
        c._apply_recency_bias(votes, self._hk(), target, {"season_gap_days": 0})
        # Member skill/bias is now the recency-weighted bias (precise oracle), and
        # corrected = raw - that bias. Recency > plain on a warming drift, so the
        # recency-corrected number is the cooler (more-corrected) one.
        self.assertAlmostEqual(v.skill_high.bias, rec_bias, places=9)
        self.assertAlmostEqual(v.corrected_high, v.raw_high - rec_bias, places=9)
        self.assertGreater(rec_bias, plain_bias)
        self.assertLess(v.corrected_high, v.raw_high - plain_bias)
        self.assertTrue(any("recency-weighted bias" in n for n in v.notes))

    def test_apply_recency_bias_noop_for_non_hk(self):
        target = dt.date(2025, 7, 1)
        votes = self._drifting_votes(target)
        before = votes[0].corrected_high
        Council.__new__(Council)._apply_recency_bias(
            votes, self._ldn(), target, {"season_gap_days": 0})
        self.assertEqual(votes[0].corrected_high, before)

    def test_apply_recency_bias_noop_out_of_season(self):
        target = dt.date(2025, 7, 1)
        votes = self._drifting_votes(target)
        before = votes[0].corrected_high
        Council.__new__(Council)._apply_recency_bias(
            votes, self._hk(), target, {"season_gap_days": 120})  # out of season
        self.assertEqual(votes[0].corrected_high, before)

    def test_validate_served_flag_and_coherence(self):
        # The same votes/observed, validated plain vs served-recency. The served
        # run must flag bias_halflife_served and (on a drifting panel) move the
        # headline council MAE — proving the headline measures the SERVED method.
        rng = random.Random(3)
        start = dt.date(2025, 1, 1)
        N = 140
        dates = [(start + dt.timedelta(days=i)).isoformat() for i in range(N)]
        observed = {d: (20.0 + rng.gauss(0, 1.5), 10.0 + rng.gauss(0, 1.5))
                    for d in dates}
        hh, hl = {}, {}
        for i, d in enumerate(dates):
            drift = 0.03 * i                 # member runs progressively warm
            oh, ol = observed[d]
            hh[d] = (round(oh + drift + rng.gauss(0, 0.4), 2), oh)
            hl[d] = (round(ol + drift + rng.gauss(0, 0.4), 2), ol)
        spec = MemberSpec("ecmwf", "ecmwf", "ecmwf", "")
        votes = [Vote(spec, dates[-1], hh[dates[-1]][0], hl[dates[-1]][0],
                      hh[dates[-1]][0], hl[dates[-1]][0],
                      Skill(0.0, 1.0, 1.0, N), Skill(0.0, 1.0, 1.0, N),
                      True, hist_high=hh, hist_low=hl)]
        plain = Council.__new__(Council)._validate(votes, observed)
        served = Council.__new__(Council)._validate(votes, observed, self._hk())
        self.assertIsNone(plain.bias_halflife_served)
        self.assertEqual(served.bias_halflife_served, RECENCY_HALFLIFE_DAYS)
        # Drift => recency tracks it => served headline MAE is lower (and different).
        self.assertNotAlmostEqual(plain.council_mae_high, served.council_mae_high,
                                  places=6)
        self.assertLess(served.council_mae_high, plain.council_mae_high)


if __name__ == "__main__":
    unittest.main()
