"""Check the shipped normalisation constants against the train part.

All five v1 members ship the same ``norm_stats.json``. Its clip bounds are the 0.1 and
99.9 percentiles of the valid S1 pixels of the 252 train chips, but its mean and std are
not that part's mean and std. Until 26.09 ``fit_norm_stats`` reduced a float32 [C, N]
array that boolean indexing had returned F-ordered, so numpy summed the N pixels of each
channel with one float32 running sum, and the sum stopped growing long before the last
pixel. The model was trained and is served with the shipped constants, so inference is
self-consistent: they are a frozen affine transform, not statistics.

``norm_check`` recomputes the statistics with the fixed ``fit_norm_stats`` (float64),
replays the old float32 reduction to show it gives the shipped numbers, and states what
0 after normalisation, the value nodata pixels get, means in dB.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np

from floodvalue.chips import Chip, read_s1
from floodvalue.features import CHANNEL_SETS, NormStats, channel_pixels, fit_norm_stats

CHUNK = 1 << 20


def float32_running_sum(values: np.ndarray, chunk: int = CHUNK) -> np.float32:
    """Left-to-right sum in one float32 accumulator; the chunks only bound memory."""
    total = np.zeros(1, dtype=np.float32)
    for start in range(0, values.size, chunk):
        total = np.cumsum(np.concatenate((total, values[start:start + chunk])), dtype=np.float32)[-1:]
    return total[0]


def float32_running_mean_std(values: np.ndarray, chunk: int = CHUNK) -> tuple[np.float32, np.float32]:
    """What ``mean`` and ``std`` returned for one row of an F-ordered float32 [C, N] array.

    numpy sums along the strided axis pixel by pixel in float32, divides by the count in
    float64 and stores the quotient as float32; ``std`` repeats that for the squared
    float32 deviations from that mean and takes a float32 square root.
    """
    count = np.float64(values.size)
    mean = np.float32(np.float64(float32_running_sum(values, chunk)) / count)
    squares = np.zeros(1, dtype=np.float32)
    for start in range(0, values.size, chunk):
        deviation = values[start:start + chunk] - mean
        squares = np.cumsum(np.concatenate((squares, deviation * deviation)), dtype=np.float32)[-1:]
    variance = np.float32(np.float64(squares[0]) / count)
    return mean, np.sqrt(variance)


class LazyChips(Sequence):
    """S1 chips read from disk on access, so a whole split part never sits in memory."""

    def __init__(self, root: Path, names: Sequence[str]) -> None:
        self.root, self.names = root, list(names)

    def __len__(self) -> int:
        return len(self.names)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return LazyChips(self.root, self.names[index])
        return read_s1(self.root, Chip.from_name(self.names[index]))


def _floats(values) -> list[float]:
    return [float(v) for v in values]


def norm_check(chips: Sequence[np.ndarray], shipped: Sequence[NormStats]) -> dict:
    """Shipped constants against the float64 statistics of the same pixels, plus the old replay."""
    stats = shipped[0]
    if any(member != stats for member in shipped):
        raise ValueError("members ship different normalisation constants")
    channels = CHANNEL_SETS[stats.channel_set]
    fixed = fit_norm_stats(chips, stats.channel_set)
    replay_mean, replay_std, darker, pixels_count = [], [], [], 0
    for channel in range(len(channels)):
        pixels = channel_pixels(chips, stats.channel_set, channel)
        pixels_count = int(pixels.size)
        np.clip(pixels, np.float32(stats.low[channel]), np.float32(stats.high[channel]), out=pixels)
        mean, std = float32_running_mean_std(pixels)
        replay_mean.append(float(mean))
        replay_std.append(float(std))
        darker.append(round(float(np.count_nonzero(pixels < stats.mean[channel])) / pixels.size, 4))
        del pixels
    z = [(stats.mean[i] - fixed.mean[i]) / fixed.std[i] for i in range(len(channels))]
    return {
        "channel_set": stats.channel_set,
        "channels": list(channels),
        "train_chips": len(chips),
        "valid_pixels_per_channel": pixels_count,
        "shipped": {"members": len(shipped), "identical_in_all_members": True, **stats.as_dict()},
        "recomputed_float64": {key: _floats(getattr(fixed, key)) for key in ("low", "high", "mean", "std")},
        "clip_bounds_equal_shipped": fixed.low == stats.low and fixed.high == stats.high,
        "old_float32_reduction_replayed": {
            "mean": replay_mean, "std": replay_std,
            "equal_to_shipped": replay_mean == _floats(stats.mean) and replay_std == _floats(stats.std),
            "rule": "clip to the shipped bounds, one float32 running sum per channel in chip order, "
                    "quotient by the pixel count in float64 stored as float32 (numpy mean/std along the "
                    "strided axis of an F-ordered float32 [C, N] array)",
        },
        "nodata": {
            "normalized_value": 0.0,
            "db": _floats(stats.mean),
            "z_in_true_train_units": [round(v, 3) for v in z],
            "share_of_train_pixels_darker": darker,
            "note": "invalid S1 pixels enter the model as 0, which the shipped transform maps from "
                    "the shipped mean in dB: bright backscatter, not the train mean",
        },
    }
