"""KATs for the automation-spine watchdog fixes (2026-07-22).

Pins, per fix:
  T3  watchdog_core: malformed --ab-now / --truth-config JSON exits ABORT(4) per the
      documented contract — never an uncaught traceback;
  T4  accumulate._snapshotted_today: only "no such table" reads as first-run; a
      locked-DB OperationalError must RAISE, never return False (a False there is
      "no snapshot today" and the run writes the duplicate the guard exists to stop);
  T5  the canary drives the REAL duty2_regression / duty3_drift (not an inline
      re-implementation) and still trips RED on known-bad input;
  T7  Duty 2's ABSTAIN message names reports/crossover_baseline.json (the file it
      actually reads), not reports/baseline.json;
  T9  resolve_truth_sources covers London via the WU *settlement* wiring
      (storage._WU_SETTLE_TZ — London settles on WU but anchors backtests on IEM);
  T12 eval_harness liveness includes daily_healthcheck's own freshness line.

Network-free; deterministic. Run with:  PYTHONPATH=. python3 -m unittest tests.test_watchdog_spine -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import accumulate, watchdog_core as wc
from tools.eval_harness import brief
from tools.resolve_truth_sources import resolve


def _run_watchdog(argv):
    """watchdog_core.main() with Duty 1 patched out (it reads the live DB; these
    tests pin Duties 2-3 input handling only)."""
    with mock.patch.object(wc, "duty1_scorecard", lambda *a: None), \
            mock.patch.object(sys, "argv", ["watchdog_core.py", *argv]):
        return wc.main()


class TestT3MalformedJsonAborts(unittest.TestCase):
    def test_malformed_ab_now_exits_4_not_traceback(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "crossover_now.json"
            bad.write_text("{not json")
            rc = _run_watchdog(["--repo", td, "--ab-now", str(bad)])
        self.assertEqual(rc, 4)                      # ABORT, per the exit-code contract

    def test_malformed_truth_config_exits_4_not_traceback(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "truth.json"
            bad.write_text("[[unbalanced")
            rc = _run_watchdog(["--repo", td, "--truth-config", str(bad)])
        self.assertEqual(rc, 4)

    def test_well_formed_inputs_do_not_abort(self):
        with tempfile.TemporaryDirectory() as td:
            now = Path(td) / "crossover_now.json"
            now.write_text("{}")
            truth = Path(td) / "truth.json"
            truth.write_text("[]")
            # no baseline in td -> Duty 2 ABSTAINs; empty truth-config -> Duty 3 ABSTAINs
            rc = _run_watchdog(["--repo", td, "--ab-now", str(now),
                                "--truth-config", str(truth)])
        self.assertEqual(rc, 0)


class TestT4IdempotencyGuard(unittest.TestCase):
    def test_no_such_table_is_first_run_false(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            db.touch()                               # exists, but has no tables
            with mock.patch.object(accumulate, "DB", db):
                self.assertFalse(accumulate._snapshotted_today("Manila"))

    def test_locked_db_raises_not_false(self):
        con = mock.Mock()
        con.execute.side_effect = accumulate.sqlite3.OperationalError("database is locked")
        with mock.patch.object(accumulate.sqlite3, "connect", return_value=con):
            with self.assertRaises(accumulate.sqlite3.OperationalError):
                accumulate._snapshotted_today("Manila")


class TestT5CanaryUsesRealDuties(unittest.TestCase):
    def test_canary_invokes_real_duty_functions(self):
        with mock.patch.object(wc, "duty2_regression",
                               wraps=wc.duty2_regression) as m2, \
             mock.patch.object(wc, "duty3_drift",
                               wraps=wc.duty3_drift) as m3:
            rc = wc.run_canary()
        self.assertEqual(rc, 0)                      # real detector trips RED -> canary OK
        m2.assert_called_once()
        m3.assert_called_once()


class TestT7AbstainMessageFilename(unittest.TestCase):
    def test_schema_guard_names_crossover_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            reports = Path(td) / "reports"
            reports.mkdir()
            # the basket-MAE monitor baseline shape — NOT a {city:{hour:rate}} file
            (reports / "crossover_baseline.json").write_text(json.dumps(
                {"basket_mae_current": 0.9, "date": "2026-07-01", "variant": ["mean", 2]}))
            res = wc.Result()
            wc.duty2_regression(td, {}, res)
        self.assertEqual(res.lines[0]["verdict"], "ABSTAIN")
        self.assertIn("reports/crossover_baseline.json", res.lines[0]["msg"])
        self.assertNotIn("reports/baseline.json is not", res.lines[0]["msg"])


class TestT9LondonTruthCoverage(unittest.TestCase):
    def test_london_tracked_via_wu_settlement_wiring(self):
        rows = resolve()
        london = [r for r in rows if "london" in r[0].lower()]
        self.assertEqual(len(london), 1)
        self.assertIn("_WU_SETTLE_TZ", london[0][0])
        self.assertIn("wunderground", london[0][1].lower())   # else Duty 3 false-REDs


class TestT12HealthcheckLiveness(unittest.TestCase):
    def _state(self, liveness):
        return {"now_sgt": "2026-07-22T16:00", "pre_sunset": True,
                "lock": {"rows": 1, "settled": 1, "cov": {}, "status": {},
                         "today": None},
                "scorecard": {}, "twc": {"n": 0, "hits": 0},
                "pop": {"dry": 0, "convective": 0}, "dead": [],
                "liveness": liveness}

    def test_healthcheck_freshness_line_present(self):
        out = "\n".join(brief(self._state({"healthcheck": "2026-07-22T06:00"})))
        self.assertIn("healthcheck", out)
        self.assertIn("2026-07-22T06:00", out)

    def test_missing_healthcheck_heartbeat_flags_dormant(self):
        out = "\n".join(brief(self._state({"healthcheck": None})))
        self.assertIn("healthcheck", out)
        self.assertIn("DORMANT", out)


if __name__ == "__main__":
    unittest.main()
