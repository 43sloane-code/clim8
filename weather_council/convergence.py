"""Mechanism-convergence layer — recommend-only verdict affirmation.

WHAT THIS IS. The headline verdict is the skill-weighted council blend. This
layer asks a *separate* question: do the project's other, independently-sourced
mechanisms — each scored only on its own held-out accuracy — cohesively
corroborate that number? When independent mechanisms land within their own
held-out error of one another, the verdict is AFFIRMED with a measured
confidence; when they diverge beyond that noise it is flagged CONTESTED and the
layer ABSTAINS from affirming rather than inventing agreement. "Convergence"
here means exactly that collaboration: each mechanism's data-backed observation
either coheres into one affirmed reading or it does not, and we report which.

WHAT THIS IS NOT. It never edits the verdict, never moves the headline, never
trades. A recommended nudge toward the consensus is gated three ways:

  GUARDRAIL 1 — lineage (one-directional). Mechanisms are grouped by lineage.
  Within a lineage the members are collapsed to a single effective estimate
  whose precision is *capped at the best member's* — correlated mechanisms (the
  council blend and the naive average are both functions of the same NWP
  forecasts) cannot sum their evidence, they can only stand in for one. Shared
  lineage can therefore only ever DISCOUNT corroboration, never inflate it. The
  affirmation confidence is a vote across *independent lineages*, so two
  same-lineage mechanisms agreeing is not mistaken for independent support.

  GUARDRAIL 2 — significance gating. A nudge is surfaced only when it exceeds
  both an operational floor and the headline's own standard error, so the
  recommended position cannot thrash on noise from one run to the next.

  GUARDRAIL 3 — C7 gate. Even a significant, well-corroborated nudge is only
  ever *allowed* to move the headline once C7 realized-outcome calibration has
  validated a real edge (`c7_validated`). Until C7 earns that right every output
  here is annotation for human review; `allowed_to_move` stays False.

Everything is closed-form and deterministic. The per-mechanism scores are the
real held-out MAEs the walk-forward backtest already produced — not fitted,
bootstrapped, or asserted here — so the layer measures the existing mechanisms
against each other and never manufactures a number.
"""

from __future__ import annotations

__all__ = [
    'Mechanism', 'ConvergenceInputs', 'MechanismScore', 'LineageEstimate', 'Convergence',
    'score_mechanisms', 'converge', 'report_lines'
]

import math
from dataclasses import dataclass, field

EPS = 1e-9
# A nudge below this is not worth surfacing — it sits inside the rounding the
# verdict already reports and well inside any settlement grain. Operational, not
# a performance knob.
NUDGE_FLOOR_C = 0.3
# Significance gate: the consensus must pull the headline by at least this many
# standard errors of the headline's own held-out residuals before we call the
# nudge meaningful. One SE is a deliberately soft gate paired with the hard floor
# above; together they stop run-to-run weight thrash.
Z_GATE = 1.0
# Below this /100 the independent lineages disagree by more than their own noise:
# the verdict is CONTESTED and we abstain from affirming rather than invent a
# consensus.
AFFIRM_MIN = 50.0
# Need at least two genuinely independent lineages to claim corroboration at all.
MIN_INDEP_LINEAGES = 2
# A mechanism needs at least this many held-out days behind its MAE to be trusted
# as a scorer (mirrors the project's small-sample caution elsewhere).
MIN_N = 5


@dataclass(frozen=True)
class Mechanism:
    """One verdict-forming mechanism's live estimate of the target quantity,
    paired with its OWN held-out accuracy. `lineage` groups mechanisms that share
    inputs (and therefore correlate) so they cannot double-count as independent
    corroboration."""
    name: str
    lineage: str
    estimate_c: float       # this mechanism's live point estimate (°C)
    mae_c: float            # its held-out mean absolute error (°C), its proper score
    n: int                  # held-out days behind that MAE


@dataclass(frozen=True)
class ConvergenceInputs:
    """Everything `converge()` needs for one quantity, gathered at verdict-build
    time (where the live estimates and the held-out residuals are both in hand).
    Kept separate from the decision so the C7 gate can be applied later, by the
    caller that knows the live realized-outcome status."""
    quantity: str
    headline_c: float
    mechanisms: tuple[Mechanism, ...]
    residual_spread_c: float | None
    n_resid: int

    def decide(self, c7_validated: bool = False) -> "Convergence":
        return converge(self.quantity, self.headline_c, list(self.mechanisms),
                        self.residual_spread_c, self.n_resid, c7_validated)


@dataclass(frozen=True)
class MechanismScore:
    name: str
    lineage: str
    estimate_c: float
    mae_c: float
    n: int
    score: float            # /100 reliability, inverse-MSE relative to the field
    usable: bool            # enough held-out days + finite MAE to count


