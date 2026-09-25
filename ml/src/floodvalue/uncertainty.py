"""Uncertainty measures and the check that they are higher where the method errs."""

from __future__ import annotations

import numpy as np
from sklearn.metrics import roc_auc_score

from floodvalue.calibration import subsample

MAX_AUROC_PIXELS = 2_000_000
DECILES = 10
EPS = 1e-7


def binary_entropy(prob: np.ndarray) -> np.ndarray:
    """Entropy in bits of a Bernoulli(p); 1 at p = 0.5, 0 at p in {0, 1}."""
    p = np.clip(prob, EPS, 1 - EPS)
    return (-(p * np.log2(p) + (1 - p) * np.log2(1 - p))).astype(np.float32)


def error_detection(uncertainty: np.ndarray, error: np.ndarray, seed: int = 0) -> dict:
    """AUROC of the uncertainty as a detector of wrong pixels, and error rate by uncertainty decile."""
    index = subsample(uncertainty.size, MAX_AUROC_PIXELS, seed)
    u, e = uncertainty[index], error[index].astype(bool)
    auroc = float(roc_auc_score(e, u)) if 0 < e.sum() < e.size else None
    edges = np.quantile(u, np.linspace(0, 1, DECILES + 1))
    which = np.clip(np.searchsorted(edges[1:-1], u, side="right"), 0, DECILES - 1)
    rates = [float(e[which == d].mean()) if np.any(which == d) else None for d in range(DECILES)]
    return {
        "auroc": auroc,
        "error_rate": float(e.mean()),
        "decile_edges": [float(v) for v in edges],
        "error_rate_by_decile": rates,
        "pixels_used": int(index.size),
    }
