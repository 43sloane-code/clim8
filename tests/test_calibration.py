"""Reproducible, network-free tests for the probabilistic-scoring and
calibration layer: scoring.py (CRPS / coverage / PIT), compare.residual_calibration
(now sharing one estimator with the council), compare_high's empirical bucket
probabilities, and Council._validate's leak-free CRPS wiring.

Stdlib unittest only — matching the project's no-dependency rule. Run with:
    PYTHONPATH=. python3 -m unittest discover -s tests
"""

from __future__ import annotations

import datetime as dt
import math
import random
import statistics as st
import unittest

from weather_council import scoring
from weather_council.scoring import crps_sample, crps_gaussian, interval_coverage, quantile, pit
from weather_council.compare import residual_calibration, compare_high, MIN_RESIDUALS
from weather_council.market import WeatherMarket, MarketBucket
from weather_council.agents import Vote, MemberSpec, Skill
from weather_council.council import Council


class TestScoring(unittest.TestCase):
    def test_module_self_test(self):
        # The module ships its own correctness oracle; make the suite re-run it.
        scoring._self_test()

    def test_point_forecast_is_absolute_error(self):
        self.assertEqual(crps_sample([7.3], 4.1), abs(7.3 - 4.1))

    def test_fast_equals_bruteforce(self):
        rng = random.Random(1)
        for _ in range(200):
            n = rng.randint(2, 30)
            s = [rng.gauss(0, 4) for _ in range(n)]
            y = rng.gauss(0, 4)
            mad = sum(abs(x - y) for x in s) / n
            brute = mad - 0.5 * sum(abs(a - b) for a in s for b in s) / (n * n)
            self.assertAlmostEqual(crps_sample(s, y, fair=False), brute, places=9)

    def test_energy_form_matches_gaussian(self):
        rng = random.Random(2)
        mu, sig = 10.0, 2.5
        samp = [rng.gauss(mu, sig) for _ in range(120_000)]
        for y in (5.0, 8.0, 10.0, 12.0, 15.0):
            self.assertLess(abs(crps_sample(samp, y) - crps_gaussian(mu, sig, y)), 0.03)

    def test_pit_uniform_for_calibrated_forecast(self):
        rng = random.Random(3)
        samp = [rng.gauss(0, 1) for _ in range(3000)]
        vals = [pit(samp, rng.gauss(0, 1)) for _ in range(3000)]
        self.assertAlmostEqual(st.mean(vals), 0.5, delta=0.03)

    def test_quantile_interpolates(self):
        self.assertAlmostEqual(quantile([0.0, 10.0], 0.5), 5.0)
        self.assertEqual(quantile([4.2], 0.9), 4.2)
        xs = [3.0, 1.0, 2.0, 5.0, 4.0]
        self.assertLessEqual(quantile(xs, 0.1), quantile(xs, 0.9))


class TestUnifiedCalibration(unittest.TestCase):
    """residual_calibration must now use the SAME estimator as Validation."""

    def test_coverage_matches_growing_prefix_reference(self):
        rng = random.Random(11)
        res = [rng.gauss(0.3, 2.0) for _ in range(60)]
        cal = residual_calibration(res)
        hits = cov_n = 0
        for i in range(MIN_RESIDUALS, len(res)):
            covered, _ = interval_coverage(res[:i], res[i])
            hits += 1 if covered else 0
            cov_n += 1
        self.assertEqual(cal.coverage_n, cov_n)
        self.assertAlmostEqual(cal.coverage_80, round(hits / cov_n, 2), places=9)

    def test_quantiles_use_linear_interp_convention(self):
        rng = random.Random(12)
        res = [rng.gauss(0, 1.5) for _ in range(40)]
        cal = residual_calibration(res)
        self.assertAlmostEqual(cal.p10, round(quantile(res, 0.10), 2), places=9)
        self.assertAlmostEqual(cal.p90, round(quantile(res, 0.90), 2), places=9)

    def test_below_floor_returns_none(self):
        self.assertIsNone(residual_calibration([0.1] * (MIN_RESIDUALS - 1)))


