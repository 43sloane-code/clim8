"""KAT for the TWC (Weather Channel) cross-reference bias note (run._twc_bias / _cross_check_lines).

TWC is a FORECAST cross-reference ONLY — WU remains the settlement oracle, IEM the observation
cross-ref, and TWC is NEVER blended into the served verdict. The cross-check annotates the TWC
signal with its DATA-DERIVED directional bias vs the WU settlement (mean TWC forecast − settled
high over the logged pairs), so the reader knows which way TWC leans. This pins:
  * _twc_bias returns None below the 8-pair floor (too thin to state a direction),
  * with enough pairs it returns the signed mean + n (negative == TWC runs BELOW WU),
  * the cross-check note shows the raw TWC value, the BELOW/ABOVE direction, the WU-scale read,
    and states TWC is never blended.
Network-free; isolated temp DB.

Run with:  PYTHONPATH=. python3 -m unittest tests.test_twc_crossref -v
"""
from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import run
from weather_council import storage


class TestTwcCrossref(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patch = mock.patch.object(storage, "DB_PATH", Path(self._tmp.name))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        Path(self._tmp.name).unlink(missing_ok=True)

    def _twc(self, place, target, fc, actual=None):
        conn = storage._connect()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO tracked_forecasts "
                "(source, issued_at, place, target_date, fc_high, fc_low, actual_high) "
                "VALUES ('twc', '2026-06-01T00:00:00', ?, ?, ?, ?, ?)",
                (place, target, fc, fc - 6.0, actual))
        conn.close()

    def test_bias_thin_returns_none(self):
        for i in range(5):                       # only 5 settled pairs (< 8 floor)
            self._twc("X", f"2026-06-0{i+1}", 30.0, 30.7)
        self.assertIsNone(run._twc_bias())

    def test_bias_below_signed_mean(self):
        for i in range(10):                      # TWC 0.8°C below the settled high
            self._twc("X", f"2026-06-{i+10}", 30.0, 30.8)
        m, n = run._twc_bias()
        self.assertEqual(n, 10)
        self.assertAlmostEqual(m, -0.8, places=2)   # negative => BELOW

    def test_bias_above_signed_mean(self):
        for i in range(10):                      # TWC 0.5°C above
            self._twc("X", f"2026-06-{i+10}", 30.5, 30.0)
        m, n = run._twc_bias()
        self.assertAlmostEqual(m, +0.5, places=2)   # positive => ABOVE

    def _crosscheck(self, place, target, fc):
        # settled below-bias history + an unsettled forecast for the target
        for i in range(10):
            self._twc(place, f"2026-06-{i+10}", 30.0, 30.8)
        self._twc(place, target, fc, None)
        v = types.SimpleNamespace(
            place=types.SimpleNamespace(label=lambda place=place: place),
            target=target, high=31.0)
        with mock.patch.object(run, "live_bucket_scorecard", lambda *a, **k: {"recent": []}):
            return "\n".join(run._cross_check_lines(v, {"bucket": 31}, None))

    def test_crossref_note_below(self):
        out = self._crosscheck("Singapore, Singapore", "2026-07-11", 30.6)
        self.assertIn("TWC (Weather Channel) forecast 30.6°C", out)
        self.assertIn("CROSS-REFERENCE only", out)
        self.assertIn("BELOW the WU settlement", out)
        self.assertIn("never blended", out)          # WU stays the oracle
        self.assertIn("soft floor", out)             # below => floor
        self.assertNotIn("ABOVE", out)

    def test_crossref_note_accruing_when_thin(self):
        # a target forecast but < 8 settled pairs -> "accruing" note, no direction claimed
        self._twc("Karachi, Pakistan", "2026-07-12", 34.0, None)
        for i in range(3):
            self._twc("Karachi, Pakistan", f"2026-06-0{i+1}", 33.0, 33.5)
        v = types.SimpleNamespace(
            place=types.SimpleNamespace(label=lambda: "Karachi, Pakistan"),
            target="2026-07-12", high=34.0)
        with mock.patch.object(run, "live_bucket_scorecard", lambda *a, **k: {"recent": []}):
            out = "\n".join(run._cross_check_lines(v, {"bucket": 34}, None))
        self.assertIn("bias vs WU is still accruing", out)
        self.assertNotIn("BELOW the WU settlement", out)


if __name__ == "__main__":
    unittest.main()
