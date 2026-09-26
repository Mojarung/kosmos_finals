import numpy as np
import pytest

from floodvalue.l2a_boost import features, split_fold


def test_missing_l2a_preserves_radar_and_does_not_impute_fake_spectrum():
    s1 = np.full((2, 20, 20), -15, np.float32)
    s2 = np.full((12, 20, 20), .2, np.float32)
    usable = np.ones((20, 20), bool)
    usable[5:10, 5:10] = False
    x = features(s1, s2, usable)
    assert x.shape == (20, 20, 47)
    assert np.isfinite(x[..., :23]).all()
    assert np.isnan(x[~usable, 23:46]).all()
    np.testing.assert_array_equal(x[..., -1], usable)
    assert np.isfinite(x[usable]).all()
    with pytest.raises(ValueError, match="12 bands"):
        features(s1, np.zeros((13, 20, 20)), usable)


def test_nested_folds_exclude_outer_from_early_stopping_and_refit():
    folds = {f"chip{i}": i for i in range(5)}
    for k in range(5):
        pilot, inner, refit, outer = split_fold(k, folds)
        assert set(pilot).isdisjoint(inner)
        assert set(refit) == set(pilot)|set(inner)
        assert set(refit).isdisjoint(outer)
        assert inner == [f"chip{(k+1)%5}"]