class TestBucketProbabilities(unittest.TestCase):
    def _ladder(self):
        # Contiguous whole-°C ladder 16..20 with open tails, summing prices ~1.
        buckets = (
            MarketBucket("16°C or below", 0.10, 0.90, (), None, 16),
            MarketBucket("17°C", 0.20, 0.80, (), 17, 17),
            MarketBucket("18°C", 0.35, 0.65, (), 18, 18),
            MarketBucket("19°C", 0.25, 0.75, (), 19, 19),
            MarketBucket("20°C or above", 0.10, 0.90, (), 20, None),
        )
        return WeatherMarket(
            event_id="t", title="Test City high", city="Test", date_label="d",
            station=None, grain="C", precision="whole °C", resolution_source=None,
            end_date=None, slug=None, buckets=buckets,
        )

    def test_probabilities_are_a_valid_distribution(self):
        rng = random.Random(7)
        residuals = [rng.gauss(0.0, 1.2) for _ in range(80)]
        cmp = compare_high(self._ladder(), verdict_high_c=18.3, residuals_c=residuals)
        self.assertIsNotNone(cmp)
        probs = [b.model_prob for b in cmp.buckets]
        for p in probs:
            self.assertGreaterEqual(p, 0.0)
            self.assertLessEqual(p, 1.0)
        # Contiguous ladder with open tails: every dressed draw lands somewhere.
        self.assertAlmostEqual(sum(probs), 1.0, places=9)
        self.assertIsNotNone(cmp.calibration)

    def test_declines_below_residual_floor(self):
        self.assertIsNone(
            compare_high(self._ladder(), 18.0, [0.1] * (MIN_RESIDUALS - 1)))


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


class TestConditionalSpreadCalibration(unittest.TestCase):
    """The recommend-only conditional-spread check must accept real
    heteroscedastic signal, reject homoscedastic noise, stay leak-free, and never
    claim to change the served verdict."""

    def test_module_self_test(self):
        from weather_council.calibration import _self_test
        _self_test()

    def test_recommends_when_error_tracks_dispersion(self):
        from weather_council.calibration import conditional_spread_eval
        rng = random.Random(3)
        pairs = []
        for _ in range(400):
            disp = rng.uniform(0.5, 4.0)
            pairs.append((rng.gauss(0.0, disp), disp))   # error scales with dispersion
        ev = conditional_spread_eval(pairs)
        self.assertIsNotNone(ev)
        self.assertTrue(ev.recommend)
        self.assertGreater(ev.improvement, 0)
        self.assertGreaterEqual(ev.z, 2.0)

    def test_declines_on_homoscedastic_noise(self):
        from weather_council.calibration import conditional_spread_eval
        rng = random.Random(4)
        pairs = [(rng.gauss(0.0, 1.5), rng.uniform(0.5, 4.0)) for _ in range(400)]
        ev = conditional_spread_eval(pairs)
        self.assertIsNotNone(ev)
        self.assertFalse(ev.recommend)

    def test_thin_sample_returns_none(self):
        from weather_council.calibration import conditional_spread_eval
        rng = random.Random(5)
        pairs = [(rng.gauss(0, 1), rng.uniform(1, 3)) for _ in range(15)]
        self.assertIsNone(conditional_spread_eval(pairs))

    def test_leak_free_first_warmup_days_unscored(self):
        # With exactly warmup+min_scored pairs, only those past the warmup can be
        # scored — proving each day uses strictly-earlier pairs, never the future.
        from weather_council.calibration import conditional_spread_eval, WARMUP
        rng = random.Random(6)
        n = WARMUP + 25
        pairs = [(rng.gauss(0, 2), rng.uniform(0.5, 4)) for _ in range(n)]
        ev = conditional_spread_eval(pairs, min_scored=1)
        self.assertEqual(ev.n_scored, n - WARMUP)


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


