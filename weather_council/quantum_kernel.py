"""Quantum-inspired kernel methods — classically EXACT, stdlib-only, deterministic.

Why this module exists (and why it is *not* a "quantum neural network")
-----------------------------------------------------------------------
The quantum-ML literature applied to weather proposes three families:

  1. Quantum Neural Networks / variational quantum circuits (QNNs) and Quantum
     GRUs (QGRUs) for meteorological time series (temperature, wind speed).
  2. Quantum kernels — encode data into a quantum state |phi(x)> and learn in the
     resulting Hilbert space via the fidelity kernel k(x,x')=|<phi(x)|phi(x')>|^2.
  3. QAOA — a variational quantum algorithm for combinatorial optimisation,
     proposed for uncertainty-/constraint-handling in forecasting workflows.

ECMWF's own position (Peter Dueben, quoted by the user) is the honest anchor
here: machine learning crossed into ECMWF's workflows only once it reached a high
technology-readiness level — ready hardware, mature libraries. *Quantum computing
has not.* There is, at the time of writing, no peer-reviewed demonstration of a
fault-tolerant quantum advantage on an operational NWP task; published QML-for-
weather results are small-scale, run on classical SIMULATORS or tiny NISQ
devices, and are typically matched or beaten by a well-tuned classical baseline.

So this module does the one thing that is both honest and verifiable here:

  * For a PRODUCT-STATE angle encoding with single-qubit RY rotations, the
    fidelity kernel factorises exactly,
        k(x, x') = |<phi(x)|phi(x')>|^2 = PROD_i cos^2( (x_i - x'_i) / 2 ),
    which is computable on a classical CPU in O(d). We implement THAT and
    backtest it on real station truth, head-to-head with classical kernels.

  * We deliberately do NOT simulate ENTANGLING feature maps (e.g. ZZ maps). The
    entangling regime is precisely where a classical machine can no longer follow
    the circuit efficiently — i.e. the only place a real quantum advantage could
    live. We cannot run it on hardware here; a classical "simulation" of it would
    be exponential AND would prove nothing about a real device. Claiming an edge
    from it would violate this project's rule that every output be data-derived
    and backtested. We measure the tractable subset and let the numbers decide.

  * QAOA is out of scope by inspection, not omission: the council's only live
    optimisation (ensemble member weighting; WEIGHT_POWER, bias method, dispersion
    thresholds) is a low-dimensional, smooth, effectively convex problem already
    solved in closed form / by exhaustive grid. QAOA targets NP-hard combinatorial
    problems; it offers nothing over the existing exact solve here, and on current
    hardware would be strictly worse. Documented so it is not re-proposed.

Everything below is pure stdlib, seeded, and self-tested.
"""
from __future__ import annotations

__all__ = [
    'fidelity_kernel', 'rbf_kernel', 'linear_kernel', 'standardize_fit',
    'standardize_apply', 'angle_encode', 'KernelRidge'
]

import math
import random


# --------------------------------------------------------------------------- #
#  Kernels                                                                      #
# --------------------------------------------------------------------------- #
def fidelity_kernel(x: list[float], y: list[float]) -> float:
    """The EXACT product-state quantum fidelity kernel for an RY angle encoding:
        k(x, y) = PROD_i cos^2( (x_i - y_i) / 2 ).
    This is |<phi(x)|phi(y)>|^2 where |phi(x)> = ⊗_i RY(x_i)|0> — a genuine
    quantum kernel, evaluated in the classically-tractable product regime.
    Inputs are angles (radians); see `angle_encode`."""
    k = 1.0
    for a, b in zip(x, y):
        c = math.cos((a - b) / 2.0)
        k *= c * c
    return k


def rbf_kernel(x: list[float], y: list[float], gamma: float) -> float:
    """Classical Gaussian RBF kernel exp(-gamma * ||x-y||^2) — the standard
    benchmark a quantum kernel must beat to justify itself."""
    s = sum((a - b) * (a - b) for a, b in zip(x, y))
    return math.exp(-gamma * s)


def linear_kernel(x: list[float], y: list[float]) -> float:
    """Plain dot-product kernel (kernel ridge with this == ridge regression)."""
    return sum(a * b for a, b in zip(x, y))


# --------------------------------------------------------------------------- #
#  Feature scaling / angle encoding                                            #
# --------------------------------------------------------------------------- #
def standardize_fit(X: list[list[float]]) -> tuple[list[float], list[float]]:
    """Per-feature (mean, std) over the training rows; std floored away from 0."""
    n = len(X)
    d = len(X[0]) if n else 0
    mean = [0.0] * d
    for row in X:
        for j in range(d):
            mean[j] += row[j]
    mean = [m / n for m in mean]
    var = [0.0] * d
    for row in X:
        for j in range(d):
            var[j] += (row[j] - mean[j]) ** 2
    std = [math.sqrt(v / n) if v > 0 else 1.0 for v in var]
    std = [s if s > 1e-9 else 1.0 for s in std]
    return mean, std


def standardize_apply(x: list[float], mean: list[float],
                      std: list[float]) -> list[float]:
    return [(v - m) / s for v, m, s in zip(x, mean, std)]


