"""Tests for the lock certification ledger (tools/lock_logger).

Pins the pure pieces: the report-seed parser (locked + diffuse + rejects), settle/hit filling,
the °F→settlement-bucket math, and the FROZEN certification bar (CERTIFIED within −10pp of the
mean stated conviction at n≥20; OVERCONFIDENT downgrade below it; ACCRUING under the n floor).
Network fetch, live lever call, and disk I/O are not exercised."""
import unittest

from tools.lock_logger import (parse_report, settle_rows, coverage, certify,
                               _bucket_f, N_FLOOR, TOL, _selftest)


class TestLockLogger(unittest.TestCase):
    def test_parse_report_locked_and_diffuse(self):
        locked = ("    running max by 18:00 local: 27.8°C (Changi)\n"
                  "    remaining-rise learned from 160 strictly-earlier days\n"
                  "    => HIGH-CONVICTION call: 28°C at 100% (vs day-ahead)")
        r = parse_report(locked, "verdict-singapore-2026-06-30-1809sgt.txt")
        self.assertEqual((r["hour"], r["modal_bucket"], r["modal_prob"], r["n_rise"]),
                         (18, 28, 1.0, 160))
        diffuse = ("    running max by 04:00 local: 28.9°C (x)\n"
                   "    => still diffuse (34°C at 26%) — too early")
        r2 = parse_report(diffuse, "verdict-singapore-2026-07-02-0449sgt.txt")
        self.assertEqual((r2["hour"], r2["modal_bucket"]), (4, 34))
        self.assertIsNone(parse_report("prose only", "verdict-singapore-2026-07-02-0449sgt.txt"))
        self.assertIsNone(parse_report(locked, "unrelated.txt"))

    def test_settle_and_bucket_math(self):
        rows = [{"target_date": "2026-06-30", "hour": 18, "kind": "sharpened",
                 "modal_bucket": 28, "modal_prob": 1.0}]
        self.assertEqual(settle_rows(rows, {"2026-06-30": 28}), 1)
        self.assertTrue(rows[0]["hit"])
        self.assertEqual(_bucket_f(90), 32)     # 90°F = 32.2°C -> 32
        self.assertEqual(_bucket_f(82), 28)     # 82°F = 27.8°C -> 28

    def test_frozen_certification_bar(self):
        self.assertEqual((N_FLOOR, TOL), (20, 0.10))     # changing these = documented breakpoint
        rows = [{"target_date": f"d{i}", "hour": 15, "kind": "sharpened", "modal_bucket": 30,
                 "modal_prob": 0.95, "hit": i < 18} for i in range(20)]      # 90% vs stated 95%
        self.assertEqual(certify(coverage(rows, hours=(15,)))[15], "CERTIFIED")
        for r in rows[:3]:
            r["hit"] = False                                                  # 75% -> downgrade
        self.assertTrue(certify(coverage(rows, hours=(15,)))[15].startswith("OVERCONFIDENT"))
        self.assertEqual(certify(coverage(rows[:5], hours=(15,)))[15], "ACCRUING")

    def test_selftest_passes(self):
        self.assertEqual(_selftest(), 0)


class TestPerCityLedger(unittest.TestCase):
    """2026-07-12 execution of london_lock_instrumentation.md §1: per-city rows with the
    Singapore migration default; city-scoped settle; per-city coverage so London rows can
    never pollute Singapore's FROZEN certification table (and vice versa); London settles
    on the WU EGLC record (the 2026-07-07 'wunderground only' directive supersedes the
    prereg's IEM line)."""

    def test_cities_config_and_frozen_singapore_bars(self):
        from tools.lock_logger import CITIES, CERT_HOURS
        self.assertEqual(CITIES["Singapore"]["cert_hours"], (12, 13, 14, 15, 16, 18))
        self.assertEqual(CERT_HOURS, CITIES["Singapore"]["cert_hours"])   # frozen, unchanged
        self.assertEqual(CITIES["London"]["icao"], "EGLC")                # WU settle, not IEM
        self.assertIsNone(CITIES["London"]["seed_glob"])                  # live rows only

    def test_migration_default_is_singapore(self):
        from tools.lock_logger import _key
        legacy = {"target_date": "2026-06-30", "hour": 15}
        self.assertEqual(_key(legacy)[0], "Singapore")
        self.assertNotEqual(_key(legacy), _key({"city": "London", **legacy}))

    def test_settle_is_city_scoped(self):
        rows = [{"target_date": "2026-06-30", "hour": 18, "kind": "sharpened",
                 "modal_bucket": 28, "modal_prob": 1.0},                  # legacy Singapore
                {"city": "London", "target_date": "2026-06-30", "hour": 15,
                 "kind": "sharpened", "modal_bucket": 18, "modal_prob": 0.9}]
        self.assertEqual(settle_rows(rows, {"2026-06-30": 28}), 1)        # Singapore only
        self.assertIsNone(rows[1].get("settled_bucket"))
        self.assertEqual(settle_rows(rows, {"2026-06-30": 17}, city="London"), 1)
        self.assertFalse(rows[1]["hit"])                                  # 18 called, 17 settled

    def test_coverage_isolated_per_city(self):
        rows = ([{"target_date": f"s{i}", "hour": 15, "kind": "sharpened", "modal_bucket": 30,
                  "modal_prob": 0.95, "hit": True} for i in range(20)] +
                [{"city": "London", "target_date": f"l{i}", "hour": 15, "kind": "sharpened",
                  "modal_bucket": 18, "modal_prob": 0.95, "hit": False} for i in range(20)])
        self.assertEqual(coverage(rows, hours=(15,))[15]["hit_rate"], 1.0)   # Singapore clean
        self.assertEqual(coverage(rows, hours=(15,), city="London")[15]["hit_rate"], 0.0)

    def test_london_bucket_math_round_half_up(self):
        self.assertEqual(_bucket_f(64), 18)     # 64°F = 17.78°C -> 18
        self.assertEqual(_bucket_f(63), 17)     # 63°F = 17.22°C -> 17


if __name__ == "__main__":
    unittest.main()
