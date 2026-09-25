"""Load chips of one split part into memory as normalised arrays."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from floodvalue.chips import LABEL_INVALID, LABEL_WATER, Chip, read_label, read_s1
from floodvalue.features import NormStats, normalize


@dataclass(frozen=True)
class RawPart:
    names: tuple[str, ...]
    s1: tuple[np.ndarray, ...]
    labels: tuple[np.ndarray, ...]


@dataclass(frozen=True)
class TensorPart:
    """x [N, C, H, W] float32, target [N, H, W] float32, valid [N, H, W] bool."""

    names: tuple[str, ...]
    x: np.ndarray
    target: np.ndarray
    valid: np.ndarray


def load_raw(root: Path, names: Sequence[str]) -> RawPart:
    chips = [Chip.from_name(name) for name in names]
    return RawPart(
        tuple(names),
        tuple(read_s1(root, chip) for chip in chips),
        tuple(read_label(root, chip) for chip in chips),
    )


def to_tensors(raw: RawPart, stats: NormStats) -> TensorPart:
    xs, targets, valids = [], [], []
    for s1, label in zip(raw.s1, raw.labels):
        x, s1_ok = normalize(s1, stats)
        xs.append(x)
        targets.append((label == LABEL_WATER).astype(np.float32))
        valids.append(s1_ok & (label != LABEL_INVALID))
    return TensorPart(raw.names, np.stack(xs), np.stack(targets), np.stack(valids))
