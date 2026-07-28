"""KAT for tools/verify_cli_archive.py — the S2 rule-2 verifier (kalshi_sf_seam.md:
the IEM parsed-CLI archive must be verified against first-party CLISFO text on
≥30 recent days BEFORE the sf_cli_scale_intraday_pmf.md probe may score against
it; on failure, direct capture forward-only). Pins the parser, the "M"-sentinel
honesty, and all four verdict paths offline.

unittest.TestCase (pytest-style bare functions run ZERO tests under the repo gate)."""
import unittest

from tools.verify_cli_archive import compare_cli_series, parse_cli_product

_SAMPLE = """CLIMATE REPORT filler
...THE SAN FRANCISCO AIRPORT CLIMATE SUMMARY FOR JULY 26 2026...
 MAXIMUM         69   4:04 PM  94    1963  73     -4       69
 MINIMUM         59  11:59 PM"""


class TestParseCliProduct(unittest.TestCase):
    def test_module_self_test(self):
        from tools import verify_cli_archive
        self.assertEqual(verify_cli_archive._selftest(), 0)

    def test_date_and_maximum(self):
        self.assertEqual(parse_cli_product(_SAMPLE), ("2026-07-26", 69.0))

    def test_missing_sentinel_is_none_not_a_number(self):
        # The kalshi_logger bug class: "M" must never pass an is-not-None check.
        self.assertEqual(parse_cli_product(_SAMPLE.replace("69   4:04", "M    4:04")),
                         ("2026-07-26", None))

    def test_unparseable_returns_none(self):
        self.assertIsNone(parse_cli_product("no climate header here"))


class TestVerdictGate(unittest.TestCase):
    def setUp(self):
        self.good = [(f"2026-07-{d:02d}", 70.0, 70.0) for d in range(1, 31)]

    def test_adopt(self):
        self.assertEqual(compare_cli_series(self.good)["verdict"], "ADOPT")

    def test_reject_on_tolerance_breach(self):
        off = self.good[:29] + [("2026-07-30", 70.0, 74.0)]
        self.assertEqual(compare_cli_series(off)["verdict"], "REJECT")

    def test_reject_on_exact_rate(self):
        sloppy = [(d, a, b if i % 2 else b + 1.0)
                  for i, (d, a, b) in enumerate(self.good)]
        self.assertEqual(compare_cli_series(sloppy)["verdict"], "REJECT")

    def test_insufficient_below_30_and_bar_not_relaxed(self):
        r = compare_cli_series(self.good[:12])
        self.assertEqual(r["verdict"], "INSUFFICIENT")
        self.assertIn("not relaxed", r["reason"])

    def test_missing_side_never_comparable(self):
        missing = [(d, None, b) for d, _, b in self.good]
        self.assertEqual(compare_cli_series(missing)["n_comparable"], 0)


if __name__ == "__main__":
    unittest.main()