class TestRegimeConsensus(unittest.TestCase):
    """regime_consensus is a pure post-hoc summary of a finished Verdict: it
    classifies the regime from already-computed signals and measures whether the
    independent estimators reach a matched verdict. It must never depend on I/O
    and never imply a change to the headline number."""

    def _verdict(self, *, high=30.0, low=20.0, naive_high=None, naive_low=None,
                 mean_high=None, mean_low=None, blend_eligible=False,
                 backtest_days=0, eff=2.0, gap=None, repr_sigma=None, test_days=40):
        from types import SimpleNamespace as NS
        en = NS(mean_high=mean_high, mean_low=mean_low,
                blend_eligible=blend_eligible, backtest_days=backtest_days)
        return NS(high=high, low=low, naive_high=naive_high, naive_low=naive_low,
                  ensemble=en, validation=NS(test_days=test_days),
                  confidence_detail={"effective_uncertainty": eff,
                                     "season_gap_days": gap,
                                     "representativeness_sigma": repr_sigma})

    def test_matched_when_estimators_within_one_sigma(self):
        from weather_council.council import regime_consensus
        v = self._verdict(high=30.0, low=20.0, naive_high=30.5, naive_low=20.4,
                          mean_high=29.6, mean_low=19.8, eff=2.0)
        rc = regime_consensus(v)
        self.assertEqual(rc["consensus"]["status"], "matched")
        self.assertLessEqual(rc["consensus"]["worst_ratio"], 1.0)

    def test_split_when_an_estimator_diverges_beyond_threshold(self):
        from weather_council.council import regime_consensus
        v = self._verdict(high=30.0, low=20.0, naive_high=30.2, naive_low=20.1,
                          mean_high=34.0, mean_low=20.0, eff=2.0)  # 2.0σ on high
        rc = regime_consensus(v)
        self.assertEqual(rc["consensus"]["status"], "split")
        self.assertEqual(rc["consensus"]["worst_axis"], "high")

    def test_out_of_season_regime_and_trusted_validation(self):
        from weather_council.council import regime_consensus
        v = self._verdict(gap=39, naive_high=30.1, mean_high=30.0, naive_low=20.0, mean_low=20.0)
        rc = regime_consensus(v)
        self.assertEqual(rc["regime"]["season"], "out-of-season")
        self.assertTrue(any("trailing-window hit-rate" in t.lower()
                            for t in rc["trusted_validation"]))

    def test_benign_regime_reports_face_value(self):
        from weather_council.council import regime_consensus
        v = self._verdict(gap=0, eff=1.0, repr_sigma=0.2, blend_eligible=True,
                          backtest_days=30, naive_high=30.1, mean_high=30.0,
                          naive_low=20.0, mean_low=20.0)
        rc = regime_consensus(v)
        self.assertEqual(rc["regime"]["volatility"], "calm")
        self.assertEqual(rc["regime"]["spatial"], "flat")
        self.assertEqual(rc["regime"]["data"], "rich")
        self.assertTrue(any("face value" in t for t in rc["trusted_validation"]))

    def test_sigma_floor_used_when_effective_unavailable(self):
        from weather_council.council import regime_consensus
        v = self._verdict(eff=None, naive_high=30.5, mean_high=30.0,
                          naive_low=20.0, mean_low=20.0)
        rc = regime_consensus(v)
        self.assertFalse(rc["consensus"]["scaled_by_effective_sigma"])
        self.assertEqual(rc["consensus"]["sigma_used"], 1.0)


