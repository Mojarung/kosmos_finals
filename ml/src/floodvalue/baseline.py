"""Threshold baseline: water where one S1 channel (dB) is below a global threshold.

The threshold for each channel is chosen on train by maximising pooled IoU, the
channel is chosen on validation, and the result is frozen in ``baseline.json``.
Histograms make the grid search exact at the bin resolution without storing pixels.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass

import numpy as np

from floodvalue.chips import LABEL_INVALID, LABEL_WATER, s1_valid
from floodvalue.metrics import Confusion

CHANNELS = ("VV", "VH")
DB_MIN, DB_MAX, DB_STEP = -50.0, 10.0, 0.05
EDGES = np.arange(DB_MIN, DB_MAX + DB_STEP / 2, DB_STEP)


@dataclass(frozen=True)
class ThresholdModel:
    channel: str
    threshold_db: float

    def predict(self, s1: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Water mask and validity mask for an S1 array [2, H, W] in dB."""
        valid = s1_valid(s1)
        band = s1[CHANNELS.index(self.channel)]
        water = np.zeros(band.shape, dtype=bool)
        water[valid] = band[valid] < self.threshold_db
        return water, valid

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ChannelHistograms:
    """Counts of water and dry pixels per dB bin for one channel."""

    water: np.ndarray
    dry: np.ndarray

    def __add__(self, other: ChannelHistograms) -> ChannelHistograms:
        return ChannelHistograms(self.water + other.water, self.dry + other.dry)


def channel_histograms(s1: np.ndarray, label: np.ndarray, channel: int) -> ChannelHistograms:
    valid = s1_valid(s1) & (label != LABEL_INVALID)
    values = np.clip(s1[channel][valid], DB_MIN, DB_MAX - DB_STEP / 2)
    is_water = label[valid] == LABEL_WATER
    water, _ = np.histogram(values[is_water], bins=EDGES)
    dry, _ = np.histogram(values[~is_water], bins=EDGES)
    return ChannelHistograms(water.astype(np.int64), dry.astype(np.int64))


def threshold_curve(hist: ChannelHistograms) -> tuple[np.ndarray, list[Confusion]]:
    """Confusion for every candidate threshold (upper bin edge; predict water below it)."""
    tp = np.cumsum(hist.water)
    fp = np.cumsum(hist.dry)
    fn = hist.water.sum() - tp
    tn = hist.dry.sum() - fp
    thresholds = EDGES[1:]
    return thresholds, [Confusion(int(a), int(b), int(c), int(d)) for a, b, c, d in zip(tp, fp, fn, tn)]


def best_threshold(hist: ChannelHistograms) -> tuple[float, Confusion]:
    thresholds, curve = threshold_curve(hist)
    ious = np.array([c.iou or 0.0 for c in curve])
    best = int(np.argmax(ious))
    return float(round(thresholds[best], 4)), curve[best]


def accumulate(chips: Iterable[tuple[np.ndarray, np.ndarray]]) -> dict[str, ChannelHistograms]:
    totals: dict[str, ChannelHistograms] = {}
    for s1, label in chips:
        for index, channel in enumerate(CHANNELS):
            hist = channel_histograms(s1, label, index)
            totals[channel] = totals[channel] + hist if channel in totals else hist
    return totals


def evaluate_threshold(hist: ChannelHistograms, threshold_db: float) -> Confusion:
    thresholds, curve = threshold_curve(hist)
    index = int(np.argmin(np.abs(thresholds - threshold_db)))
    return curve[index]


def fit(train: dict[str, ChannelHistograms], valid: dict[str, ChannelHistograms]) -> tuple[ThresholdModel, dict]:
    """Per-channel threshold on train, channel choice on validation."""
    report: dict[str, dict] = {}
    for channel in CHANNELS:
        threshold, train_conf = best_threshold(train[channel])
        report[channel] = {
            "threshold_db": threshold,
            "train": train_conf.as_dict(),
            "valid": evaluate_threshold(valid[channel], threshold).as_dict(),
        }
    chosen = max(CHANNELS, key=lambda ch: report[ch]["valid"]["iou"] or 0.0)
    return ThresholdModel(chosen, report[chosen]["threshold_db"]), report
