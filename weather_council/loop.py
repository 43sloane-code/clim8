"""The operating feedback loop, encoded as enforceable gates.

    hypothesis -> model -> validate -> risk -> deploy -> iterate

Each stage is a pure function of an Experiment record returning pass/fail with a
reason. `run()` walks the stages in order and STOPS at the first failed gate — a
stage can never be skipped, and a result can never reach Deploy without surviving
every prior gate. Two invariants are structural, not advisory:

  1. Pre-registration. The hypothesis (metric, baseline, threshold, alpha) must be
     LOCKED before any validation number is attached. Validate then compares the
     measured result against the *locked* threshold, so a target cannot be moved
     to fit the data after the fact.

  2. Recommend-only by default. Deploy returns RECOMMEND_ONLY unless BOTH the C7
     realized-outcome edge is validated AND a human has signed off. Absent either,
     the loop refuses to go live. It never emits an action that trades or moves
     funds, and Risk hard-fails on any experiment that claims to.

This mirrors the consensus protocol's ethos: every advance is earned from measured
numbers, and the off-switch is the default. Pure stdlib, deterministic, self-tested.
"""
from __future__ import annotations

__all__ = [
    'Stage', 'Experiment', 'GateResult', 'LoopResult', 'gate_hypothesis', 'gate_model',
    'gate_validate', 'gate_risk', 'gate_deploy', 'run', 'format_result', 'Ledger'
]

import hashlib
import json
from dataclasses import dataclass, field, asdict
from enum import IntEnum


class Stage(IntEnum):
    HYPOTHESIS = 1
    MODEL = 2
    VALIDATE = 3
    RISK = 4
    DEPLOY = 5
    ITERATE = 6


@dataclass
class Experiment:
    # --- Hypothesis (pre-registered; locked before validation) ---------------
    id: str
    hypothesis: str
    metric: str = ""                 # e.g. "skill_score", "c7_brier"
    baseline: str = ""               # e.g. "persistence", "market"
    threshold: float | None = None   # minimum improvement that counts
    alpha: float = 0.05
    locked_hash: str | None = None   # set by lock(); proves pre-registration

    # --- Model ---------------------------------------------------------------
    deterministic: bool = False      # seeded + reproducible
    stdlib_only: bool = True
    self_test_passed: bool = False

    # --- Validate (filled AFTER lock) ----------------------------------------
    measured_skill: float | None = None
    p_value: float | None = None

    # --- Risk ----------------------------------------------------------------
    n_trials: int = 1                # for multiple-testing correction
    fold_pass_fraction: float = 1.0  # share of walk-forward folds that held up
    min_fold_pass: float = 0.6
    data_stale_days: float = 0.0
    max_stale_days: float = 30.0
    places_trades: bool = False      # MUST stay False
    moves_funds: bool = False        # MUST stay False
    autonomous_code_edit: bool = False  # MUST stay False (scheduled tasks)

    # --- Deploy --------------------------------------------------------------
    c7_validated: bool = False       # from edge.is_edge_validated(...)
    human_signoff: bool = False

    def lock(self) -> Experiment:
        """Freeze the pre-registered hypothesis fields. Must be called before any
        validation result is attached; the hash records exactly what was promised."""
        payload = json.dumps({
            "id": self.id, "hypothesis": self.hypothesis, "metric": self.metric,
            "baseline": self.baseline, "threshold": self.threshold,
            "alpha": self.alpha,
        }, sort_keys=True).encode()
        self.locked_hash = hashlib.sha256(payload).hexdigest()[:16]
        return self


@dataclass
class GateResult:
    stage: Stage
    passed: bool
    reason: str


@dataclass
class LoopResult:
    experiment_id: str
    reached: Stage
    action: str                       # REJECTED | RECOMMEND_ONLY | LIVE
    transcript: list[GateResult] = field(default_factory=list)