class TestHKORhrread(unittest.TestCase):
    """The live Hong Kong 'current observation' must come from the HKO instrument
    (rhrread), not an Open-Meteo grid cell that sits ~2 °C off the Observatory."""

    def _payload(self, temp_rows, hum_rows=None, rt="2026-06-07T10:00:00+08:00"):
        d = {"temperature": {"recordTime": rt, "data": temp_rows}}
        if hum_rows is not None:
            d["humidity"] = {"recordTime": rt, "data": hum_rows}
        return d

    def test_extracts_observatory_reading(self):
        from weather_council.sources import _parse_hko_rhrread
        out = _parse_hko_rhrread(self._payload(
            [{"place": "King's Park", "value": 30, "unit": "C"},
             {"place": "Hong Kong Observatory", "value": 28.8, "unit": "C"}],
            [{"place": "Hong Kong Observatory", "value": 74, "unit": "percent"}]))
        self.assertEqual(out["temperature_2m"], 28.8)
        self.assertEqual(out["relative_humidity_2m"], 74.0)
        self.assertEqual(out["record_time"], "2026-06-07T10:00:00+08:00")

    def test_humidity_optional(self):
        from weather_council.sources import _parse_hko_rhrread
        out = _parse_hko_rhrread(self._payload(
            [{"place": "Hong Kong Observatory", "value": 29, "unit": "C"}]))
        self.assertEqual(out["temperature_2m"], 29.0)
        self.assertIsNone(out["relative_humidity_2m"])

    def test_missing_observatory_yields_none(self):
        from weather_council.sources import _parse_hko_rhrread
        self.assertIsNone(_parse_hko_rhrread(self._payload(
            [{"place": "King's Park", "value": 30, "unit": "C"}])))

    def test_corrupt_value_yields_none(self):
        from weather_council.sources import _parse_hko_rhrread
        # Out-of-band temperature is dropped by the plausibility screen, so the
        # caller keeps the grid reading rather than ingesting a corrupt one.
        self.assertIsNone(_parse_hko_rhrread(self._payload(
            [{"place": "Hong Kong Observatory", "value": 999, "unit": "C"}])))
        self.assertIsNone(_parse_hko_rhrread({"not": "a feed"}))


