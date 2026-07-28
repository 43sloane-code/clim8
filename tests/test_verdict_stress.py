"""Verdict stress suite — synthetic day-types replayed through the SF intraday
ceiling block + CLI-seam guard (run._ceiling_lines / _cli_seam_guard_lines /
_market_bucket_top_boundary).

Born from the 2026-07-27 KSFO miss (obs modal 69 served at 78%, CLI paid 70 via
the 18-00Z catch) and extended by stress: each test is a day-type that has burned
or could burn a served intraday read. The guard is labeling-only, so these pin
WHEN the machine must refuse single-bucket vocabulary — the oversight that the
07-27 class cannot recur silently.

unittest.TestCase (pytest-style bare functions run ZERO tests under the repo gate)."""
import unittest

from weather_council.intraday_ceiling import IntradayCeiling
from run import (_ceiling_lines, _cli_seam_guard_lines,
                 _market_bucket_top_boundary)

_SF = "San Francisco, United States"


def _day(*, modal, rm_c, hour, prob=0.90):
    """A synthetic SF intraday state: banked running max rm_c (°C), sharpened pmf
    modal at `modal`°F with high conviction, at local hour `hour`."""
    return IntradayCeiling(
        kind="sharpened", city=_SF, target="2026-07-27", sub_degree=False,
        grain="F", hour=hour, running_max_c=rm_c, n_rise=160,
        pmf=((modal, prob), (modal + 1, 0.05), (modal + 2, 0.03), (modal + 4, 0.02)),
        modal_bucket=modal, modal_prob=prob, source="stress")


def _warned(c) -> bool:
    return any("⚠" in l for l in _cli_seam_guard_lines(c))


class TestBoundaryMath(unittest.TestCase):
    def test_bucket_top_boundaries(self):
        # Kalshi 2°F buckets [even, even+1] inclusive (kalshi_sf_seam.md).
        self.assertEqual(_market_bucket_top_boundary(69.1), 69.5)
        self.assertEqual(_market_bucket_top_boundary(68.0), 69.5)
        self.assertEqual(_market_bucket_top_boundary(70.2), 71.5)
        self.assertEqual(_market_bucket_top_boundary(69.5), 69.5)   # on the line


class TestDayTypes(unittest.TestCase):
    def test_0727_replay_warns(self):
        # THE miss: obs plateau at 20.6°C (69.08°F), modal 69 at 90%, 15:00 —
        # must refuse single-bucket vocabulary and name 70-71.
        txt = "\n".join(_ceiling_lines(_day(modal=69, rm_c=20.6, hour=15)))
        self.assertIn("⚠", txt)
        self.assertIn("70–71", txt)
        self.assertIn("UNRESOLVED", txt)

    def test_safe_mid_bucket_day_quiet(self):
        # Modal 68 with the max parked at 67.0°F: 2.5°F of headroom to the
        # boundary — no catch scenario reaches the next bucket.
        self.assertFalse(_warned(_day(modal=68, rm_c=19.4, hour=12)))

    def test_even_modal_high_in_bucket_warns(self):
        # The case the parity proxy MISSED (found by this stress pass): modal 68
        # but the obs max already 68.9°F — a +1.27 seam puts 70.2 within reach.
        self.assertTrue(_warned(_day(modal=68, rm_c=20.5, hour=14)))  # 68.9°F

    def test_plus2_catch_day_warns_even_from_even_modal(self):
        # 07-12 specimen class (CLI 76 vs WU 74): an even modal with the max
        # 0.6°F under the boundary is inside even a +2°F catch.
        self.assertTrue(_warned(_day(modal=68, rm_c=20.5, hour=11)))

    def test_post_00z_group_day_quiet(self):
        # From ~17:00 the 18-00Z group is measured: the catch is known, not a
        # risk — warning would be noise.
        self.assertFalse(_warned(_day(modal=69, rm_c=20.6, hour=17)))

    def test_early_morning_diffuse_day_still_warns_at_boundary(self):
        # 09:00 with the max already 69.1°F (hot start): boundary risk exists
        # all day — the guard must not assume mornings are safe.
        self.assertTrue(_warned(_day(modal=69, rm_c=20.6, hour=9)))

    def test_conviction_never_buys_silence(self):
        # 90% modal conviction must NOT suppress the boundary warning — the
        # 07-27 failure was high conviction at a boundary, not low.
        txt = "\n".join(_ceiling_lines(_day(modal=69, rm_c=20.6, hour=15, prob=0.90)))
        self.assertIn("=> HIGH-CONVICTION call: 69°F at 90%", txt)   # number stays
        self.assertIn("UNRESOLVED", txt)                             # label overrides

    def test_far_from_boundary_even_pre_noon_quiet(self):
        # Modal 66 with max 65.5°F: 4°F to the boundary — a quiet day must stay
        # quiet (guard precision: no warning fatigue).
        self.assertFalse(_warned(_day(modal=66, rm_c=18.6, hour=11)))
    def test_0728_live_book_line_day(self):
        # 07-28 day-type: book line at 70.5 (T71 tail — NOT on the static 2°F
        # grid), obs plateaus at 21.1°C=69.98 (banked 70). The static-grid guard
        # was quiet all afternoon; the book-aware guard must fire and name the
        # tail and the bucket by their contract titles.
        c = IntradayCeiling(**{**_day(modal=70, rm_c=21.1, hour=15).__dict__,
                               "target": "2026-07-28"})
        lines = _cli_seam_guard_lines(c)
        txt = "\n".join(lines)
        self.assertTrue(any("⚠" in l for l in lines))
        self.assertIn("70.5", txt)
        self.assertIn("70° or below", txt)
        self.assertIn("71° to 72°", txt)


if __name__ == "__main__":
    unittest.main()
