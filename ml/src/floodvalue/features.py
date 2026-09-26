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


def channel_pixels(s1_chips: Sequence[np.ndarray], channel_set: str, channel: int) -> np.ndarray:
    """Valid pixels of one channel from every chip, in chip order, as one contiguous float32 vector."""
    parts = [build_channels(s1, channel_set)[channel][s1_valid(s1)] for s1 in s1_chips]
    return np.concatenate(parts) if parts else np.empty(0, dtype=np.float32)


def fit_norm_stats(s1_chips: Sequence[np.ndarray], channel_set: str) -> NormStats:
    """Percentile clip bounds, then mean/std of clipped valid pixels, per channel.

    One channel at a time as a contiguous vector, with float64 sums. Until 26.09 the sums
    ran in float32 along the strided axis of an F-ordered [C, N] array, which numpy
    accumulates pixel by pixel; on the train part that moved the mean by about 5 dB.
    """
    low, high, mean, std = [], [], [], []
    for channel in range(len(CHANNEL_SETS[channel_set])):
        pixels = channel_pixels(s1_chips, channel_set, channel)
        if pixels.size == 0:
            raise ValueError("no valid S1 pixels to fit normalisation statistics")
        # Scalar q keeps the float32 bounds that the shipped members were trained with.
        lo, hi = np.percentile(pixels, CLIP_PERCENTILES[0]), np.percentile(pixels, CLIP_PERCENTILES[1])
        np.clip(pixels, lo, hi, out=pixels)
        low.append(float(lo))
        high.append(float(hi))
        mean.append(float(pixels.mean(dtype=np.float64)))
        std.append(float(max(pixels.std(dtype=np.float64), MIN_STD)))
    return NormStats(channel_set, tuple(low), tuple(high), tuple(mean), tuple(std))


def normalize(s1: np.ndarray, stats: NormStats) -> tuple[np.ndarray, np.ndarray]:
    """Model input [C, H, W] with invalid pixels set to 0, plus the validity mask.

    0 stands for ``stats.mean`` in dB. For the shipped v1 members that is VV -5.6 / VH -8.7 dB,
    bright backscatter rather than the train mean (see ``floodvalue norm-check``).
    """
    valid = s1_valid(s1)
    x = build_channels(s1, stats.channel_set)
    low, high = np.array(stats.low)[:, None, None], np.array(stats.high)[:, None, None]
    mean, std = np.array(stats.mean)[:, None, None], np.array(stats.std)[:, None, None]
    x = (np.clip(x, low, high) - mean) / std
    x[:, ~valid] = 0.0
    return x.astype(np.float32), valid
