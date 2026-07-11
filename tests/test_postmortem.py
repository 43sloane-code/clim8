"""KAT for the post-mortem error-decomposition engine (weather_council/postmortem.py, Plan 3 Ph 3).

Pins the decomposition contract the lessons aggregator (Phase 4) rides on: INPUT/BLEND/BIAS
telescope exactly to final−actual (identity enforced — a non-closing row ABORTS, never stores a
wrong attribution); the taxonomy labels by strict precedence; a settled verdict WITHOUT provenance
is counted UNATTRIBUTABLE-PREPROVENANCE (never guessed); and a contract-vs-anchor divergence is
attributed SETTLEMENT. Network-free; isolated temp DB.

Run with:  PYTHONPATH=. python3 -m unittest tests.test_postmortem -v
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weather_council import storage, postmortem


def _prov(pre_bias, bias, raws):
    return {"pipeline_version": "t", "included_high": [f"m{i}" for i in range(len(raws))],
            "votes": [{"member_id": f"m{i}", "raw_high": r} for i, r in enumerate(raws)],
            "blend": {"high_pre_bias": pre_bias, "bias_high": bias}}


class TestBuild(unittest.TestCase):
    def test_module_selftest(self):
        postmortem._selftest()

    def test_identity_and_bias_attribution(self):
        # naive 30.5, weighted-raw 30.7, bias +0.8 -> final 31.5; actual 30.0 -> total +1.5
        pm = postmortem.build_postmortem(31.5, 30.0, _prov(30.7, 0.8, [30.0, 31.0]))
        c = pm["components"]
        self.assertAlmostEqual(c["input_error"] + c["blend_deviation"] + c["bias_contribution"],
                               pm["total_error"], places=6)
        self.assertEqual(pm["attributed_cause"], "BIAS")     # 0.8 > 0.5*1.5

    def test_identity_violation_raises(self):
        with self.assertRaises(postmortem.IdentityError):
            postmortem.build_postmortem(31.5, 30.0, _prov(99.0, 0.8, [30.0, 31.0]))


class TestRun(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patch = mock.patch.object(storage, "DB_PATH", Path(self._tmp.name))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        Path(self._tmp.name).unlink(missing_ok=True)

    def _verdict(self, place, target, high, actual, prov):
        conn = storage._connect()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO verdicts (issued_at, place, target_date, high, low, "
                "confidence, actual_high, provenance_json) "
                "VALUES ('2026-07-01T00:00:00', ?, ?, ?, 20.0, 'HIGH', ?, ?)",
                (place, target, high, actual, json.dumps(prov) if prov is not None else None))
        conn.close()

    def _pm_row(self, place, target):
        conn = sqlite3.connect(self._tmp.name)
        r = conn.execute("SELECT attributed_cause, total_error, crossed_boundary, "
                         "settlement_divergence FROM postmortems WHERE place=? AND target_date=?",
                         (place, target)).fetchone()
        conn.close()
        return r

    def test_run_scores_provenance_verdicts(self):
        self._verdict("Singapore, X", "2026-07-01", 31.5, 30.0, _prov(30.7, 0.8, [30.0, 31.0]))
        s = postmortem.run_postmortems()
        self.assertEqual(s["scored"], 1)
        self.assertEqual(s["by_cause"], {"BIAS": 1})
        self.assertEqual(self._pm_row("Singapore, X", "2026-07-01")[0], "BIAS")

    def test_preprovenance_counted_not_guessed(self):
        self._verdict("Old, X", "2020-01-01", 20.0, 19.0, None)     # settled, NO provenance
        s = postmortem.run_postmortems()
        self.assertEqual(s["scored"], 0)
        self.assertEqual(s["unattributable_preprovenance"], 1)
        self.assertIsNone(self._pm_row("Old, X", "2020-01-01"))     # no fabricated row

    def test_corrupt_provenance_aborts_no_row(self):
        self._verdict("Bad, X", "2026-07-01", 31.5, 30.0, _prov(99.0, 0.8, [30.0, 31.0]))
        s = postmortem.run_postmortems()
        self.assertEqual(s["aborted"], 1)
        self.assertEqual(s["scored"], 0)
        self.assertIsNone(self._pm_row("Bad, X", "2026-07-01"))

    def test_settlement_attribution_from_pm_resolved(self):
        # model bucket == actual bucket (31), but the contract paid bucket 30 -> SETTLEMENT
        self._verdict("Div, X", "2026-07-01", 31.2, 31.0, _prov(30.5, 0.7, [30.0, 31.0]))
        conn = storage._connect()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO market_snapshots (issued_at, place, target_date, grain, "
                "buckets_json, pm_resolved_label) VALUES ('2026-07-01T00:00:00','Div, X',"
                "'2026-07-01','C','[]','30°C')")
        conn.close()
        s = postmortem.run_postmortems()
        self.assertEqual(s["by_cause"].get("SETTLEMENT"), 1)
        row = self._pm_row("Div, X", "2026-07-01")
        self.assertEqual(row[0], "SETTLEMENT")
        self.assertEqual(row[3], 1.0)                               # actual 31 − contract 30

    def test_histogram(self):
        self._verdict("Singapore, X", "2026-07-01", 31.5, 30.0, _prov(30.7, 0.8, [30.0, 31.0]))
        postmortem.run_postmortems()
        self.assertEqual(postmortem.attribution_histogram(), {"BIAS": 1})


if __name__ == "__main__":
    unittest.main()
