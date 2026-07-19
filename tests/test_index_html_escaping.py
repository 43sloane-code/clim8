"""Gateless KAT for index.html upstream-string escaping (F1).

The page renders third-party API strings (place names, market titles, bucket
labels, observation backbone, health-check recommendations) into innerHTML.
This test does NOT need a browser: it verifies that the single escape helper is
present and that the highest-risk interpolations route through it. A place name
containing HTML metacharacters would therefore render as literal text.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


class TestIndexHtmlEscaping(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.html = Path(__file__).resolve().parent.parent / "index.html"
        cls.text = cls.html.read_text()

    def test_escape_helper_present(self):
        self.assertIn("const esc = s =>", self.text)
        self.assertIn("String(s).replace(/[&<>\"']/g", self.text)

    def test_high_risk_strings_routed_through_esc(self):
        required = [
            "${esc(d.place)}",
            "${esc(d.target)}",
            "${esc(obs.backbone)}",
            "${esc(ts.label)}",
            "${esc(st.source||\"raw airport METAR\")}",
            "${esc(st.high_native)}",
            "${esc(st.low_native)}",
            "${esc(mc.market_title)}",
            "${esc(mc.model_modal||'–')}",
            "${esc(mc.market_modal||'–')}",
            "${esc(mc.verdict_bucket||'–')}",
            "${esc(mc.verdict_reading)}",
            "${esc(b.label)}",
            "${esc(m.institution)}",
            "${esc(reg.label)}",
            "${esc(rc.takeaway)}",
            '${esc((s&&s.reason)||"health-check status unavailable")}',
            '${esc(lp.reason||"")}',
            '${esc(hc.date||"–")}',
        ]
        for r in required:
            with self.subTest(pattern=r):
                self.assertIn(r, self.text)

    def test_no_raw_interpolation_for_high_risk_strings(self):
        # These patterns must NOT appear unescaped in innerHTML contexts.
        forbidden = [
            r"\$\{d\.place\}",
            r"\$\{d\.target\}",
            r"\$\{obs\.backbone\}",
            r"\$\{ts\.label\}",
            r"\$\{mc\.market_title\}",
            r"\$\{mc\.model_modal\}(?!\|\|)",
            r"\$\{mc\.market_modal\}(?!\|\|)",
            r"\$\{mc\.verdict_bucket\}(?!\|\|)",
            r"\$\{m\.institution\}",
            r"\$\{reg\.label\}",
        ]
        for pat in forbidden:
            with self.subTest(pattern=pat):
                matches = re.findall(pat, self.text)
                self.assertEqual(matches, [], f"found unescaped {pat}")

    def test_anchor_strings_escaped_at_source(self):
        # anchor/xref/note/error are escaped where extracted so composites
        # (body/takeaway) stay HTML-safe without double-escaping.
        self.assertIn("const anchor = esc(xr.anchor_station", self.text)
        self.assertIn("const xref = esc(xr.cross_ref_station", self.text)
        self.assertIn("${esc(xr.error)}", self.text)
        self.assertIn("${esc(xr.note)}", self.text)


if __name__ == "__main__":
    unittest.main()
