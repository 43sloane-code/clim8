"""KAT for issue-time provenance capture (weather_council/provenance.py + storage, Plan 3 Phase 0).

Provenance is the FOUNDATION of the learning loop: without the decisions stored at issue time,
error attribution (Phase 3) is retrodiction. This pins the contract:
  * build_provenance extracts per-source votes, the applied bias (pre = final − bias), and the
    regime, staying under the 8 KB budget;
  * validate_provenance is quarantine-grade — it flags a malformed / over-budget blob but the
    caller still stores it;
  * log_verdict writes provenance_json + provenance_ok additively, never failing the verdict,
    and a provenance-less row is countable as UNATTRIBUTABLE-PREPROVENANCE.
Network-free; isolated temp DB.

Run with:  PYTHONPATH=. python3 -m unittest tests.test_provenance -v
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from weather_council import storage, provenance, council


def _vote(mid, rh, ch):
    return types.SimpleNamespace(
        spec=types.SimpleNamespace(member_id=mid, model=mid.upper(), institution="Inst"),
        raw_high=rh, raw_low=rh - 6, corrected_high=ch, corrected_low=ch - 6,
        skill_high=0.8, skill_low=0.8, eligible=True)


def _fake_verdict(place="Testville, X", target="2026-07-12", high=31.3, low=25.1):
    return types.SimpleNamespace(
        place=types.SimpleNamespace(label=lambda place=place: place, latitude=1.0, longitude=2.0),
        target=target, high=high, low=low, high_spread=1.1, low_spread=0.6,
        confidence="HIGH", confidence_detail={"tier": "high"},
        votes=[_vote("ecmwf", 30.5, 31.4), _vote("gfs", 30.8, 31.2)],
        included_high=["ecmwf", "gfs"], included_low=["ecmwf", "gfs"],
        weights_high={"ecmwf": 0.6, "gfs": 0.4}, weights_low={"ecmwf": 0.5, "gfs": 0.5},
        naive_high=30.65, naive_low=24.65, truth_source={"kind": "station", "station": {"id": "X"}})


class TestProvenanceModule(unittest.TestCase):
    def test_module_selftest(self):
        provenance._selftest()                       # house-style parity: __main__ path stays green

    def test_validate_flags_missing_and_oversize(self):
        good = {"version": 1, "pipeline_version": "abc", "votes": [{"member_id": "e"}],
                "blend": {"high": 31.0, "bias_high": 0.8}, "regime": {}}
        self.assertEqual(provenance.validate_provenance(good), [])
        self.assertTrue(provenance.validate_provenance({}))               # missing keys
        nov = dict(good); nov["votes"] = []
        self.assertTrue(any("votes" in m for m in provenance.validate_provenance(nov)))
        big = dict(good); big["regime"] = {"x": "y" * (provenance.MAX_BYTES + 50)}
        self.assertTrue(any("budget" in m for m in provenance.validate_provenance(big)))


class TestLogVerdictProvenance(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patch = mock.patch.object(storage, "DB_PATH", Path(self._tmp.name))
        self._patch.start()
        # Mock the council helpers build_provenance calls, so we test the storage wiring in
        # isolation from the full deliberate() pipeline.
        self._m = [
            mock.patch.object(council, "applied_bias_correction",
                              lambda v, attr="high": 0.85 if attr == "high" else 0.5),
            mock.patch.object(council, "_classify_regime", lambda v: {"regime": "in-season"}),
            mock.patch.object(council, "regime_consensus", lambda v: {"status": "MATCHED"}),
        ]
        for p in self._m:
            p.start()

    def tearDown(self):
        for p in self._m:
            p.stop()
        self._patch.stop()
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_migration_adds_columns(self):
        storage._connect().close()
        conn = sqlite3.connect(self._tmp.name)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(verdicts)")}
        conn.close()
        self.assertIn("provenance_json", cols)
        self.assertIn("provenance_ok", cols)

    def test_log_verdict_writes_valid_provenance(self):
        storage.log_verdict(_fake_verdict())
        conn = sqlite3.connect(self._tmp.name)
        pj, ok = conn.execute(
            "SELECT provenance_json, provenance_ok FROM verdicts").fetchone()
        conn.close()
        self.assertEqual(ok, 1)                       # valid, not quarantined
        prov = json.loads(pj)
        self.assertEqual(len(prov["votes"]), 2)
        self.assertEqual(prov["votes"][0]["member_id"], "ecmwf")
        self.assertEqual(prov["votes"][0]["weight_high"], 0.6)
        self.assertEqual(prov["blend"]["bias_high"], 0.85)
        self.assertEqual(prov["blend"]["high_pre_bias"], round(31.3 - 0.85, 3))
        self.assertEqual(prov["regime"]["regime"], "in-season")
        self.assertLessEqual(len(pj.encode()), provenance.MAX_BYTES)

    def test_null_provenance_row_is_countable(self):
        # A pre-provenance row (NULL blob) must be distinguishable — UNATTRIBUTABLE-PREPROVENANCE.
        conn = storage._connect()
        with conn:
            conn.execute("INSERT INTO verdicts (issued_at, place, target_date, high, low, "
                         "confidence) VALUES ('2020-01-01T00:00:00','Old','2020-01-01',20,10,'x')")
        n_null = conn.execute(
            "SELECT COUNT(*) FROM verdicts WHERE provenance_json IS NULL").fetchone()[0]
        conn.close()
        self.assertEqual(n_null, 1)

    def test_provenance_failure_never_breaks_the_verdict(self):
        # If build_provenance blows up, the verdict must STILL be logged (NULL provenance).
        with mock.patch.object(council, "applied_bias_correction",
                               side_effect=RuntimeError("boom")):
            # build_provenance catches per-helper errors, but force a hard failure at json.dumps
            with mock.patch.object(provenance, "build_provenance", side_effect=RuntimeError("x")):
                storage.log_verdict(_fake_verdict(place="Robust, X"))
        conn = sqlite3.connect(self._tmp.name)
        row = conn.execute("SELECT high, provenance_json FROM verdicts WHERE place='Robust, X'").fetchone()
        conn.close()
        self.assertIsNotNone(row)                     # verdict logged despite provenance failure
        self.assertIsNone(row[1])                     # provenance NULL (best-effort)


if __name__ == "__main__":
    unittest.main()