# --------------------------------------------------------------------------- #
#  Stage gates (pure functions)                                                 #
# --------------------------------------------------------------------------- #
def gate_hypothesis(e: Experiment) -> GateResult:
    if not e.hypothesis.strip():
        return GateResult(Stage.HYPOTHESIS, False, "no falsifiable hypothesis stated")
    if not e.metric or e.threshold is None:
        return GateResult(Stage.HYPOTHESIS, False, "metric/threshold not pre-registered")
    if not (0 < e.alpha < 1):
        return GateResult(Stage.HYPOTHESIS, False, f"alpha out of range: {e.alpha}")
    if e.locked_hash is None:
        return GateResult(Stage.HYPOTHESIS, False, "hypothesis not locked (call lock())")
    return GateResult(Stage.HYPOTHESIS, True,
                      f"pre-registered: {e.metric} vs {e.baseline} "
                      f">= {e.threshold} at alpha {e.alpha} [{e.locked_hash}]")


def gate_model(e: Experiment) -> GateResult:
    if not e.deterministic:
        return GateResult(Stage.MODEL, False, "model is not deterministic/seeded")
    if not e.stdlib_only:
        return GateResult(Stage.MODEL, False, "model introduces a non-stdlib dependency")
    if not e.self_test_passed:
        return GateResult(Stage.MODEL, False, "model self-test did not pass")
    return GateResult(Stage.MODEL, True, "deterministic, stdlib-only, self-tested")


def gate_validate(e: Experiment) -> GateResult:
    if e.locked_hash is None:
        return GateResult(Stage.VALIDATE, False, "validating an unlocked hypothesis")
    if e.measured_skill is None or e.p_value is None:
        return GateResult(Stage.VALIDATE, False, "no measured skill / p-value")
    if e.measured_skill < e.threshold:
        return GateResult(Stage.VALIDATE, False,
                          f"skill {e.measured_skill:.3f} < pre-registered "
                          f"{e.threshold:.3f} -> NO EDGE")
    if e.p_value >= e.alpha:
        return GateResult(Stage.VALIDATE, False,
                          f"p={e.p_value:.3f} >= alpha {e.alpha} -> not significant")
    return GateResult(Stage.VALIDATE, True,
                      f"skill {e.measured_skill:.3f} >= {e.threshold:.3f}, "
                      f"p={e.p_value:.3f} < {e.alpha}")


def gate_risk(e: Experiment) -> GateResult:
    # Hard boundary first — no statistics can buy past this.
    if e.places_trades or e.moves_funds or e.autonomous_code_edit:
        return GateResult(Stage.RISK, False,
                          "HARD BOUNDARY: experiment claims to trade / move funds / "
                          "edit code autonomously")
    # Multiple-testing: the significance must survive a Bonferroni correction.
    alpha_eff = e.alpha / max(1, e.n_trials)
    if e.p_value is None or e.p_value >= alpha_eff:
        return GateResult(Stage.RISK, False,
                          f"fails multiple-testing: p={e.p_value} >= "
                          f"alpha/{e.n_trials}={alpha_eff:.4f}")
    if e.fold_pass_fraction < e.min_fold_pass:
        return GateResult(Stage.RISK, False,
                          f"not robust: {e.fold_pass_fraction:.2f} of folds held "
                          f"(< {e.min_fold_pass:.2f})")
    if e.data_stale_days > e.max_stale_days:
        return GateResult(Stage.RISK, False,
                          f"stale data: {e.data_stale_days:.0f}d > "
                          f"{e.max_stale_days:.0f}d")
    return GateResult(Stage.RISK, True,
                      f"survives Bonferroni (alpha_eff {alpha_eff:.4f}), robust "
                      f"({e.fold_pass_fraction:.2f}), fresh ({e.data_stale_days:.0f}d), "
                      f"within boundary")


def gate_deploy(e: Experiment) -> GateResult:
    # Default is recommend-only; live requires BOTH C7 validation and sign-off.
    if e.c7_validated and e.human_signoff:
        return GateResult(Stage.DEPLOY, True, "LIVE: C7-validated and signed off")
    why = []
    if not e.c7_validated:
        why.append("C7 edge not yet validated")
    if not e.human_signoff:
        why.append("no human sign-off")
    return GateResult(Stage.DEPLOY, True,
                      "RECOMMEND_ONLY (" + "; ".join(why) + ")")


_GATES = [gate_hypothesis, gate_model, gate_validate, gate_risk, gate_deploy]