class TestC7EdgeScoring(unittest.TestCase):
    """C7 realized-outcome scorer: strictly-proper scores and the edge gate."""

    def _snap(self, realized, council, market):
        """One settled snapshot. council/market are {label: prob} dicts; the
        bucket ladder is their shared keys."""
        labels = list(council.keys())
        return {
            "place": "Testville", "target_date": "2026-06-01",
            "realized_label": realized,
            "buckets": [{"label": b, "lo": None, "hi": None,
                         "model_prob": council[b], "market_prob": market[b]}
                        for b in labels],
        }

    def test_brier_and_logloss_values(self):
        from weather_council.edge import _brier, _logloss
        # Perfect forecast: Brier 0, log loss 0.
        probs = {"A": 1.0, "B": 0.0}
        self.assertAlmostEqual(_brier(probs, ["A", "B"], "A"), 0.0)
        self.assertAlmostEqual(_logloss(probs, "A"), 0.0)
        # Even forecast over two buckets: Brier 0.5, log loss ln 2.
        even = {"A": 0.5, "B": 0.5}
        self.assertAlmostEqual(_brier(even, ["A", "B"], "A"), 0.5)
        self.assertAlmostEqual(_logloss(even, "A"), math.log(2))

    def test_missing_bucket_prob_scored_as_zero(self):
        from weather_council.edge import _brier, _logloss, EPS
        probs = {"A": 0.7}                      # B unpriced
        # B realized: Brier counts 0.7^2 (A) + 1^2 (B as 0) = 1.49.
        self.assertAlmostEqual(_brier(probs, ["A", "B"], "B"), 0.7 ** 2 + 1.0)
        # Log loss on the unpriced realized bucket uses the EPS floor, not +inf.
        self.assertAlmostEqual(_logloss(probs, "B"), -math.log(EPS))

    def test_score_snapshot_maps_realized(self):
        from weather_council.edge import score_snapshot
        s = score_snapshot(self._snap("A", {"A": 0.8, "B": 0.2},
                                      {"A": 0.5, "B": 0.5}))
        self.assertEqual(s.realized_label, "A")
        self.assertAlmostEqual(s.council_p_realized, 0.8)
        self.assertAlmostEqual(s.market_p_realized, 0.5)
        self.assertLess(s.council_logloss, s.market_logloss)   # council sharper here

    def test_unsettled_or_offladder_snapshot_skipped(self):
        from weather_council.edge import score_snapshot
        self.assertIsNone(score_snapshot(
            {"realized_label": None, "buckets": [{"label": "A"}]}))
        self.assertIsNone(score_snapshot(
            {"realized_label": "Z",                 # outside the ladder
             "buckets": [{"label": "A", "model_prob": 1.0, "market_prob": 1.0}]}))

    def test_edge_unvalidated_below_min_settled(self):
        from weather_council.edge import score_snapshots, MIN_SETTLED
        # Council strictly better, but too few days to certify.
        snaps = [self._snap("A", {"A": 0.9, "B": 0.1}, {"A": 0.6, "B": 0.4})
                 for _ in range(MIN_SETTLED - 1)]
        r = score_snapshots(snaps)
        self.assertFalse(r.is_edge_validated)
        self.assertIn("not enough", r.note)
        self.assertEqual(r.n, MIN_SETTLED - 1)

    def test_edge_validated_when_council_dominates(self):
        from weather_council.edge import score_snapshots, MIN_SETTLED
        # Council always sharper toward the realized bucket than the market, on a
        # comfortable margin and enough days — the CI on the gain must clear zero.
        snaps = [self._snap("A", {"A": 0.85, "B": 0.15}, {"A": 0.55, "B": 0.45})
                 for _ in range(MIN_SETTLED)]
        r = score_snapshots(snaps)
        self.assertTrue(r.is_edge_validated)
        self.assertLess(r.council_logloss, r.market_logloss)
        self.assertLess(r.council_brier, r.market_brier)
        self.assertIsNotNone(r.logloss_diff_ci)
        self.assertGreater(r.logloss_diff_ci[0], 0)            # CI excludes zero
        self.assertIn("VALIDATED", r.note)

    def test_no_edge_when_market_wins(self):
        from weather_council.edge import score_snapshots, MIN_SETTLED
        # Market is the sharper forecaster — no edge regardless of n.
        snaps = [self._snap("A", {"A": 0.55, "B": 0.45}, {"A": 0.85, "B": 0.15})
                 for _ in range(MIN_SETTLED)]
        r = score_snapshots(snaps)
        self.assertFalse(r.is_edge_validated)
        self.assertIn("no edge", r.note)

    def test_empty_report_is_honest(self):
        from weather_council.edge import score_snapshots
        r = score_snapshots([])
        self.assertEqual(r.n, 0)
        self.assertFalse(r.is_edge_validated)
        self.assertIsNone(r.council_brier)

    def test_bootstrap_ci_is_seed_reproducible(self):
        from weather_council.edge import _bootstrap_ci, BOOTSTRAP_SEED
        diffs = [0.1, 0.2, -0.05, 0.3, 0.15, 0.0, 0.25, -0.1, 0.2, 0.05]
        a = _bootstrap_ci(diffs, 2000, BOOTSTRAP_SEED)
        b = _bootstrap_ci(diffs, 2000, BOOTSTRAP_SEED)
        self.assertEqual(a, b)                                  # deterministic
        self.assertLessEqual(a[0], a[1])


