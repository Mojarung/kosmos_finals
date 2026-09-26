"""Normalisation statistics must be the float64 mean and std of the clipped train pixels.

Until 26.09 ``fit_norm_stats`` reduced ``build_channels(s1)[:, valid]``: boolean indexing
returns that [C, N] array F-ordered, so numpy summed its float32 pixels along the strided
axis with one float32 running sum. On the 252 train chips that shifted the shipped mean
from about -10.4/-17.2 dB to -5.64/-8.74 dB. Two million synthetic pixels are enough to
see the same drift in the fourth decimal; the test stays under a second.
"""

import numpy as np

from floodvalue.chips import s1_valid
from floodvalue.features import build_channels, fit_norm_stats
from floodvalue.norm_check import float32_running_mean_std


def _chips(count: int = 8, size: int = 512) -> list[np.ndarray]:
    """dB chips shaped like Sen1Floods11: VV around -10.4, VH around -17.2, a NaN stripe."""
    rng = np.random.default_rng(0)
    chips = []
    for _ in range(count):
        s1 = np.stack([rng.normal(-10.4, 4.0, (size, size)), rng.normal(-17.2, 4.7, (size, size))])
        s1 = s1.astype(np.float32)
        s1[:, :8] = np.nan
        chips.append(s1)
    return chips


def _float64_reference(chips: list[np.ndarray]) -> tuple[np.ndarray, ...]:
    """The same float32 clip bounds, then mean and std with float64 sums over each channel."""
    low, high, mean, std = [], [], [], []
    for channel in range(2):
        values = np.concatenate([s1[channel][s1_valid(s1)] for s1 in chips])
        lo, hi = np.percentile(values, 0.1), np.percentile(values, 99.9)
        clipped = np.clip(values.astype(np.float64), lo, hi)
        low.append(lo)
        high.append(hi)
        mean.append(clipped.mean())
        std.append(clipped.std())
    return tuple(np.array(v) for v in (low, high, mean, std))


def test_float32_mean_of_the_f_ordered_pixel_array_drifts():
    """The old expression: F-ordered float32 [C, N], reduced along N in one float32 running sum."""
    chips = _chips()
    _, _, mean, _ = _float64_reference(chips)
    pixels = np.concatenate([build_channels(s1, "vv_vh")[:, s1_valid(s1)] for s1 in chips], axis=1)
    pixels = np.clip(pixels, *(np.percentile(pixels, q, axis=1)[:, None] for q in (0.1, 99.9)))

    assert pixels.dtype == np.float32 and pixels.flags.f_contiguous and not pixels.flags.c_contiguous
    assert np.abs(pixels.mean(axis=1) - mean).max() > 1e-4


def test_replay_of_the_old_float32_reduction_is_bit_exact():
    """norm-check replays the old sums chunk by chunk; the chunk edges must not change a bit."""
    chips = _chips(count=2)
    pixels = np.concatenate([build_channels(s1, "vv_vh")[:, s1_valid(s1)] for s1 in chips], axis=1)
    pixels = np.clip(pixels, *(np.percentile(pixels, q, axis=1)[:, None] for q in (0.1, 99.9)))
    old_mean, old_std = pixels.mean(axis=1), pixels.std(axis=1)

    for channel in range(2):
        row = np.ascontiguousarray(pixels[channel])
        mean, std = float32_running_mean_std(row, chunk=100_003)
        assert (mean, std) == (old_mean[channel], old_std[channel])
        assert mean.dtype == std.dtype == np.float32


def test_fit_norm_stats_matches_float64_mean_and_std():
    chips = _chips()
    low, high, mean, std = _float64_reference(chips)

    stats = fit_norm_stats(chips, "vv_vh")

    np.testing.assert_array_equal(stats.low, low)
    np.testing.assert_array_equal(stats.high, high)
    np.testing.assert_allclose(stats.mean, mean, rtol=0, atol=1e-5)
    np.testing.assert_allclose(stats.std, std, rtol=0, atol=1e-5)