def angle_encode(z: list[float], scale: float = 1.0) -> list[float]:
    """Map a standardized feature vector to rotation angles for the fidelity
    kernel. arctan keeps every angle in (-pi/2, pi/2) so the cos^2 product never
    wraps around its period (which would alias distant points as 'similar')."""
    return [2.0 * math.atan(scale * v) for v in z]


# --------------------------------------------------------------------------- #
#  SPD linear solve (Cholesky) — no numpy                                       #
# --------------------------------------------------------------------------- #
def _cholesky(A: list[list[float]]) -> list[list[float]]:
    n = len(A)
    L = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = sum(L[i][k] * L[j][k] for k in range(j))
            if i == j:
                d = A[i][i] - s
                L[i][j] = math.sqrt(d) if d > 1e-12 else 1e-6
            else:
                L[i][j] = (A[i][j] - s) / L[j][j]
    return L


def _chol_solve(L: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    y = [0.0] * n
    for i in range(n):
        y[i] = (b[i] - sum(L[i][k] * y[k] for k in range(i))) / L[i][i]
    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (y[i] - sum(L[k][i] * x[k] for k in range(i + 1, n))) / L[i][i]
    return x


# --------------------------------------------------------------------------- #
#  Kernel ridge regression                                                      #
# --------------------------------------------------------------------------- #
class KernelRidge:
    """Closed-form kernel ridge regression: alpha = (K + lam I)^-1 y, then
    f(x*) = sum_i alpha_i k(x*, x_i). Kernel is any callable (x, y) -> float."""

    def __init__(self, kernel, lam: float = 1.0):
        self.kernel = kernel
        self.lam = lam
        self.X: list[list[float]] = []
        self.alpha: list[float] = []
        self.y_mean = 0.0

    def fit(self, X: list[list[float]], y: list[float]) -> "KernelRidge":
        n = len(X)
        self.y_mean = sum(y) / n if n else 0.0
        yc = [v - self.y_mean for v in y]           # centre the target
        K = [[self.kernel(X[i], X[j]) for j in range(n)] for i in range(n)]
        for i in range(n):
            K[i][i] += self.lam
        L = _cholesky(K)
        self.alpha = _chol_solve(L, yc)
        self.X = X
        return self

    def predict(self, x: list[float]) -> float:
        return self.y_mean + sum(
            self.alpha[i] * self.kernel(self.X[i], x)
            for i in range(len(self.X)))


# --------------------------------------------------------------------------- #
#  Self-test                                                                    #
# --------------------------------------------------------------------------- #
def _self_test() -> None:
    # 1. Fidelity kernel: identity -> 1, antipodal angle -> 0, symmetric, in [0,1].
    assert abs(fidelity_kernel([0.3, -1.1], [0.3, -1.1]) - 1.0) < 1e-12
    assert abs(fidelity_kernel([0.0], [math.pi])) < 1e-12          # cos^2(pi/2)=0
    a, b = [0.2, 0.5], [-0.4, 1.0]
    assert abs(fidelity_kernel(a, b) - fidelity_kernel(b, a)) < 1e-15
    assert 0.0 <= fidelity_kernel(a, b) <= 1.0

    # 2. Fidelity kernel equals the literal |<phi(x)|phi(y)>|^2 of the product
    #    state |phi> = ⊗ RY(theta)|0> = ⊗ (cos(theta/2), sin(theta/2)).
    def overlap_sq(x, y):
        amp = 1.0
        for tx, ty in zip(x, y):
            amp *= (math.cos(tx / 2) * math.cos(ty / 2)
                    + math.sin(tx / 2) * math.sin(ty / 2))
        return amp * amp
    for _ in range(200):
        x = [random.uniform(-3, 3) for _ in range(3)]
        y = [random.uniform(-3, 3) for _ in range(3)]
        assert abs(fidelity_kernel(x, y) - overlap_sq(x, y)) < 1e-12, "not a real fidelity kernel"

    # 3. Cholesky solve agrees with a known SPD system.
    A = [[4.0, 1.0], [1.0, 3.0]]
    b = [1.0, 2.0]
    sol = _chol_solve(_cholesky(A), b)
    # verify A @ sol == b
    r0 = A[0][0] * sol[0] + A[0][1] * sol[1]
    r1 = A[1][0] * sol[0] + A[1][1] * sol[1]
    assert abs(r0 - b[0]) < 1e-9 and abs(r1 - b[1]) < 1e-9

    # 4. KRR interpolates a smooth function it has seen (low lam, train error ~0).
    rng = random.Random(0)
    Xtr = [[rng.uniform(-1, 1)] for _ in range(25)]
    ytr = [math.sin(3 * x[0]) for x in Xtr]
    kr = KernelRidge(lambda u, v: rbf_kernel(u, v, gamma=2.0), lam=1e-6).fit(Xtr, ytr)
    err = max(abs(kr.predict(Xtr[i]) - ytr[i]) for i in range(len(Xtr)))
    assert err < 1e-2, f"KRR failed to fit training data: {err}"
    print("quantum_kernel self-test PASSED "
          "(fidelity kernel == product-state overlap^2; Cholesky solve exact; KRR fits)")


if __name__ == "__main__":
    _self_test()
