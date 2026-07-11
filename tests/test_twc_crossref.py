"""KAT for the TWC signed-offset cross-reference block (run._twc_cross_reference[_lines], Plan 4 Ph4).

TWC is a CROSS-REFERENCE ONLY — WU remains the settlement oracle, TWC is NEVER blended into the
served verdict, and this block touches NO council number (pure additivity). Pins:
  * no TWC row for the day  → block ABSENT (None / []);
  * n < 20                  → measured offset UNMEASURED(n), NO offset-adjusted line (raw is never
                              adjusted off an uncertified/absent direction);
  * certified ABOVE (n≥20 + sign-p<0.05 + CI excludes 0) → offset-adjusted = raw − median shown
    beside the council's own value, with a divergence line;
  * divergence carries the AMBER flag only when |adjusted − council| exceeds 2× the council's OWN
    recent MAE (a measured yardstick, not a magic constant);
  * the raw forecast is ALWAYS shown beside the adjusted one (an adjusted number without its raw
    parent is how a display correction becomes fake data).
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


def _v(place, target, high):
    return types.SimpleNamespace(
        place=types.SimpleNamespace(label=lambda place=place: place),
        target=target, high=high)


class TestTwcCrossref(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._patch = mock.patch.object(storage, "DB_PATH", Path(self._tmp.name))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        Path(self._tmp.name).unlink(missing_ok=True)

    def _row(self, place, target, fc, actual=None, council=None):
        conn = storage._connect()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO tracked_forecasts "
                "(source, issued_at, place, target_date, fc_high, fc_low, council_high, "
                " actual_high) VALUES ('twc','2026-06-01T00:00:00',?,?,?,?,?,?)",
                (place, target, fc, fc - 6.0, council, actual))
        conn.close()

    def _settled_above(self, place, offset=1.0, n=22, council_err=0.3):
        """n settled days where TWC runs `offset`° ABOVE actual; council off by `council_err`."""
        for i in range(n):
            actual = 30.0 + (i % 3) * 0.1
            self._row(place, f"2026-05-{i+1:02d}", actual + offset, actual, actual + council_err)

    # ── block absent when no forecast captured ───────────────────────────────────────────────────
    def test_absent_when_no_twc_row(self):
        v = _v("Nowhere", "2026-07-12", 30.0)
        self.assertIsNone(run._twc_cross_reference(v))
        self.assertEqual(run._twc_cross_reference_lines(v), [])

    # ── UNMEASURED: raw shown, no adjustment ─────────────────────────────────────────────────────
    def test_unmeasured_shows_raw_but_no_adjustment(self):
        self._row("Singapore, Singapore", "2026-07-12", 30.6, None)   # a raw forecast, 0 settled
        v = _v("Singapore, Singapore", "2026-07-12", 30.2)
        ref = run._twc_cross_reference(v)
        self.assertEqual(ref["direction"], "UNMEASURED")
        self.assertIsNone(ref["adjusted"])
        out = "\n".join(run._twc_cross_reference_lines(v))
        self.assertIn("raw forecast    : 30.6°C", out)
        self.assertIn("UNMEASURED", out)
        self.assertIn("no certified direction", out)
        self.assertNotIn("offset-adjusted", out)

    # ── certified ABOVE: adjusted beside council + divergence ─────────────────────────────────────
    def test_certified_above_applies_adjustment(self):
        self._settled_above("Singapore, Singapore", offset=1.0, n=22, council_err=0.3)
        self._row("Singapore, Singapore", "2026-07-12", 31.0, None)   # today's raw forecast
        v = _v("Singapore, Singapore", "2026-07-12", 30.2)
        ref = run._twc_cross_reference(v)
        self.assertEqual(ref["direction"], "ABOVE")
        self.assertAlmostEqual(ref["adjusted"], 31.0 - ref["median_offset"], places=3)
        self.assertAlmostEqual(ref["council_high"], 30.2)
        out = "\n".join(run._twc_cross_reference_lines(v))
        self.assertIn("raw forecast    : 31.0°C", out)          # raw always beside adjusted
        self.assertIn("offset-adjusted", out)
        self.assertIn("council 30.2°C", out)
        self.assertIn("consistent", out)                         # small divergence -> not amber

    def test_divergence_amber_when_beyond_2x_council_mae(self):
        # council MAE ~0.3 -> threshold ~0.6; a raw forecast far from council trips AMBER.
        self._settled_above("Singapore, Singapore", offset=1.0, n=22, council_err=0.3)
        self._row("Singapore, Singapore", "2026-07-12", 35.0, None)
        v = _v("Singapore, Singapore", "2026-07-12", 30.2)
        ref = run._twc_cross_reference(v)
        self.assertTrue(ref["amber"])
        self.assertGreater(ref["divergence"], ref["divergence_threshold"])
        out = "\n".join(run._twc_cross_reference_lines(v))
        self.assertIn("LARGE", out)
        self.assertIn("flag for review", out)

    # ── pure additivity: the council number is never touched ─────────────────────────────────────
    def test_block_never_changes_council_value(self):
        self._settled_above("Singapore, Singapore", offset=1.0, n=22)
        self._row("Singapore, Singapore", "2026-07-12", 31.0, None)
        v = _v("Singapore, Singapore", "2026-07-12", 30.2)
        before = v.high
        run._twc_cross_reference_lines(v)                        # render the block
        self.assertEqual(v.high, before)                         # council high unchanged
        self.assertEqual(run._twc_cross_reference(v)["council_high"], 30.2)


if __name__ == "__main__":
    unittest.main()
