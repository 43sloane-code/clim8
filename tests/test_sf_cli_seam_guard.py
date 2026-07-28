"""KATs for the CLI-seam guard in the intraday-ceiling block (run._cli_seam_guard_lines).

2026-07-27 KSFO specimen: the obs-scale modal 69°F was served at 78% ("HIGH-
CONVICTION") while the settling NWS CLI printed 70 via the 18-00Z 6-hourly catch
(`10211` = 21.1°C = 69.98°F — the between-obs max the hourly T-groups never saw).
The +1.27°F CLI−WU seam was quoted in the SETTLEMENT RECORD block all afternoon
but never applied at the bucket-interpretation point, and a whole-°C 5-minute
plateau at the boundary was misread as evidence FOR the lower bucket.

The guard is LABELING ONLY (sf_verdict_blockers #3 precedent): it must never
change a pmf/modal number — it appends the seam context and, when the modal is
the TOP of its 2°F market bucket before the 18-00Z group can have printed, the
UNRESOLVED-at-obs-scale warning. These KATs pin the firing and non-firing cases
so the guard cannot silently rot; `make check` runs them.
"""
import unittest

from weather_council.intraday_ceiling import IntradayCeiling
from run import _ceiling_lines, _cli_seam_guard_lines, _load_cli_seam


def _ceiling(modal=69, prob=0.78, hour=12, city="San Francisco, United States",
             grain="F"):
    return IntradayCeiling(
        kind="sharpened", city=city, target="2026-07-27", sub_degree=False,
        grain=grain, hour=hour, running_max_c=20.6, n_rise=160,
        pmf=((modal, prob), (modal + 1, 0.08), (modal + 2, 0.06), (modal + 4, 0.02)),
        modal_bucket=modal, modal_prob=prob, source="test")


class TestGuardFires(unittest.TestCase):
    def test_top_of_bucket_modal_pre_group_warns(self):
        # The 07-27 case exactly: modal 69 (top of 68-69), 12:00 local.
        txt = "\n".join(_cli_seam_guard_lines(_ceiling(modal=69, hour=12)))
        self.assertIn("CLI-scale guard", txt)
        self.assertIn("⚠", txt)
        self.assertIn("68–69", txt)          # the modal's own market bucket
        self.assertIn("70–71", txt)          # the bucket the CLI-catch can pay
        self.assertIn("UNRESOLVED", txt)

    def test_seam_context_names_the_mechanism(self):
        txt = "\n".join(_cli_seam_guard_lines(_ceiling()))
        self.assertIn("6-hourly groups", txt)
        self.assertIn("AT OR ABOVE", txt)    # the asymmetry the guard exists for

    def test_guard_present_inside_full_ceiling_block(self):
        txt = "\n".join(_ceiling_lines(_ceiling()))
        self.assertIn("CLI-scale guard", txt)


class TestGuardDoesNotFire(unittest.TestCase):
    def test_even_modal_is_safe(self):
        # 68 is the BOTTOM of 68-69: a +1°F CLI-catch stays inside the bucket.
        lines = _cli_seam_guard_lines(_ceiling(modal=68))
        self.assertTrue(any("CLI-scale guard" in l for l in lines))   # context stays
        self.assertFalse(any("⚠" in l for l in lines))                # warning does not

    def test_post_group_hour_is_safe(self):
        # From ~17:00 local the 18-00Z group has printed: the catch is measured,
        # no longer a risk.
        lines = _cli_seam_guard_lines(_ceiling(modal=69, hour=17))
        self.assertFalse(any("⚠" in l for l in lines))

    def test_grain_c_city_gets_nothing(self):
        self.assertEqual(_cli_seam_guard_lines(
            _ceiling(city="London", grain="C", modal=30)), [])

    def test_non_cli_f_city_gets_nothing(self):
        # Seattle settles whole-°F but is not a CLI-primary (Kalshi) station.
        self.assertEqual(_cli_seam_guard_lines(
            _ceiling(city="Seattle", modal=69)), [])

    def test_unavailable_kind_gets_nothing(self):
        c = IntradayCeiling(kind="unavailable", city="San Francisco",
                            target="2026-07-27", sub_degree=False, grain="F",
                            note="test")
        self.assertEqual(_cli_seam_guard_lines(c), [])


class TestLabelingOnly(unittest.TestCase):
    def test_pmf_and_modal_render_unchanged(self):
        # The guard may annotate but NEVER move a served number: the pmf line and
        # the conviction line render byte-for-byte as before.
        txt = "\n".join(_ceiling_lines(_ceiling()))
        self.assertIn("    sharpened final-max pmf: 69°F 78%  70°F 8%  71°F 6%  73°F 2%", txt)
        self.assertIn("=> HIGH-CONVICTION call: 69°F at 78%", txt)


class TestSeamLoader(unittest.TestCase):
    def test_ksfo_seam_series_loads(self):
        seam = _load_cli_seam("KSFO")
        self.assertIsNotNone(seam)           # ledger/ksfo_cli_wu.jsonl is committed
        self.assertIn("mean", seam)
        self.assertGreater(seam["n"], 0)

    def test_unknown_station_degrades_to_none(self):
        self.assertIsNone(_load_cli_seam("XXXX"))


if __name__ == "__main__":
    unittest.main()
