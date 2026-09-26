"""Input channels and normalisation for the main method.

Statistics are computed on the training part only and stored next to the weights,
so inference on any other official chip uses exactly the same transform.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass

import numpy as np

from floodvalue.chips import s1_valid

CHANNEL_SETS = {
    "vv_vh": ("VV", "VH"),
    "vv_vh_diff": ("VV", "VH", "VV-VH"),
    "s2_six": ("B2", "B3", "B4", "B8", "B11", "B12"),
}
CLIP_PERCENTILES = (0.1, 99.9)
MIN_STD = 1e-6


def build_channels(s1: np.ndarray, channel_set: str) -> np.ndarray:
    """Stack the requested channels from S1 [2, H, W] in dB; NaN stays NaN."""
    if channel_set == "s2_six":
        raise ValueError("S2 models require floodvalue.optical_cnn, not the S1 loader")
    vv, vh = s1[0], s1[1]
    available = {"VV": vv, "VH": vh, "VV-VH": vv - vh}
    return np.stack([available[name] for name in CHANNEL_SETS[channel_set]]).astype(np.float32)


@dataclass(frozen=True)
class NormStats:
    channel_set: str
    low: tuple[float, ...]
    high: tuple[float, ...]
    mean: tuple[float, ...]
    std: tuple[float, ...]

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> NormStats:
        return cls(
            data["channel_set"],
            tuple(data["low"]),
            tuple(data["high"]),
            tuple(data["mean"]),
            tuple(data["std"]),
        )


def fit_norm_stats(s1_chips: Sequence[np.ndarray], channel_set: str) -> NormStats:
    """Percentile clip bounds, then mean/std of clipped valid pixels, per channel."""
    stacks = [build_channels(s1, channel_set)[:, s1_valid(s1)] for s1 in s1_chips]
    pixels = np.concatenate(stacks, axis=1) if stacks else np.empty((len(CHANNEL_SETS[channel_set]), 0))
    if pixels.shape[1] == 0:
        raise ValueError("no valid S1 pixels to fit normalisation statistics")
    low = np.percentile(pixels, CLIP_PERCENTILES[0], axis=1)
    high = np.percentile(pixels, CLIP_PERCENTILES[1], axis=1)
    clipped = np.clip(pixels, low[:, None], high[:, None])
    return NormStats(
        channel_set,
        tuple(float(v) for v in low),
        tuple(float(v) for v in high),
        tuple(float(v) for v in clipped.mean(axis=1)),
        tuple(float(max(v, MIN_STD)) for v in clipped.std(axis=1)),
    )


def normalize(s1: np.ndarray, stats: NormStats) -> tuple[np.ndarray, np.ndarray]:
    """Model input [C, H, W] with invalid pixels set to 0 (the channel mean), plus the validity mask."""
    valid = s1_valid(s1)
    x = build_channels(s1, stats.channel_set)
    low, high = np.array(stats.low)[:, None, None], np.array(stats.high)[:, None, None]
    mean, std = np.array(stats.mean)[:, None, None], np.array(stats.std)[:, None, None]
    x = (np.clip(x, low, high) - mean) / std
    x[:, ~valid] = 0.0
    return x.astype(np.float32), valid