class TestC7Settlement(unittest.TestCase):
    """The snapshot ledger settles realized buckets against the verdict's anchor
    station (the record the market pays out on), not a face-value reading."""

    def test_bucket_for_reading_respects_open_tails(self):
        from weather_council.storage import _bucket_for_reading
        ladder = [{"label": "18 or below", "lo": None, "hi": 18},
                  {"label": "19", "lo": 19, "hi": 19},
                  {"label": "20", "lo": 20, "hi": 20},
                  {"label": "21 or above", "lo": 21, "hi": None}]
        self.assertEqual(_bucket_for_reading(ladder, 17), "18 or below")
        self.assertEqual(_bucket_for_reading(ladder, 19), "19")
        self.assertEqual(_bucket_for_reading(ladder, 25), "21 or above")

    def test_roundtrip_settles_against_anchor_station(self):
        import tempfile, types, os
        from pathlib import Path
        from weather_council import storage

        # Isolate the ledger in a temp DB so the real verdicts.db is untouched.
        tmp = Path(tempfile.mkdtemp()) / "c7.db"
        orig = storage.DB_PATH
        storage.DB_PATH = tmp
        try:
            place = types.SimpleNamespace(
                latitude=22.3, longitude=114.2,
                label=lambda: "Hong Kong, HK")
            verdict = types.SimpleNamespace(
                place=place, target="2026-06-01",
                truth_source={"kind": "station", "station": {"id": "HKO"}})
            bucket = types.SimpleNamespace
            comparison = types.SimpleNamespace(
                market_title="Highest temperature in Hong Kong",
                grain="C",
                buckets=[bucket(label="30 or below", lo=None, hi=30,
                                model_prob=0.3, market_prob=0.4),
                         bucket(label="31", lo=31, hi=31,
                                model_prob=0.5, market_prob=0.35),
                         bucket(label="32 or above", lo=32, hi=None,
                                model_prob=0.2, market_prob=0.25)])
            storage.log_market_snapshot(verdict, comparison)

            # Anchor station reports a 31.2 °C high -> native reading 31 -> "31".
            fake_sources = types.SimpleNamespace(
                fetch_station_daily=lambda st: {"2026-06-01": (31.2, 24.0)})
            settled = storage.settle_market_snapshots(fake_sources)
            self.assertEqual(len(settled), 1)

            snaps = storage.fetch_settled_snapshots()
            self.assertEqual(len(snaps), 1)
            self.assertEqual(snaps[0]["realized_label"], "31")
            self.assertEqual(snaps[0]["place"], "Hong Kong, HK")
            self.assertEqual(len(snaps[0]["buckets"]), 3)

            # The settled snapshot flows straight into the C7 scorer.
            from weather_council.edge import score_snapshot
            s = score_snapshot(snaps[0])
            self.assertEqual(s.realized_label, "31")
            self.assertAlmostEqual(s.council_p_realized, 0.5)
            self.assertAlmostEqual(s.market_p_realized, 0.35)
        finally:
            storage.DB_PATH = orig
            try:
                os.remove(tmp)
            except OSError:
                pass


