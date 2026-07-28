"""KAT for tools/mc_verdict_sim.py — the Monte Carlo + 10y backtest validator for
the SF CLI-seam guard (2026-07-27: obs modal served at 78-98% while the CLI paid
the bucket above via the 18-00Z catch). Pins the predicate known-answers, the
dataset hygiene rules (truncation screen, |catch|>5°F truth quarantine), and the
scorer wiring so the harness can't silently rot.

unittest.TestCase (pytest-style bare functions run ZERO tests under the repo gate)."""
import unittest

from tools.mc_verdict_sim import (backtest, build_dataset, driver_stats,
                                  guard_fires, parity_fires, _score,
                                  _top_boundary, SERVED_HOURS)


def _day(d, rm, om, cl):
    return {"date": d, "obs_max_f": om, "cli_f": cl, "catch_f": cl - om,
            "rm": {h: rm for h in SERVED_HOURS}}


class TestPredicate(unittest.TestCase):
    def test_module_self_test(self):
        from tools import mc_verdict_sim
        self.assertEqual(mc_verdict_sim._selftest(), 0)

    def test_distance_rule_known_answers(self):
        self.assertTrue(guard_fires(69.1, 15, 1.27))     # 0.4 < seam
        self.assertFalse(guard_fires(68.0, 15, 1.27))    # 1.5 > seam
        self.assertTrue(guard_fires(68.9, 14, 1.27))     # even modal, high in bucket
        self.assertFalse(guard_fires(69.1, 17, 1.27))    # post-00Z group: quiet

    def test_boundary_math(self):
        self.assertEqual(_top_boundary(69.1), 69.5)
        self.assertEqual(_top_boundary(70.2), 71.5)

    def test_parity_baseline_preserved_for_comparison(self):
        self.assertTrue(parity_fires(69, 15))
        self.assertFalse(parity_fires(68, 15))
        self.assertFalse(parity_fires(69, 17))


class TestDatasetHygiene(unittest.TestCase):
    def test_truncated_days_excluded(self):
        obs = {"2026-01-01": [(h, 15.0) for h in range(0, 20)]}   # ends 19:00
        days, quar = build_dataset(obs, {"2026-01-01": 70.0})
        self.assertEqual((days, quar), ([], []))

    def test_truth_artifacts_quarantined(self):
        full = [(h, 20.0) for h in range(0, 24)]
        obs = {"2026-01-01": full, "2026-01-02": full}
        cli = {"2026-01-01": 79.0, "2026-01-02": 70.0}   # 79 vs obs 68 -> +11 artifact
        days, quar = build_dataset(obs, cli)
        self.assertEqual([d["date"] for d in days], ["2026-01-02"])
        self.assertEqual([q["date"] for q in quar], ["2026-01-01"])

    def test_out_of_band_obs_screened(self):
        obs = {"2026-01-01": [(h, 999.0 if h == 12 else 20.0) for h in range(24)]}
        days, _ = build_dataset(obs, {"2026-01-01": 68.0})
        self.assertEqual(len(days), 1)
        self.assertLess(days[0]["obs_max_f"], 100.0)    # the 999°C row is gone


class TestScorers(unittest.TestCase):
    def test_score_math(self):
        recs = [{"date": "a", "above_pays": True, "w": True},
                {"date": "b", "above_pays": True, "w": False},
                {"date": "c", "above_pays": False, "w": True},
                {"date": "d", "above_pays": False, "w": False}]
        s = _score(recs, "w")
        self.assertEqual((s["recall"], s["precision"]), (0.5, 0.5))
        self.assertEqual(s["warn_rate"], 0.5)

    def test_driver_halves_sign_stable(self):
        days = [_day(f"2026-01-{i:02d}", 60.0, 68.0, 69.0) for i in range(1, 11)]
        dr = driver_stats(days)
        self.assertEqual(dr["all"]["mean"], 1.0)
        self.assertEqual(dr["bucket_cross_rate"], 0.0)   # 68/69 both in 68-69
        self.assertGreater(dr["h1"]["mean"], 0)
        self.assertGreater(dr["h2"]["mean"], 0)

    def test_backtest_leak_free_wiring(self):
        days = [_day("2026-01-01", 60.0, 62.0, 62.0),
                _day("2026-01-02", 69.1, 69.1, 70.0)]
        bt = backtest(days)
        self.assertEqual(bt["n_cells"], 2 * len(SERVED_HOURS))
        self.assertIn("by_band", bt)


if __name__ == "__main__":
    unittest.main()
