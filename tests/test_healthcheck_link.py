"""Network-free tests for the one-way link between the recommend-only daily
health check and the LIVE verdict.

The link is deliberately read-only: the health check WRITES a compact, machine-
readable status file (tools/daily_healthcheck._write_status); the verdict READS
it and renders a banner (run._healthcheck_banner). The banner DISPLAYS the
monitor's findings beside the verdict — it never reads back into, gates, or moves
any forecast number. These tests pin that contract honest:

  * absent or malformed status -> NO banner, so the verdict is never blocked by
    the monitor (a missing/garbage file can't take the verdict down);
  * a populated, fresh status -> a banner that shows MAE-vs-baseline, coverage,
    and surfaces recommendations explicitly marked "do NOT auto-apply";
  * a stale status (monitor hasn't run in >2 days) is flagged STALE, and a
    regression is flagged, so an operator can't mistake old green for fresh green;
  * a PRODUCER->CONSUMER round-trip: a dict written by _write_status is read back
    by _healthcheck_banner, so the writer and reader can never drift apart on the
    schema (the whole point of the link).

daily_healthcheck is loaded by file path because tools/ is intentionally not an
importable package; run is importable from the project root.

Run with:  PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import run

_HC_PATH = Path(__file__).resolve().parent.parent / "tools" / "daily_healthcheck.py"
_spec = importlib.util.spec_from_file_location("daily_healthcheck", _HC_PATH)
hc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hc)


def _status(**over) -> dict:
    base = {
        "date": dt.date.today().isoformat(),
        "variant": ["mean", 2],
        "basket_mae": 0.7948,
        "baseline_mae": 0.7949,
        "baseline_date": "2026-06-06",
        "regression": False,
        "calibration_coverage_pct": 72.4,
        "calibration_label": "OVER-CONFIDENT (under-dispersed)",
        "recommendations": [],
        "cities_usable": 8,
        "cities_total": 8,
        "data_freshness_max_gap_days": 84,
        "requests": 233,
        "metrics": {
            "run_seconds": 142.3,
            "requests": 233,
            "cities_usable": 8,
            "cities_total": 8,
            "city_error_rate": 0.0,
            "backtest_mae": 0.7948,
            "coverage_pct_80": 72.4,
            "data_freshness_max_gap_days": 84,
        },
    }
    base.update(over)
    return base


class TestVerdictBannerIsDisplayOnly(unittest.TestCase):

    def test_absent_status_yields_no_banner(self):
        # A missing monitor file must never block or decorate the verdict.
        out = run._healthcheck_banner(status_path=Path("/no/such/status.json"))
        self.assertEqual(out, [])

    def test_malformed_status_yields_no_banner(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.json"
            p.write_text("{ this is not json")
            self.assertEqual(run._healthcheck_banner(status_path=p), [])

    def test_fresh_status_renders_findings_and_recommendations(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            p.write_text(json.dumps(_status(recommendations=[
                "widen predictive spread (80% coverage below 70%)",
                "variant->",
            ])))
            out = run._healthcheck_banner(today=dt.date.today(), status_path=p)
        text = "\n".join(out)
        # It announces itself as a display-only, recommend-only monitor.
        self.assertIn("recommend-only monitor", text)
        self.assertIn("never moves this verdict", text)
        # It shows MAE-vs-baseline and coverage drawn from the status file.
        self.assertIn("basket MAE 0.7948", text)
        self.assertIn("baseline 0.7949", text)
        self.assertIn("72.4%", text)
        # Recommendations are surfaced AND fenced as not-auto-applied.
        self.assertIn("do NOT auto-apply", text)
        self.assertIn("widen predictive spread", text)
        # Measured operational metrics are surfaced read-only.
        self.assertIn("monitor run 142.3s", text)
        self.assertIn("233 requests", text)

    def test_no_recommendations_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            p.write_text(json.dumps(_status(recommendations=[])))
            text = "\n".join(run._healthcheck_banner(today=dt.date.today(),
                                                     status_path=p))
        self.assertIn("no constant changes recommended", text)

    def test_stale_status_is_flagged(self):
        # 5 days old -> the operator must see it's not a fresh green.
        old = (dt.date.today() - dt.timedelta(days=5)).isoformat()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            p.write_text(json.dumps(_status(date=old)))
            text = "\n".join(run._healthcheck_banner(today=dt.date.today(),
                                                     status_path=p))
        self.assertIn("STALE", text)

    def test_regression_is_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            p.write_text(json.dumps(_status(regression=True,
                                            basket_mae=0.86, baseline_mae=0.79)))
            text = "\n".join(run._healthcheck_banner(today=dt.date.today(),
                                                     status_path=p))
        self.assertIn("REGRESSION", text)


class TestWriterReaderRoundTrip(unittest.TestCase):
    """The producer (_write_status) and consumer (_healthcheck_banner) must agree
    on the schema. Write a status, read it straight back as a banner: if a key is
    renamed on one side only, this test breaks instead of silently going blank."""

    def test_write_status_round_trips_into_a_banner(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "healthcheck_status.json"
            hc._write_status(_status(recommendations=["variant-> something"]),
                             status_path=p)
            # File is valid JSON on disk...
            on_disk = json.loads(p.read_text())
            self.assertEqual(on_disk["basket_mae"], 0.7948)
            # ...and the verdict reader makes a non-empty banner from it.
            out = run._healthcheck_banner(today=dt.date.today(), status_path=p)
            self.assertTrue(out)
            self.assertIn("variant-> something", "\n".join(out))


if __name__ == "__main__":
    unittest.main()
