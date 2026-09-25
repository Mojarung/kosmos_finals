import numpy as np
import pytest

from floodvalue.calibration import Platt, best_prob_threshold, fit_platt, prob_threshold_curve, reliability
from floodvalue.features import NormStats, fit_norm_stats, normalize
from floodvalue.uncertainty import binary_entropy, error_detection


def test_platt_recovers_known_scaling():
    rng = np.random.default_rng(1)
    z = rng.normal(0, 3, 200_000)
    y = rng.random(z.size) < 1 / (1 + np.exp(-(0.5 * z - 1.0)))

    platt = fit_platt(z, y)

    assert platt.a == pytest.approx(0.5, abs=0.02)
    assert platt.b == pytest.approx(-1.0, abs=0.03)


def test_reliability_is_perfect_for_matching_frequencies():
    prob = np.array([0.1] * 10 + [0.9] * 10)
    target = np.array([1] + [0] * 9 + [1] * 9 + [0])

    table = reliability(prob, target, n_bins=10)

    assert table["ece"] == pytest.approx(0.0)
    assert table["n_pixels"] == 20
    assert table["brier"] == pytest.approx(0.09)


def test_threshold_curve_counts_at_or_above():
    prob = np.array([0.2, 0.4, 0.6, 0.8])
    target = np.array([0, 1, 1, 0])

    thresholds, curve = prob_threshold_curve(prob, target)
    at_05 = curve[list(thresholds).index(0.5)]

    assert (at_05.tp, at_05.fp, at_05.fn, at_05.tn) == (1, 1, 1, 1)


def test_best_prob_threshold_separates_classes():
    prob = np.array([0.1, 0.2, 0.3, 0.7, 0.8])
    target = np.array([0, 0, 0, 1, 1])

    threshold, conf = best_prob_threshold(prob, target)

    assert 0.3 < threshold <= 0.7
    assert conf.iou == 1.0


def test_binary_entropy_bounds():
    values = binary_entropy(np.array([0.0, 0.5, 1.0]))

    assert values[1] == pytest.approx(1.0)
    assert values[0] < 1e-5 and values[2] < 1e-5


def test_error_detection_auroc_is_one_when_uncertainty_flags_errors():
    uncertainty = np.array([0.1, 0.2, 0.3, 0.9, 0.95])
    error = np.array([0, 0, 0, 1, 1])

    result = error_detection(uncertainty, error)

    assert result["auroc"] == pytest.approx(1.0)
    assert result["error_rate"] == pytest.approx(0.4)


def test_normalize_zeroes_invalid_pixels_and_uses_train_stats():
    s1 = np.array([[[-10.0, np.nan], [-20.0, -30.0]], [[-20.0, -25.0], [-30.0, -40.0]]], dtype=np.float32)

    stats = fit_norm_stats([s1], "vv_vh_diff")
    x, valid = normalize(s1, stats)

    assert x.shape == (3, 2, 2)
    assert not valid[0, 1] and np.all(x[:, 0, 1] == 0)
    assert np.isfinite(x).all()
    assert NormStats.from_dict(stats.as_dict()) == stats


def test_norm_stats_reject_inputs_without_valid_pixels():
    all_nan = np.full((2, 4, 4), np.nan, dtype=np.float32)

    with pytest.raises(ValueError):
        fit_norm_stats([all_nan], "vv_vh")
    with pytest.raises(ValueError):
        fit_norm_stats([], "vv_vh")


def test_platt_apply_is_monotonic():
    probs = Platt(2.0, 0.5).apply(np.array([-1.0, 0.0, 1.0]))

    assert np.all(np.diff(probs) > 0)


def test_remove_small_components_keeps_only_large_blobs():
    from floodvalue.postprocess import remove_small_components

    mask = np.zeros((6, 6), dtype=bool)
    mask[0:3, 0:3] = True  # 9 px
    mask[5, 5] = True  # 1 px

    result = remove_small_components(mask, 4)

    assert result[:3, :3].all()
    assert not result[5, 5]
    assert remove_small_components(mask, 0).sum() == 10
