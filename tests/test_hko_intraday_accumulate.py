"""Network-free tests for the HKO hourly accumulator (the prospective archive that
will eventually unlock HK intraday conviction). Verifies it logs from the live
reading, keeps the per-hour MAX (running max), is idempotent within an hour, opens
new rows for new hours, and never raises on a feed gap.

Run with:  PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from tools.hko_intraday_accumulate import accumulate
from weather_council.sources import (HKO_1MIN_TEMP_URL, HKO_RHRREAD_URL,
                                      Sources)


class _Src:
    """Stand-in for Sources.hko_current."""
    def __init__(self, reading):
        self._r = reading

    def hko_current(self):
        return self._r


class _FakeHTTP:
    """Routes the two HKO open-data calls to recorded payloads, so the REAL
    parsers (_parse_hko_rhrread + _parse_hko_1min_temp) and the live
    Sources.hko_current chain run unchanged — only the network is replaced."""
    def __init__(self, rhrread: dict | None = None, one_min: str | None = None):
        self._rhrread = rhrread
        self._one_min = one_min

    def get_json(self, base_url, params):
        if base_url == HKO_RHRREAD_URL and self._rhrread is not None:
            return self._rhrread
        raise RuntimeError(f"unexpected get_json {base_url}")

    def get_text(self, base_url, params=None):
        if base_url == HKO_1MIN_TEMP_URL and self._one_min is not None:
            return self._one_min
        raise RuntimeError(f"unexpected get_text {base_url}")


def _reading(temp, rt="2026-06-17T09:00:00+08:00"):
    return {"temperature_2m": temp, "record_time": rt}


class TestAccumulate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Path(self.tmp.name) / "hko_intraday.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def _rows(self):
        with self.ledger.open(newline="") as fh:
            return list(csv.DictReader(fh))

    def test_logs_first_reading(self):
        line = accumulate(_Src(_reading(26.9)), self.ledger)
        self.assertIn("logged", line)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-06-17")
        self.assertEqual(int(rows[0]["hour"]), 9)
        self.assertEqual(rows[0]["temp_c"], "26.9")

    def test_same_hour_keeps_running_max(self):
        accumulate(_Src(_reading(26.9, "2026-06-17T09:10:00+08:00")), self.ledger)
        # a higher reading in the same hour updates the max
        line = accumulate(_Src(_reading(27.4, "2026-06-17T09:50:00+08:00")), self.ledger)
        self.assertIn("updated", line)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["temp_c"], "27.4")
        # a LOWER reading in the same hour is ignored (running max preserved)
        self.assertIsNone(accumulate(_Src(_reading(25.0, "2026-06-17T09:55:00+08:00")),
                                     self.ledger))
        self.assertEqual(self._rows()[0]["temp_c"], "27.4")

    def test_new_hour_opens_new_row(self):
        accumulate(_Src(_reading(26.9, "2026-06-17T09:00:00+08:00")), self.ledger)
        accumulate(_Src(_reading(28.1, "2026-06-17T13:00:00+08:00")), self.ledger)
        rows = self._rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual({(r["date"], int(r["hour"])) for r in rows},
                         {("2026-06-17", 9), ("2026-06-17", 13)})

    def test_feed_gap_logs_nothing_and_does_not_raise(self):
        self.assertIsNone(accumulate(_Src(None), self.ledger))
        self.assertIsNone(accumulate(_Src({"temperature_2m": None}), self.ledger))
        self.assertFalse(self.ledger.exists())

    def test_missing_record_time_falls_back_to_now(self):
        line = accumulate(_Src({"temperature_2m": 30.0}), self.ledger)
        self.assertIsNotNone(line)            # still logs, keyed on the local clock
        self.assertEqual(len(self._rows()), 1)


class TestLivePathParsing(unittest.TestCase):
    """End-to-end through the real HKO parsers and Sources.hko_current chain,
    with only the network stubbed. The unit tests above stub hko_current, so
    without this the production parse path (rhrread + 1-min override + record-time
    bucketing — the 0.1°C signal the whole HK intraday premise rides on) is never
    exercised by the accumulator's own suite."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Path(self.tmp.name) / "hko_intraday.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def _rows(self):
        with self.ledger.open(newline="") as fh:
            return list(csv.DictReader(fh))

    @staticmethod
    def _rhrread(temp_whole, rt="2026-06-17T14:00:00+08:00"):
        return {"temperature": {"recordTime": rt,
                                "data": [{"place": "Hong Kong Observatory",
                                          "value": temp_whole, "unit": "C"},
                                         {"place": "Sha Tin",
                                          "value": temp_whole + 1, "unit": "C"}]},
                "humidity": {"recordTime": rt,
                             "data": [{"place": "Hong Kong Observatory",
                                       "value": 73, "unit": "percent"}]}}

    @staticmethod
    def _onemin(temp_fine, stamp="202606171400"):
        return ("Date time,Automatic Weather Station,Air Temperature(degree Celsius)\n"
                f"{stamp},HK Observatory,{temp_fine}\n"
                f"{stamp},Ta Kwu Ling,32.1\n")

    def test_full_chain_prefers_1min_tenth_degree(self):
        # rhrread reports whole 31; the 1-minute gauge reports 31.4 — the ledger
        # must capture the finer 0.1°C value, keyed to the reading's own hour.
        src = Sources(_FakeHTTP(self._rhrread(31), self._onemin("31.4")))
        line = accumulate(src, self.ledger)
        self.assertIn("logged", line)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-06-17")
        self.assertEqual(int(rows[0]["hour"]), 14)
        self.assertEqual(rows[0]["temp_c"], "31.4")

    def test_full_chain_falls_back_to_whole_degree_when_1min_absent(self):
        # 1-minute feed missing the Observatory row -> keep the rhrread whole value.
        src = Sources(_FakeHTTP(self._rhrread(30), "h\n202606171400,Foo,30.0\n"))
        line = accumulate(src, self.ledger)
        self.assertIsNotNone(line)
        self.assertEqual(self._rows()[0]["temp_c"], "30.0")


if __name__ == "__main__":
    unittest.main()