class TestSubDegreeSettlementRendering(unittest.TestCase):
    """The MARKET COMPARISON 'settles' line must be GRAIN-aware. A sub-degree
    record (Hong Kong on the HKO Observatory, 0.1 °C) keeps the tenths — a 30.7 °C
    high settles as 30.7 °C, NOT a whole-degree '31'. Only whole-degree
    airport-METAR records snap to an integer. This locks in the fix for the user's
    correction that whole-degree rounding does not apply to Hong Kong."""

    def _offset(self, high_mean):
        from weather_council.station_offset import StationOffset
        return StationOffset(
            settlement_station_id="45007",
            settlement_station_name="Hong Kong Inter-National Airport",
            settlement_distance_km=6.2, backtest_station_id="45005",
            backtest_station_name="Royal Observatory",
            high_mean=high_mean, high_median=0.0, high_sd=0.5, n_season=583, n_all=900,
            season_window_days=21, overlap_start="2023-05-18", overlap_end="2026-05-18",
            is_modern=True)

    def _ladder(self, precision):
        buckets = (
            MarketBucket("29°C or below", 0.10, 0.90, (), None, 29),
            MarketBucket("30°C", 0.30, 0.70, (), 30, 30),
            MarketBucket("31°C", 0.40, 0.60, (), 31, 31),
            MarketBucket("32°C or above", 0.20, 0.80, (), 32, None),
        )
        return WeatherMarket(
            event_id="hk", title="Hong Kong high June 8", city="Hong Kong",
            date_label="June 8", station="Hong Kong Observatory", grain="C",
            precision=precision, resolution_source=None, end_date=None, slug=None,
            buckets=buckets)

    def test_sub_degree_record_keeps_tenths_no_whole_degree_rounding(self):
        import run
        rng = random.Random(11)
        residuals = [rng.gauss(0.0, 0.7) for _ in range(80)]
        cmp = compare_high(self._ladder("0.1°C"), verdict_high_c=30.7,
                           residuals_c=residuals, station_offset=self._offset(0.0))
        self.assertIsNotNone(cmp)
        self.assertTrue(cmp.settles_sub_degree)
        text = "\n".join(run._market_lines(cmp))
        settles = [ln for ln in run._market_lines(cmp) if "settles  :" in ln][0]
        # Keeps the tenths and says so explicitly; never snaps to a whole "31".
        self.assertIn("30.7 °C settles as 30.7 °C", settles)
        self.assertIn("no whole-degree rounding applies", settles)
        self.assertNotIn("settles as 31", text)
        self.assertNotIn("(ROUNDED)", text)
        # The whole-degree "integer label is fragile" note must not fire here.
        self.assertNotIn("integer label", text)

    def test_whole_degree_record_still_snaps_to_integer(self):
        import run
        rng = random.Random(13)
        residuals = [rng.gauss(0.0, 0.7) for _ in range(80)]
        cmp = compare_high(self._ladder("whole °C"), verdict_high_c=30.7,
                           residuals_c=residuals)
        self.assertIsNotNone(cmp)
        self.assertFalse(cmp.settles_sub_degree)
        settles = [ln for ln in run._market_lines(cmp) if "settles  :" in ln][0]
        # A whole-degree airport-METAR record DOES round half-up: 30.7 -> 31.
        self.assertIn("whole °C", settles)
        self.assertIn("rounds to 31", settles)
        self.assertIn("(ROUNDED)", settles)


