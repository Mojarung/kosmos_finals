"""Segmentation metrics computed on explicit validity masks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import numpy as np


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


@dataclass(frozen=True)
class Confusion:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    def __add__(self, other: Confusion) -> Confusion:
        return Confusion(self.tp + other.tp, self.fp + other.fp, self.fn + other.fn, self.tn + other.tn)

    @property
    def n_pixels(self) -> int:
        return self.tp + self.fp + self.fn + self.tn

    @property
    def n_water(self) -> int:
        return self.tp + self.fn

    @property
    def iou(self) -> float | None:
        return _ratio(self.tp, self.tp + self.fp + self.fn)

    @property
    def f1(self) -> float | None:
        return _ratio(2 * self.tp, 2 * self.tp + self.fp + self.fn)

    @property
    def precision(self) -> float | None:
        return _ratio(self.tp, self.tp + self.fp)

    @property
    def recall(self) -> float | None:
        return _ratio(self.tp, self.tp + self.fn)

    def as_dict(self) -> dict:
        return {
            "iou": self.iou,
            "f1": self.f1,
            "precision": self.precision,
            "recall": self.recall,
            "n_pixels": self.n_pixels,
            "n_water": self.n_water,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
        }


def confusion(pred: np.ndarray, target: np.ndarray, valid: np.ndarray) -> Confusion:
    """Count TP/FP/FN/TN over pixels where ``valid`` is true."""
    pred = pred.astype(bool) & valid
    target = target.astype(bool) & valid
    tp = int(np.count_nonzero(pred & target))
    fp = int(np.count_nonzero(pred & ~target))
    fn = int(np.count_nonzero(~pred & target))
    tn = int(np.count_nonzero(valid)) - tp - fp - fn
    return Confusion(tp, fp, fn, tn)


def pooled(items: Iterable[Confusion]) -> Confusion:
    total = Confusion()
    for item in items:
        total = total + item
    return total


def summarize(per_chip: Mapping[str, Confusion]) -> dict:
    """Micro (pooled pixels) metrics plus the mean of per-chip IoU over chips with a non-empty union."""
    total = pooled(per_chip.values())
    chip_ious = [c.iou for c in per_chip.values() if c.iou is not None]
    return {
        "micro": total.as_dict(),
        "chip_mean_iou": float(np.mean(chip_ious)) if chip_ious else None,
        "chips": len(per_chip),
        "chips_with_union": len(chip_ious),
    }
