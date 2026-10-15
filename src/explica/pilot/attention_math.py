"""Toy numbers for the pilot, computed (not typed by hand) and checked in tests (A07).

Setting: three tokens, d_k = d_v = 2, single head, causal mask allows the last
position to attend to positions 1..3 (itself included). Values are rounded to
two decimals only for display; the sum-to-one check runs on exact floats and
the displayed weights also sum to 1.00 at two decimals (no visible paradox).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

TOKENS = ["El", "gato", "duerme"]
D_K = 2
Q = [1.0, 1.0]  # query of position 3 ("duerme")
K = [[1.0, 0.0], [0.5, 1.0], [0.0, 1.0]]  # keys of positions 1..3
V = [[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]]  # values of positions 1..3


@dataclass
class AttentionStep:
    tokens: list[str]
    q: list[float]
    k: list[list[float]]
    v: list[list[float]]
    d_k: int
    scores: list[float] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)
    output: list[float] = field(default_factory=list)

    @property
    def scale(self) -> float:
        return math.sqrt(self.d_k)


def dot(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError("dimension mismatch")
    return sum(x * y for x, y in zip(a, b, strict=True))


def softmax(xs: list[float]) -> list[float]:
    m = max(xs)
    ex = [math.exp(x - m) for x in xs]
    s = sum(ex)
    return [e / s for e in ex]


def compute(tokens=TOKENS, q=Q, k=K, v=V, d_k=D_K) -> AttentionStep:
    if not (len(tokens) == len(k) == len(v)):
        raise ValueError("tokens, keys and values must have the same length")
    if any(len(row) != d_k for row in k) or len(q) != d_k:
        raise ValueError("q and every k must have d_k components")
    step = AttentionStep(list(tokens), list(q), [list(r) for r in k], [list(r) for r in v], d_k)
    step.scores = [dot(q, ki) / step.scale for ki in k]
    step.weights = softmax(step.scores)
    step.output = [sum(step.weights[i] * v[i][j] for i in range(len(v))) for j in range(len(v[0]))]
    return step


def rounded(xs: list[float], decimals: int = 2) -> list[float]:
    return [round(x, decimals) for x in xs]
