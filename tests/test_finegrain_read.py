"""KAT for tools/finegrain_read.py — the 00Z / T-group fine-grain settlement read
(manual §14g; 2026-07-16 specimen: 6h-max 20.6°C = 69.1°F resolved KXHIGHTSFO-26JUL16
to the 68-69 bucket five hours before settlement).

unittest.TestCase (pytest-style bare functions run ZERO tests under the repo gate)."""
import unittest

from tools.finegrain_read import (finegrain_day_max, parse_six_hour_max,
                                  parse_t_group, _pattern_lines,
                                  _cli_seam_note, _screen_obs)


class TestFineGrainParsers(unittest.TestCase):

    def test_module_self_test(self):
        from tools import finegrain_read
        self.assertEqual(finegrain_read._selftest(), 0)

    def test_t_group_tenths_and_sign(self):
        self.assertEqual(parse_t_group("RMK AO2 SLP121 T02060183"), 20.6)
        self.assertEqual(parse_t_group("RMK T10061011"), -0.6)
        self.assertIsNone(parse_t_group("KSFO 31015KT 10SM FEW008"))

    def test_six_hour_max_does_not_false_match_wind_vis_slp(self):
        self.assertEqual(parse_six_hour_max("RMK AO2 SLP121 10206 20111"), 20.6)
        self.assertIsNone(parse_six_hour_max("31015KT 10SM SLP121 T02060183"))

    def test_specimen_2026_07_16(self):
        # The exact resolution datum: T-group and 6h-max both 20.6C -> CLI 69,
        # killing the 70-71 bucket (no between-obs spike existed).
        rows = [("2026-07-16 12:56", "RMK AO2 T02060183"),
                ("2026-07-16 16:56", "RMK AO2 10206 T01890178")]
        r = finegrain_day_max(rows, "2026-07-16")
        self.assertEqual(r["cli_whole_f"], 69)
        self.assertEqual(r["max_c"], 20.6)

    def test_cli_rounding_boundary(self):
        # The frozen decision rule: <=20.8C -> 69, >=20.9C -> 70.
        self.assertEqual(round(20.8 * 9 / 5 + 32), 69)
        self.assertEqual(round(20.9 * 9 / 5 + 32), 70)


class TestPatternLabeling(unittest.TestCase):
    """2026-07-27 audit: pattern_rate measures the hourly-OBS climb rate (the
    archive is (hour, °C) rows — no 6-hourly groups), yet its output labeled
    itself 'the CLI-catch path' and served 0% against the 70-71 bucket the CLI
    then paid via `10211`. The label is pinned honest here so it cannot rot
    back; the NUMBERS (n_match, n_catch) are untouched (labeling-only)."""

    def test_output_says_obs_climb_not_cli_catch(self):
        lines = _pattern_lines("KSFO", 17.0, 69.1, (158, 0))
        txt = "\n".join(lines)
        self.assertIn("ON THE HOURLY-OBS RECORD", txt)
        self.assertIn("obs-climb rate", txt)
        self.assertIn("NOT the CLI settle", txt)

    def test_output_carries_the_asymmetry_caveat(self):
        txt = "\n".join(_pattern_lines("KSFO", 17.0, 69.1, (158, 0)))
        self.assertIn(">= this", txt)              # paying rate is at least this
        self.assertIn("6-hourly groups", txt)      # the mechanism
        self.assertIn("07-27", txt)                # the specimen that exposed it

    def test_numbers_render_unchanged(self):
        # Same (n, k) in, same figures out — only the words changed.
        txt = "\n".join(_pattern_lines("KSFO", 17.0, 69.1, (158, 0)))
        self.assertIn("158 archive days", txt)
        self.assertIn("0 (0%) climbed", txt)
        self.assertIn(">= 69.5F", txt)

    def test_seam_note_reads_the_shared_ledger(self):
        self.assertIn("n=", _cli_seam_note("KSFO"))        # committed series
        self.assertEqual(_cli_seam_note("XXXX"), "seam not yet logged")

    def test_screen_obs_drops_malformed_and_out_of_band(self):
        obs = [(0.9, 13.9), (1.9, 999.0), (2.9, "x"), (3.9, True), (4.9, -60.0)]
        self.assertEqual(_screen_obs(obs), [(0.9, 13.9)])


if __name__ == "__main__":
    unittest.main()
