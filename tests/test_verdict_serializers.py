"""Shape KATs for the verdict_to_dict per-eval serializers (run.py).

The self_improvement_check / spread_skill / rank_histogram / pit_calibration /
coverage_calibration blocks were inline dicts, extracted verbatim into helpers
(behavior-preserving dedup — the JSON contract is served output). These pin the
exact dict shape AND key order (json.dumps preserves insertion order), plus the
None pass-through, so any drift in the extraction trips here.

Run with:  PYTHONPATH=. python3 -m unittest tests.test_verdict_serializers -v
"""
from __future__ import annotations

import types
import unittest

import run


class TestDiagJson(unittest.TestCase):
    """rank_histogram and pit_calibration share ONE serializer, identical modulo
    the object — that identity is the point of the extraction."""

    def _ev(self):
        diag = types.SimpleNamespace(shape="bell", edge_ratio=1.5, reduced_chi2=0.9,
                                     z=2.0, uniform=False, bins=(1, 2, 3))
        return types.SimpleNamespace(verdict="CALIBRATED", diag=diag, n=42)

    def test_shape_and_key_order(self):
        d = run._diag_json(self._ev())
        self.assertEqual(list(d.keys()),
                         ["verdict", "shape", "edge_ratio", "reduced_chi2", "z",
                          "uniform", "bins", "n", "applied"])
        self.assertEqual(d, {"verdict": "CALIBRATED", "shape": "bell",
                             "edge_ratio": 1.5, "reduced_chi2": 0.9, "z": 2.0,
                             "uniform": False, "bins": [1, 2, 3], "n": 42,
                             "applied": False})

    def test_none_passes_through(self):
        self.assertIsNone(run._diag_json(None))


class TestSelfImprovementJson(unittest.TestCase):
    def test_shape_and_key_order(self):
        ev = types.SimpleNamespace(recommend=True, crps_conditional=1.1,
                                   crps_incumbent=1.2, improvement_pct=0.08, z=2.5,
                                   disp_corr=0.3, n_scored=50)
        d = run._self_improvement_json(ev)
        self.assertEqual(list(d.keys()),
                         ["method", "recommend", "crps_conditional", "crps_incumbent",
                          "improvement_pct", "sigma_past_noise",
                          "dispersion_error_corr", "scored_days", "applied"])
        self.assertEqual(d["sigma_past_noise"], 2.5)
        self.assertEqual(d["dispersion_error_corr"], 0.3)
        self.assertEqual(d["scored_days"], 50)
        self.assertFalse(d["applied"])
        self.assertIn("heteroscedastic distribution", d["method"])

    def test_none_passes_through(self):
        self.assertIsNone(run._self_improvement_json(None))


class TestSpreadSkillJson(unittest.TestCase):
    def test_shape_and_key_order(self):
        ev = types.SimpleNamespace(label="RELIABLE", reliable=True, tracks_error=True,
                                   consistency=0.4, reliability_gap=0.05,
                                   avg_members_factor=2.2, rmse=1.9, mean_spread=3.1,
                                   n=60)
        d = run._spread_skill_json(ev)
        self.assertEqual(list(d.keys()),
                         ["label", "reliable", "tracks_error", "consistency",
                          "reliability_gap", "averaging_factor", "rmse",
                          "mean_spread", "n", "applied"])
        self.assertEqual(d["averaging_factor"], 2.2)
        self.assertFalse(d["applied"])

    def test_none_passes_through(self):
        self.assertIsNone(run._spread_skill_json(None))


class TestCoverageCalibrationJson(unittest.TestCase):
    def test_shape_and_key_order(self):
        ev = types.SimpleNamespace(recommend=False, final_factor=1.1,
                                   coverage_incumbent=0.78, coverage_calibrated=0.81,
                                   target=0.80, under_sigma=1.0, crps_calibrated=1.0,
                                   crps_incumbent=1.05, improvement_pct=0.04, z=0.9,
                                   n_scored=45)
        d = run._coverage_calibration_json(ev)
        self.assertEqual(list(d.keys()),
                         ["method", "recommend", "candidate_factor",
                          "coverage_incumbent", "coverage_calibrated", "target",
                          "under_sigma", "crps_calibrated", "crps_incumbent",
                          "improvement_pct", "sigma_past_noise", "scored_days",
                          "applied"])
        self.assertEqual(d["candidate_factor"], 1.1)   # final_factor -> candidate_factor
        self.assertEqual(d["sigma_past_noise"], 0.9)
        self.assertIn("split conformal", d["method"])

    def test_none_passes_through(self):
        self.assertIsNone(run._coverage_calibration_json(None))


if __name__ == "__main__":
    unittest.main()