@dataclass(frozen=True)
class LineageEstimate:
    """A whole lineage collapsed to one effective reading (guardrail 1)."""
    lineage: str
    estimate_c: float       # precision-weighted mean of the lineage's members
    eff_mae_c: float        # the BEST member's MAE — the one-directional cap
    members: tuple[str, ...]


@dataclass(frozen=True)
class Convergence:
    quantity: str                       # "high" or "low"
    headline_c: float
    affirmed_c: float | None            # inverse-variance consensus across lineages
    affirmation: float | None           # /100 cross-lineage agreement (noise-normalized)
    band_c: float | None                # ± half-width of the convergence band (°C)
    nudge_c: float | None               # affirmed − headline (None if abstaining)
    significant: bool                   # nudge beyond floor AND headline SE
    allowed_to_move: bool               # significant AND c7_validated (never True until C7)
    status: str                         # AFFIRMED | AFFIRMED_NUDGE | CONTESTED | ABSTAIN
    independent_lineages: int
    scores: tuple[MechanismScore, ...]
    lineages: tuple[LineageEstimate, ...] = field(default_factory=tuple)
    note: str = ""


def _precision(mae: float) -> float:
    """Inverse-variance weight. A mechanism half as wrong gets four times the say
    — the same skill-weighting principle the council blend itself uses."""
    return 1.0 / (max(mae, EPS) ** 2)


def score_mechanisms(mechs: list[Mechanism]) -> list[MechanismScore]:
    """Score each mechanism /100 from its held-out MAE, relative to the most
    precise mechanism in the field (best = 100). A mechanism with too few held-out
    days or a non-finite MAE is marked unusable and scored 0 — it is shown for
    transparency but never counts toward corroboration."""
    usable = [m for m in mechs
              if m.mae_c is not None and math.isfinite(m.mae_c) and m.n >= MIN_N]
    best = max((_precision(m.mae_c) for m in usable), default=0.0)
    out: list[MechanismScore] = []
    for m in mechs:
        ok = (m in usable) and best > 0
        score = (100.0 * _precision(m.mae_c) / best) if ok else 0.0
        out.append(MechanismScore(
            name=m.name, lineage=m.lineage, estimate_c=m.estimate_c,
            mae_c=m.mae_c, n=m.n, score=round(score, 1), usable=ok))
    return out


def _collapse_lineages(scores: list[MechanismScore]) -> list[LineageEstimate]:
    """Guardrail 1, one-directional. Collapse each lineage's usable members to a
    single effective reading represented by its BEST member — the lowest-MAE one's
    estimate AND its MAE. Correlated mechanisms therefore stand in for exactly one,
    and the lineage can never do better than its best member nor be pulled toward a
    worse sibling (the council blend is not dragged back toward the naive average
    it was built to beat). Shared lineage can only ever discount, never inflate.
    Non-best members stay on the scoreboard for transparency but move nothing."""
    groups: dict[str, list[MechanismScore]] = {}
    for s in scores:
        if s.usable:
            groups.setdefault(s.lineage, []).append(s)
    out: list[LineageEstimate] = []
    for lineage, members in groups.items():
        best = min(members, key=lambda m: m.mae_c)   # best member represents the lineage
        out.append(LineageEstimate(
            lineage=lineage, estimate_c=best.estimate_c, eff_mae_c=best.mae_c,
            members=tuple(m.name for m in members)))
    return out


