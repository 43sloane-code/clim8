"""Tests for the TWC forecast forward-logger (tools/twc_forecast_logger).

Verifies the pure date-alignment + °F→°C bucket logic deterministically (the network
fetch and DB write are not exercised): the correct day's high/low is picked from the
parallel TWC arrays, missing dates and null highs yield None, and the whole-°F forecast
maps to the right settlement bucket. Also runs the tool's own selftest."""
import unittest

from tools.twc_forecast_logger import _pick, _f_to_c, _selftest
from weather_council.market import _native_reading_int


class TestTwcForecastLogger(unittest.TestCase):
    def test_pick_date_alignment(self):
        valid = ["2026-07-01T07:00:00+0800", "2026-07-02T07:00:00+0800"]
        self.assertEqual(_pick(valid, [90, 86], [78, 79], "2026-07-02"), (86.0, 79.0))
        self.assertEqual(_pick(valid, [90, 86], [78, 79], "2026-07-01"), (90.0, 78.0))

    def test_pick_missing_and_null(self):
        valid = ["2026-07-01T07:00:00+0800"]
        self.assertIsNone(_pick(valid, [90], [78], "2026-07-09"))        # date absent
        self.assertIsNone(_pick(valid, [None], [78], "2026-07-01"))      # null high guarded

    def test_f_to_c_bucket_is_settlement_grade(self):
        self.assertAlmostEqual(_f_to_c(86), 30.0)
        self.assertEqual(_native_reading_int(_f_to_c(86), "C", False), 30)   # 86°F -> 30°C
        self.assertEqual(_native_reading_int(_f_to_c(90), "C", False), 32)   # 90°F -> 32.2°C -> 32

    def test_selftest_passes(self):
        self.assertEqual(_selftest(), 0)


if __name__ == "__main__":
    unittest.main()