class TestMechanismConvergence(unittest.TestCase):
    """The recommend-only convergence layer: independent mechanisms either cohere
    into one affirmed reading or they don't, and we report which — never moving
    the headline. Locks in the three guardrails (one-directional lineage
    de-correlation, significance gating, C7 gate) and the ABSTAIN/CONTESTED
    honesty."""

    def _m(self, name, lineage, est, mae, n=40):
        from weather_council.convergence import Mechanism
        return Mechanism(name=name, lineage=lineage, estimate_c=est, mae_c=mae, n=n)

    def test_scores_best_is_100_and_unusable_is_zero(self):
        from weather_council.convergence import score_mechanisms
        scores = score_mechanisms([
            self._m("council", "nwp", 20.0, 0.5),
            self._m("naive avg", "nwp", 20.2, 1.0),
            self._m("climatology", "clim", 21.0, 1.0, n=3),  # too few held-out days
        ])
        by = {s.name: s for s in scores}
        self.assertEqual(by["council"].score, 100.0)       # most precise = 100
        self.assertLess(by["naive avg"].score, 100.0)
        self.assertTrue(by["council"].usable)
        self.assertFalse(by["climatology"].usable)         # n<MIN_N → unused
        self.assertEqual(by["climatology"].score, 0.0)

    def test_affirmed_when_independents_cohere(self):
        from weather_council.convergence import converge
        c = converge("high", 20.0, [
            self._m("council", "nwp", 20.0, 0.5),
            self._m("naive avg", "nwp", 20.2, 0.6),
            self._m("climatology", "clim", 20.1, 1.0),
        ], residual_spread_c=1.0, n_resid=40)
        self.assertEqual(c.status, "AFFIRMED")
        self.assertEqual(c.independent_lineages, 2)
        self.assertGreaterEqual(c.affirmation, 50.0)
        self.assertFalse(c.significant)
        self.assertFalse(c.allowed_to_move)
        # Consensus sits on the headline; affirmed value ≈ headline.
        self.assertAlmostEqual(c.affirmed_c, 20.0, delta=0.15)

    def test_contested_when_independents_diverge_beyond_noise(self):
        from weather_council.convergence import converge
        c = converge("high", 20.0, [
            self._m("council", "nwp", 20.0, 0.5),
            self._m("climatology", "clim", 26.0, 1.0),
            self._m("persistence", "persist", 14.0, 1.0),
        ], residual_spread_c=1.0, n_resid=40)
        from weather_council.convergence import AFFIRM_MIN
        self.assertEqual(c.status, "CONTESTED")
        self.assertLess(c.affirmation, AFFIRM_MIN)
        self.assertIsNone(c.nudge_c)          # never fabricate a consensus nudge
        self.assertFalse(c.allowed_to_move)

    def test_abstains_with_fewer_than_two_independent_lineages(self):
        from weather_council.convergence import converge
        # Two mechanisms but SAME lineage → only one independent lineage.
        c = converge("high", 20.0, [
            self._m("council", "nwp", 20.0, 0.5),
            self._m("naive avg", "nwp", 20.1, 0.6),
        ], residual_spread_c=1.0, n_resid=40)
        self.assertEqual(c.status, "ABSTAIN")
        self.assertEqual(c.independent_lineages, 1)
        self.assertIsNone(c.affirmed_c)
        self.assertFalse(c.allowed_to_move)

    def test_lineage_de_correlation_counts_shared_lineage_once(self):
        from weather_council.convergence import converge
        # Two nwp members agree at 20; one climatology at 25. The shared lineage
        # must count ONCE (2 independent lineages, not 3), and it is represented by
        # its BEST member's estimate (council 20.0), never dragged toward a sibling.
        c = converge("high", 20.0, [
            self._m("council", "nwp", 20.0, 0.5),
            self._m("naive avg", "nwp", 20.0, 0.9),
            self._m("climatology", "clim", 25.0, 0.5),
        ], residual_spread_c=1.0, n_resid=40)
        self.assertEqual(c.independent_lineages, 2)
        self.assertEqual(len(c.lineages), 2)
        self.assertEqual(len(c.scores), 3)    # all shown, but lineage counted once
        nwp = [le for le in c.lineages if le.lineage == "nwp"][0]
        self.assertAlmostEqual(nwp.estimate_c, 20.0)   # best member, not an average
        self.assertAlmostEqual(nwp.eff_mae_c, 0.5)

    def test_significant_nudge_is_recommend_only_until_c7_validates(self):
        from weather_council.convergence import converge
        mechs = [
            self._m("council", "nwp", 20.0, 1.0),
            self._m("climatology", "clim", 21.0, 0.9),
            self._m("persistence", "persist", 21.2, 1.0),
        ]
        # A precise pair of independent lineages pulls the headline > floor.
        c = converge("high", 20.0, mechs, residual_spread_c=1.0, n_resid=40,
                     c7_validated=False)
        self.assertEqual(c.status, "AFFIRMED_NUDGE")
        self.assertTrue(c.significant)
        self.assertIsNotNone(c.nudge_c)
        self.assertGreater(c.nudge_c, 0.0)
        self.assertFalse(c.allowed_to_move)   # C7 NOT validated → annotation only
        # Same evidence, but once C7 has earned a validated edge it MAY move.
        c2 = converge("high", 20.0, mechs, residual_spread_c=1.0, n_resid=40,
                      c7_validated=True)
        self.assertEqual(c2.status, "AFFIRMED_NUDGE")
        self.assertTrue(c2.allowed_to_move)

    def test_tiny_nudge_is_not_significant(self):
        from weather_council.convergence import converge
        c = converge("high", 20.0, [
            self._m("council", "nwp", 20.0, 0.5),
            self._m("climatology", "clim", 20.1, 0.5),
        ], residual_spread_c=1.0, n_resid=40)
        # Independents agree; the sub-floor pull is not surfaced as a nudge.
        self.assertEqual(c.status, "AFFIRMED")
        self.assertFalse(c.significant)


if __name__ == "__main__":
    unittest.main()