def converge(quantity: str, headline_c: float, mechs: list[Mechanism],
             residual_spread_c: float | None, n_resid: int,
             c7_validated: bool = False) -> Convergence:
    """Reconcile the mechanisms into an affirmed reading (recommend-only).

    residual_spread_c / n_resid: the headline's OWN held-out residual spread and
    count, used to size the significance gate (SE = spread/√n).
    c7_validated: whether C7 realized-outcome calibration has validated a real
    edge. Only then may a significant nudge be `allowed_to_move`; default False.
    """
    scores = tuple(score_mechanisms(mechs))
    lineages = tuple(_collapse_lineages(list(scores)))
    n_indep = len(lineages)

    # ABSTAIN: not enough independent lineages to claim corroboration.
    if n_indep < MIN_INDEP_LINEAGES:
        return Convergence(
            quantity=quantity, headline_c=headline_c, affirmed_c=None,
            affirmation=None, band_c=None, nudge_c=None, significant=False,
            allowed_to_move=False, status="ABSTAIN", independent_lineages=n_indep,
            scores=scores, lineages=lineages,
            note=(f"only {n_indep} independent mechanism lineage(s) had enough "
                  f"held-out support — too few to affirm; the headline stands on "
                  f"its own backtest."))

    # Inverse-variance consensus ACROSS lineages (each lineage's effective
    # precision). This is the best single estimate; the council lineage, being the
    # most precise, dominates it — exactly as it should.
    wsum = sum(_precision(le.eff_mae_c) for le in lineages)
    consensus = sum(_precision(le.eff_mae_c) * le.estimate_c for le in lineages) / wsum
    band = 1.0 / math.sqrt(wsum)    # SE of the inverse-variance combination (°C)

    # Affirmation: a DEMOCRATIC vote across independent lineages — each lineage's
    # standardized distance to the consensus (in units of its own held-out error),
    # equally weighted so a reliable-but-lonely mechanism cannot drown out the
    # independents. exp(−½·meanZ²) is 100 when every lineage sits on the consensus
    # and decays smoothly as they pull apart beyond their noise.
    zs = [(le.estimate_c - consensus) / max(le.eff_mae_c, EPS) for le in lineages]
    mean_z2 = sum(z * z for z in zs) / len(zs)
    affirmation = 100.0 * math.exp(-0.5 * mean_z2)

    nudge = consensus - headline_c
    se = (residual_spread_c / math.sqrt(n_resid)
          if residual_spread_c is not None and n_resid > 0 else None)
    threshold = max(NUDGE_FLOOR_C, (Z_GATE * se) if se is not None else NUDGE_FLOOR_C)
    significant = abs(nudge) >= threshold

    if affirmation < AFFIRM_MIN:
        status = "CONTESTED"
        note = (f"independent mechanisms diverge beyond their own held-out error "
                f"(affirmation {affirmation:.0f}/100); the headline is a genuine "
                f"forecast signal NOT corroborated by the baselines — treat as "
                f"lower-confidence / event-driven, not affirmed.")
        nudge_out = None
        significant = False
    elif significant:
        status = "AFFIRMED_NUDGE"
        note = (f"mechanisms cohere (affirmation {affirmation:.0f}/100) and pull "
                f"the headline by {nudge:+.2f} °C, beyond its {threshold:.2f} °C "
                f"significance floor — RECOMMENDATION only.")
        nudge_out = round(nudge, 2)
    else:
        status = "AFFIRMED"
        note = (f"mechanisms cohere (affirmation {affirmation:.0f}/100) and sit "
                f"within ±{threshold:.2f} °C of the headline — the verdict is "
                f"affirmed; no nudge warranted.")
        nudge_out = round(nudge, 2)

    allowed = bool(status == "AFFIRMED_NUDGE" and significant and c7_validated)

    return Convergence(
        quantity=quantity, headline_c=headline_c,
        affirmed_c=round(consensus, 2), affirmation=round(affirmation, 1),
        band_c=round(band, 2), nudge_c=nudge_out, significant=significant,
        allowed_to_move=allowed, status=status, independent_lineages=n_indep,
        scores=scores, lineages=lineages, note=note)


def report_lines(conv_high: Convergence | None,
                 conv_low: Convergence | None,
                 c7_validated: bool = False) -> list[str]:
    """Human-readable, recommend-only render of the convergence layer."""
    convs = [c for c in (conv_high, conv_low) if c is not None]
    if not convs:
        return []
    L = ["  MECHANISM CONVERGENCE (independent corroboration — recommend-only, "
         "never moves the verdict)"]
    gate = ("C7 has validated a realized-outcome edge, so a significant nudge is "
            "ALLOWED to move the headline"
            if c7_validated else
            "C7 has NOT yet validated a realized-outcome edge, so every nudge "
            "below is annotation only and is NOT allowed to move the headline")
    L.append(f"    gate     : {gate}.")
    for c in convs:
        L.append(f"    {c.quantity.upper()} — {c.status}: {c.note}")
        # Per-mechanism scoreboard.
        for s in c.scores:
            tag = "" if s.usable else "  (unused: too few held-out days)"
            L.append(f"      {s.name:<22} {s.estimate_c:6.1f} °C   "
                     f"MAE {s.mae_c:4.2f}  score {s.score:5.1f}/100  "
                     f"[{s.lineage}]{tag}")
        if c.affirmed_c is not None:
            mv = (f"  -> would settle to {c.affirmed_c:.1f} °C if C7-validated"
                  if c.allowed_to_move else "")
            L.append(f"      consensus {c.affirmed_c:.1f} °C  (headline "
                     f"{c.headline_c:.1f} °C, band ±{c.band_c:.2f} °C, "
                     f"{c.independent_lineages} independent lineages){mv}")
    L.append("")
    return L
