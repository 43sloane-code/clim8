"""Network-free tests for the tuning surface (CouncilConfig).

The handful of constants the daily health check sweeps — WEIGHT_POWER,
OUTLIER_FLOOR_C, DISP_NORMAL, DISP_ELEVATED — are gathered into one frozen,
validated dataclass so a bad knob fails loudly at construction instead of as a
silent bad forecast, and so the module-level names can never drift from it. These
tests pin that contract:

  * the live CONFIG carries the committed, backtested values (a refactor that
    silently changed a tuned constant would break here);
  * the module-level aliases equal CONFIG exactly (no divergence between the two
    ways the code reads the same knob);
  * out-of-range knobs are REJECTED at construction (positive exponent/floors,
    elevated band strictly above the normal band);
  * CONFIG is frozen (you cannot mutate a tuned knob at runtime).

Run with:  PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import dataclasses
import unittest

from weather_council import council as c


class TestCouncilConfig(unittest.TestCase):

    def test_committed_values(self):
        self.assertEqual(c.CONFIG.weight_power, 2)
        self.assertIs(type(c.CONFIG.weight_power), int)
        self.assertEqual(c.CONFIG.outlier_floor_c, 4.0)
        self.assertEqual(c.CONFIG.disp_normal, 2.0)
        self.assertEqual(c.CONFIG.disp_elevated, 3.5)

    def test_module_aliases_match_config(self):
        # The back-compat names must be the SAME values CONFIG holds.
        self.assertEqual(c.WEIGHT_POWER, c.CONFIG.weight_power)
        self.assertEqual(c.OUTLIER_FLOOR_C, c.CONFIG.outlier_floor_c)
        self.assertEqual(c.DISP_NORMAL, c.CONFIG.disp_normal)
        self.assertEqual(c.DISP_ELEVATED, c.CONFIG.disp_elevated)

    def test_rejects_nonpositive_weight_power(self):
        with self.assertRaises(ValueError):
            c.CouncilConfig(weight_power=0)
        with self.assertRaises(ValueError):
            c.CouncilConfig(weight_power=-1)

    def test_rejects_float_weight_power(self):
        # Inverse-error exponent is an integer power; a float is a typo, not a knob.
        with self.assertRaises(ValueError):
            c.CouncilConfig(weight_power=2.0)

    def test_rejects_nonpositive_floor_and_dispersion(self):
        with self.assertRaises(ValueError):
            c.CouncilConfig(outlier_floor_c=0.0)
        with self.assertRaises(ValueError):
            c.CouncilConfig(disp_normal=0.0)

    def test_elevated_must_exceed_normal(self):
        with self.assertRaises(ValueError):
            c.CouncilConfig(disp_normal=3.0, disp_elevated=2.0)
        with self.assertRaises(ValueError):
            c.CouncilConfig(disp_normal=2.0, disp_elevated=2.0)   # equal is not "above"

    def test_config_is_frozen(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            c.CONFIG.weight_power = 9          # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
