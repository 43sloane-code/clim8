"""Network-free tests for candidate 44 — the codified STOP/RESTART rule for the
harness-optimizer search loop. Proves the four load-bearing behaviours with
deterministic oracles AND against the REAL ledger:

  * verdict classification precedence (POSITIVE > NEGATIVE > NEUTRAL) across both
    ledger eras, and the trailing-streak counter (SIM/ANALYSIS skipped, SHIP resets);
  * AUTO-SUSPEND at the threshold; AUTO-REARM only when EVERY station clears the
    fresh-row bar; SETTLEMENT FREEZE dominates an otherwise-healthy loop;
  * the FALSIFIED re-audit downgrades a thin-sample falsification to UNDERPOWERED,
    keeps an adequately-powered one, and flags an unknown-n one for REVIEW;
  * REPLAY on the live ledger: the rule produces a coherent state and the #38
    re-audit verdict matches its recorded n (the spec presumed n<30; reality is
    n=460, so the headline FALSIFIED is KEPT, not downgraded).

Stdlib unittest only. Run with:
    PYTHONPATH=. python3 -m unittest discover -s tests
"""
from __future__ import annotations

import json
import os
import unittest

from weather_council.stop_rule import (
    DEFAULT_CONFIG, load_config, classify_verdict, consecutive_negative_streak,
    effective_n, reaudit_falsified, loop_state, _self_test,
)

LEDGER = os.path.join(os.path.dirname(__file__), "..", ".harness_opt", "ledger.json")


def _E(id_, verdict="", kind="", title="", **kw):
    d = {"id": id_, "verdict": verdict, "kind": kind, "title": title}
    d.update(kw)
    return d


class TestStopRule(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config(path="")        # defaults only — repo-independent

    def test_module_self_test(self):
        _self_test()

    def test_classification_precedence(self):
        self.assertEqual(classify_verdict(_E(1, kind="NO-BAKE+SIM"), self.cfg), "NEGATIVE")
        self.assertEqual(classify_verdict(_E(2, kind="ANALYSIS+NO-BAKE"), self.cfg), "NEGATIVE")
        self.assertEqual(classify_verdict(_E(3, verdict="SHIP_RECOMMEND_ONLY"), self.cfg), "POSITIVE")
        self.assertEqual(classify_verdict(_E(4, kind="SIM"), self.cfg), "NEUTRAL")
        # a ship + a no-bake in one entry counts as progress (resets the streak).
        self.assertEqual(classify_verdict(_E(5, verdict="SHIP", kind="NO-BAKE"), self.cfg), "POSITIVE")

    def test_streak_skips_neutral_and_resets_on_positive(self):
        log = [_E(40, verdict="NO_BAKE"), _E(41, kind="SIM"),
               _E(42, verdict="NO_BAKE"), _E(43, verdict="NO_BAKE")]
        s = consecutive_negative_streak(log, self.cfg)
        self.assertEqual(s["streak"], 3)
        self.assertEqual(s["ids"], [40, 42, 43])           # 41 (SIM) skipped, order restored
        self.assertEqual(consecutive_negative_streak(log + [_E(44, verdict="SHIP")], self.cfg)["streak"], 0)

    def test_suspend_threshold_and_active_below(self):
        neg3 = [_E(i, verdict="NO_BAKE") for i in (40, 41, 42)]
        st = loop_state(neg3, self.cfg)
        self.assertEqual(st["status"], "SUSPENDED")
        self.assertFalse(st["can_propose"])
        self.assertEqual(loop_state(neg3[:2], self.cfg)["status"], "ACTIVE")

    def test_rearm_requires_every_station(self):
        neg3 = [_E(i, verdict="NO_BAKE") for i in (40, 41, 42)]
        thin = {s: 30 for s in self.cfg["stations"]}
        thin["london_low"] = 12
        self.assertEqual(loop_state(neg3, self.cfg, new_rows_since_suspend=thin)["status"], "SUSPENDED")
        ok = {s: 30 for s in self.cfg["stations"]}
        rearmed = loop_state(neg3, self.cfg, new_rows_since_suspend=ok)
        self.assertEqual(rearmed["status"], "ACTIVE")
        self.assertTrue(rearmed["rearm_eligible"])

    def test_settlement_freeze_dominates(self):
        healthy = [_E(40, verdict="SHIP")]
        fr = loop_state(healthy, self.cfg, settlement_day=True)
        self.assertEqual(fr["status"], "FROZEN")
        self.assertFalse(fr["can_propose"])
        # freeze can be disabled by config.
        cfg2 = dict(self.cfg); cfg2["settlement_freeze"] = False
        self.assertEqual(loop_state(healthy, cfg2, settlement_day=True)["status"], "ACTIVE")

    def test_effective_n_extraction(self):
        self.assertEqual(effective_n(_E(1, n_paired_rows={"a": 18, "b": 25})), 18)
        self.assertEqual(effective_n(_E(2, n_paired_rows=460)), 460)
        self.assertEqual(effective_n(_E(3, wet_sigma_backtest={"window": "JJA 2021-25 n=460"})), 460)
        self.assertIsNone(effective_n(_E(4, title="no number here")))

    def test_reaudit_downgrades_thin_keeps_fat_flags_unknown(self):
        ra = reaudit_falsified([
            _E(90, title="sigma FALSIFIED", n_paired_rows={"a": 18}),
            _E(91, title="drift FALSIFIED", n_paired_rows=460),
            _E(92, title="mystery FALSIFIED"),
            _E(93, verdict="NO_BAKE"),                     # negative but not FALSIFIED
        ], self.cfg)
        by = {r["id"]: r for r in ra}
        self.assertEqual(by[90]["proposal"], "UNDERPOWERED")
        self.assertEqual(by[91]["proposal"], "KEEP_FALSIFIED")
        self.assertEqual(by[92]["proposal"], "REVIEW")
        self.assertNotIn(93, by)                            # only FALSIFIED entries audited

    def test_default_config_shape(self):
        self.assertEqual(DEFAULT_CONFIG["max_consecutive_nobake"], 3)
        self.assertEqual(DEFAULT_CONFIG["rearm_min_new_rows_per_station"], 30)
        self.assertTrue(DEFAULT_CONFIG["settlement_freeze"])

    def test_replay_live_ledger(self):
        """The rule produces a coherent state on the real ledger, and the #38
        re-audit matches its recorded sample size."""
        if not os.path.exists(LEDGER):
            self.skipTest("missing ledger")
        cfg = load_config(os.path.join(os.path.dirname(LEDGER), "stop_rule.json"))
        with open(LEDGER, encoding="utf-8") as fh:
            log = json.load(fh)["log"]
        state = loop_state(log, cfg)
        self.assertIn(state["status"], ("ACTIVE", "SUSPENDED", "FROZEN"))
        self.assertEqual(state["can_propose"], state["status"] == "ACTIVE")
        self.assertGreaterEqual(state["streak"], 0)
        # Structural invariant (robust to future appends): with no settlement day,
        # the loop is SUSPENDED exactly when the streak reaches the threshold.
        if state["streak"] >= cfg["max_consecutive_nobake"]:
            self.assertEqual(state["status"], "SUSPENDED")
        else:
            self.assertEqual(state["status"], "ACTIVE")
        # #38's FALSIFIED is on n=460 -> kept, not downgraded (binding-honesty note).
        ra = {r["id"]: r for r in reaudit_falsified(log, cfg)}
        self.assertIn(38, ra)
        self.assertEqual(ra[38]["proposal"], "KEEP_FALSIFIED")
        self.assertGreaterEqual(ra[38]["n"], 30)


if __name__ == "__main__":
    unittest.main()
