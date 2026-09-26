import numpy as np
import pytest

from floodvalue.calibration import reliability
from floodvalue.reliability_check import ece_equal_mass, ece_mid_range


def test_equal_mass_ece_keeps_apart_what_one_wide_bin_cancels():
    # Both groups fall into the first 1/15-wide bin, where their gaps (-2 and +4 pixels) partly cancel.
    prob = np.concatenate([np.full(100, 0.02), np.full(100, 0.04)]).astype(np.float32)
    target = np.zeros(200, dtype=bool)
    target[100:108] = True
    order = np.random.default_rng(0).permutation(200)

    result = ece_equal_mass(prob[order], target[order], n_bins=2)

    assert [b["count"] for b in result["bins"]] == [100, 100]
    assert result["bins"][0]["mean_p"] == pytest.approx(0.02)
    assert (result["bins"][0]["water_fraction"], result["bins"][1]["water_fraction"]) == (0.0, 0.08)
    assert result["ece"] == pytest.approx((2 + 4) / 200, abs=1e-6)
    assert reliability(prob, target)["ece"] == pytest.approx(2 / 200, abs=1e-6)


def test_equal_mass_bins_differ_by_at_most_one_pixel():
    prob = np.linspace(0.0, 1.0, 10, dtype=np.float32)
    result = ece_equal_mass(prob, prob > 0.5, n_bins=3)

    assert [b["count"] for b in result["bins"]] == [4, 3, 3]
    assert result["bins"][0]["hi"] <= result["bins"][1]["lo"]


def test_mid_range_ece_ignores_confident_pixels():
    prob = np.concatenate([np.full(1000, 0.01), np.full(100, 0.3), np.full(100, 0.7)]).astype(np.float32)
    target = np.zeros(1200, dtype=bool)
    target[:1000] = True                           # confident and wrong, but outside 0.05 < p < 0.95
    target[1000:1040] = True                       # p 0.3, 40% water: gap 10 pixels
    target[1100:1170] = True                       # p 0.7, 70% water: no gap

    result = ece_mid_range(prob, target)

    assert result["pixels"] == 200 and result["share_of_pixels"] == round(200 / 1200, 4)
    assert result["ece"] == pytest.approx(10 / 200, abs=1e-6)
