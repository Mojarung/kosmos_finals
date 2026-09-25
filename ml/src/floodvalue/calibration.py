"""Probability calibration, reliability statistics and threshold selection.

Platt scaling ``p = sigmoid(a * z + b)`` is fitted on validation pixels only, where
``z`` is the mean logit of the ensemble. The binary threshold is then chosen on the
same validation pixels and frozen before any test part is scored.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import torch
from scipy.special import expit

from floodvalue.metrics import Confusion

MAX_FIT_PIXELS = 2_000_000
RELIABILITY_BINS = 15
PROB_BINS = 1000
THRESHOLD_STEP = 0.01


@dataclass(frozen=True)
class Platt:
    a: float
    b: float

    def apply(self, logits: np.ndarray) -> np.ndarray:
        return expit(self.a * logits + self.b).astype(np.float32)

    def as_dict(self) -> dict:
        return asdict(self)


def subsample(n: int, max_items: int, seed: int) -> np.ndarray:
    if n <= max_items:
        return np.arange(n)
    return np.sort(np.random.default_rng(seed).choice(n, size=max_items, replace=False))


def fit_platt(logits: np.ndarray, target: np.ndarray, seed: int = 0) -> Platt:
    """Minimise binary cross-entropy of sigmoid(a*z + b) with L-BFGS on a pixel subsample."""
    index = subsample(logits.size, MAX_FIT_PIXELS, seed)
    z = torch.as_tensor(logits[index], dtype=torch.float64)
    y = torch.as_tensor(target[index], dtype=torch.float64)
    params = torch.tensor([1.0, 0.0], dtype=torch.float64, requires_grad=True)
    optimizer = torch.optim.LBFGS([params], lr=0.5, max_iter=200, line_search_fn="strong_wolfe")

    def closure():
        optimizer.zero_grad()
        loss = torch.nn.functional.binary_cross_entropy_with_logits(params[0] * z + params[1], y)
        loss.backward()
        return loss

    optimizer.step(closure)
    a, b = params.detach().tolist()
    return Platt(float(a), float(b))


def reliability(prob: np.ndarray, target: np.ndarray, n_bins: int = RELIABILITY_BINS) -> dict:
    """Equal-width reliability table, expected calibration error and Brier score."""
    target = target.astype(np.float64)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    which = np.clip(np.digitize(prob, edges[1:-1]), 0, n_bins - 1)
    counts = np.bincount(which, minlength=n_bins)
    sum_p = np.bincount(which, weights=prob, minlength=n_bins)
    sum_y = np.bincount(which, weights=target, minlength=n_bins)
    bins = [
        {
            "lo": float(edges[i]),
            "hi": float(edges[i + 1]),
            "count": int(counts[i]),
            "mean_p": float(sum_p[i] / counts[i]) if counts[i] else None,
            "water_fraction": float(sum_y[i] / counts[i]) if counts[i] else None,
        }
        for i in range(n_bins)
    ]
    n = int(counts.sum())
    ece = float(np.abs(sum_y - sum_p).sum() / n) if n else None
    brier = float(np.mean((prob - target) ** 2)) if n else None
    return {"bins": bins, "ece": ece, "brier": brier, "n_pixels": n}


def prob_threshold_curve(prob: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, list[Confusion]]:
    """Confusion for thresholds 0.01..0.99 (water if p >= t), exact at the 1e-3 histogram resolution."""
    edges = np.linspace(0.0, 1.0, PROB_BINS + 1)
    target = target.astype(bool)
    water, _ = np.histogram(prob[target], bins=edges)
    dry, _ = np.histogram(prob[~target], bins=edges)
    water_at_or_above = np.cumsum(water[::-1])[::-1]
    dry_at_or_above = np.cumsum(dry[::-1])[::-1]
    thresholds = np.round(np.arange(THRESHOLD_STEP, 1.0, THRESHOLD_STEP), 2)
    curve = []
    for t in thresholds:
        k = int(round(t * PROB_BINS))
        tp, fp = int(water_at_or_above[k]), int(dry_at_or_above[k])
        curve.append(Confusion(tp, fp, int(water.sum()) - tp, int(dry.sum()) - fp))
    return thresholds, curve


def best_prob_threshold(prob: np.ndarray, target: np.ndarray) -> tuple[float, Confusion]:
    thresholds, curve = prob_threshold_curve(prob, target)
    best = int(np.argmax([c.iou or 0.0 for c in curve]))
    return float(thresholds[best]), curve[best]
