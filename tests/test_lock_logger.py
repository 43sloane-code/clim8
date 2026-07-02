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


if __name__ == "__main__":
    unittest.main()
