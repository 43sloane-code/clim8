"""Tests for the self-evaluation brief (tools/eval_harness).

Pins the vocabulary-enforcement contract: uncertified convictions must be labeled, 'final'
is a post-sunset word, certified hours cite only the measured number, accruing clocks are
counts not conclusions, and the exact citable sentences are generated verbatim."""
import unittest

from tools.eval_harness import brief, _selftest


def _state(**over):
    base = {"now_sgt": "2026-07-04T16:00", "pre_sunset": True,
            "lock": {"rows": 10, "settled": 8,
                     "cov": {15: {"n": 3, "mean_stated": 0.95, "hit_rate": 2 / 3, "gap": -0.28}},
                     "status": {15: "ACCRUING"},
                     "today": {"hour": 15, "modal_bucket": 32, "modal_prob": 0.97,
                               "running_max_c": 32.2}},
            "scorecard": {"Singapore": {"n": 10, "hits": 5, "rate": 0.5}},
            "twc": {"n": 2, "hits": 1}, "pop": {"dry": 0, "convective": 2},
            "dead": ["D01", "D14"]}
    base.update(over)
    return base


class TestEvalHarness(unittest.TestCase):
    def test_uncertified_label_and_citable_sentence(self):
        out = "\n".join(brief(_state()))
        self.assertIn("UNCERTIFIED", out)
        self.assertIn("backtest 95%, live 2/3", out)     # the exact relayable citation

    def test_final_is_a_post_sunset_word(self):
        self.assertIn("NOT FINAL", "\n".join(brief(_state(pre_sunset=True))))
        self.assertIn("'final' is permitted", "\n".join(brief(_state(pre_sunset=False))))

    def test_certified_hour_cites_measured_only(self):
        s = _state()
        s["lock"] = dict(s["lock"],
                         cov={15: {"n": 25, "mean_stated": 0.95, "hit_rate": 0.92, "gap": -0.03}},
                         status={15: "CERTIFIED"})
        out = "\n".join(brief(s))
        self.assertIn("measured 92% (n=25)", out)
        self.assertNotIn("backtest 95%, live", out)      # certified -> the backtest label retires

    def test_accruing_clocks_are_counts(self):
        out = "\n".join(brief(_state()))
        self.assertIn("2/40 settled pairs", out)         # TWC counted, not concluded
        self.assertIn("0/15 dry days", out)              # PoP counted, not concluded
        self.assertIn("do not relitigate", out)          # dead ledger stated

    def test_selftest_passes(self):
        self.assertEqual(_selftest(), 0)


if __name__ == "__main__":
    unittest.main()