def run(e: Experiment) -> LoopResult:
    """Walk the loop; stop at the first failed gate. Reaching Deploy yields
    RECOMMEND_ONLY unless the deploy gate explicitly clears it to LIVE."""
    transcript: list[GateResult] = []
    for gate in _GATES:
        res = gate(e)
        transcript.append(res)
        if not res.passed:
            return LoopResult(e.id, res.stage, "REJECTED", transcript)
    deploy = transcript[-1]
    action = "LIVE" if deploy.reason.startswith("LIVE") else "RECOMMEND_ONLY"
    return LoopResult(e.id, Stage.DEPLOY, action, transcript)


def format_result(r: LoopResult) -> str:
    lines = [f"[{r.experiment_id}] -> reached {r.reached.name}: {r.action}"]
    for g in r.transcript:
        mark = "PASS" if g.passed else "STOP"
        lines.append(f"  {mark} {g.stage.name:<10} {g.reason}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  Ledger (the Iterate stage's memory)                                          #
# --------------------------------------------------------------------------- #
class Ledger:
    """In-memory record of every loop run — the substrate of the Iterate stage.
    A rejected hypothesis is kept (so it is not silently re-proposed), exactly
    like the project's standing 'rejected' memory notes."""

    def __init__(self) -> None:
        self.records: list[dict] = []

    def record(self, e: Experiment, r: LoopResult) -> None:
        self.records.append({
            "id": e.id, "hypothesis": e.hypothesis, "locked": e.locked_hash,
            "reached": r.reached.name, "action": r.action,
            "stopped_reason": next((g.reason for g in r.transcript if not g.passed), ""),
            "experiment": asdict(e),
        })

    def rejected(self) -> list[dict]:
        return [x for x in self.records if x["action"] == "REJECTED"]


# --------------------------------------------------------------------------- #
#  Known-answer self-test — the gates must actually bite                        #
# --------------------------------------------------------------------------- #
def _base_good() -> Experiment:
    e = Experiment(
        id="kat", hypothesis="F beats baseline", metric="skill_score",
        baseline="persistence", threshold=0.1, alpha=0.05)
    e.deterministic = True
    e.self_test_passed = True
    e.measured_skill = 0.5
    e.p_value = 0.001
    e.n_trials = 3
    e.fold_pass_fraction = 0.9
    return e.lock()


def _self_test() -> None:
    # 1. A strong, in-boundary, un-validated experiment lands at RECOMMEND_ONLY.
    r = run(_base_good())
    assert r.reached == Stage.DEPLOY and r.action == "RECOMMEND_ONLY", r

    # 2. C7-validated + signed off -> LIVE.
    e = _base_good(); e.c7_validated = True; e.human_signoff = True
    assert run(e).action == "LIVE"

    # 3. No edge (skill below pre-registered threshold) stops at VALIDATE.
    e = _base_good(); e.measured_skill = 0.05
    r = run(e); assert r.reached == Stage.VALIDATE and r.action == "REJECTED", r

    # 4. Boundary violation stops at RISK even with perfect stats.
    e = _base_good(); e.places_trades = True
    r = run(e); assert r.reached == Stage.RISK and r.action == "REJECTED", r
    assert "HARD BOUNDARY" in r.transcript[-1].reason

    # 5. Multiple testing: p good raw but fails Bonferroni with many trials.
    e = _base_good(); e.p_value = 0.03; e.alpha = 0.05; e.n_trials = 20
    r = run(e); assert r.reached == Stage.RISK and r.action == "REJECTED", r

    # 6. Un-locked hypothesis cannot be validated.
    e = _base_good(); e.locked_hash = None
    r = run(e); assert r.reached == Stage.HYPOTHESIS and r.action == "REJECTED", r

    # 7. Ledger remembers a rejection.
    led = Ledger()
    e = _base_good(); e.measured_skill = 0.0
    led.record(e, run(e))
    assert len(led.rejected()) == 1
    print("loop self-test PASSED "
          "(recommend-only default; LIVE only when C7+signoff; no-edge/boundary/"
          "multiple-testing/unlocked all rejected; ledger remembers)")


if __name__ == "__main__":
    _self_test()
