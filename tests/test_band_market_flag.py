"""KAT for the band-vs-market-modal honesty flag (run.py _band_market_flag /
_market_modal_c) — the 2026-07-14 SF specimen: the served band 28–30°C (82%) printed one
line above a cross-check naming 26°C the independent-signal bucket, and WU settled 79°F=26°C
(model_prob 4%, market 86.5%). The flag is a LABELING fix (rule 2, gateless): it never
touches the pmf, the modal, the span, or any probability — it only refuses to let the
pmf-self-assessed band % stand unqualified when the market modal sits outside the band.
Band EXTENSION is a separate gated candidate (band_cover_market_modal.md, ACCRUING).

Written as unittest.TestCase — pytest-style bare functions run ZERO tests under the
repo's `unittest discover` gate (learned 2026-07-12)."""
import unittest

from run import _band_market_flag, _market_modal_c


class _Cmp:
    def __init__(self, market_modal, grain="C"):
        self.market_modal = market_modal
        self.grain = grain


class TestMarketModalC(unittest.TestCase):
    def test_celsius_label_passthrough(self):
        self.assertEqual(_market_modal_c(_Cmp("26°C")), 26)

    def test_fahrenheit_label_converts_to_c_bucket(self):
        # 66-67°F → first int 66 → (66-32)*5/9 = 18.9 → round-half-up 19
        self.assertEqual(_market_modal_c(_Cmp("66-67°F", grain="F")), 19)

    def test_sf_0714_specimen(self):
        # 78-79°F → 78°F → 25.6°C → bucket 26 (the settled bucket the band excluded)
        self.assertEqual(_market_modal_c(_Cmp("78-79°F", grain="F")), 26)

    def test_absent_comparison_is_none(self):
        self.assertIsNone(_market_modal_c(None))
        self.assertIsNone(_market_modal_c(_Cmp(None)))
        self.assertIsNone(_market_modal_c(_Cmp("no digits here")))


class TestBandMarketFlag(unittest.TestCase):
    def test_flags_market_modal_outside_band(self):
        flag = _band_market_flag([28, 29, 30], 26)
        self.assertIsNotNone(flag)
        self.assertIn("26°C", flag)
        self.assertIn("OUTSIDE", flag)
        # must quote the MEASURED coverage, not assert a fix
        self.assertIn("73–74.5%", flag)
        self.assertIn("band_cover_market_modal", flag)

    def test_silent_when_market_modal_inside_band(self):
        self.assertIsNone(_band_market_flag([26, 27, 28], 26))

    def test_silent_when_no_market_or_no_span(self):
        self.assertIsNone(_band_market_flag([28, 29, 30], None))
        self.assertIsNone(_band_market_flag(None, 26))
        self.assertIsNone(_band_market_flag([], 26))

    def test_flag_is_label_only_no_numbers_changed(self):
        # The flag is a string; it must not carry a revised band or probability claim.
        flag = _band_market_flag([28, 29, 30], 26)
        self.assertNotIn("extended", flag.lower())
        self.assertNotIn("new band", flag.lower())


if __name__ == "__main__":
    unittest.main()
