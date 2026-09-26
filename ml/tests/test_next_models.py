import numpy as np
import pytest

from floodvalue import next_boost
from floodvalue.optical_cnn import normalize_s2
from floodvalue.fusion import fit_gate, fit_weights, fuse


def test_next_experiments_reject_final_test_and_overlap(monkeypatch):
    monkeypatch.setattr(next_boost, "load_split", lambda path: {"train": ["A_1"], "valid": ["A_2"]})
    next_boost.check_development({"train": ["A_1"], "valid": ["A_2"]})
    with pytest.raises(ValueError, match="final-test"):
        next_boost.check_development({"train": ["A_1"], "valid": ["A_3"]})
    with pytest.raises(ValueError, match="overlapping"):
        next_boost.check_development({"train": ["A_1"], "valid": ["A_1"]})


def test_outer_events_cannot_enter_training_or_inner_validation(monkeypatch):
    monkeypatch.setattr(next_boost, "load_split", lambda path: {
        "train": ["India_1", "Spain_1"], "valid": ["India_2", "Spain_2"]})
    next_boost.check_development({"train": ["India_1"], "valid": ["India_2"], "score": ["Spain_1", "Spain_2"]})
    with pytest.raises(ValueError, match="scoring events"):
        next_boost.check_development({"train": ["India_1"], "valid": ["Spain_1"], "score": ["Spain_2"]})


def test_optical_cnn_normalization_preserves_missing_data_contract():
    s1 = np.full((2, 32, 32), -20., np.float32)
    s2 = np.full((13, 32, 32), .2, np.float32)
    s1[:, 0, 0] = np.nan
    s2[:, 1, 1] = 0
    x, available = normalize_s2(s1, s2, {"mean": [.1]*6, "std": [.1]*6})
    assert x.shape == (6, 32, 32) and x.dtype == np.float32
    assert np.isfinite(x).all()
    assert (x[:, 0, 0] == 0).all() and (x[:, 1, 1] == 0).all()
    assert not available[1, 1]
    np.testing.assert_allclose(x[:, 2, 2], 1.)


def test_convex_ensemble_rejects_uninformative_member():
    rng = np.random.default_rng(2)
    y = rng.integers(0, 2, 2000)
    p = np.column_stack([.1+.8*y, rng.uniform(.1, .9, len(y))])
    weights = np.asarray(fit_weights(p, y))
    assert weights[0] > .99
    assert (weights >= 0).all()
    np.testing.assert_allclose(weights.sum(), 1.)


def test_quality_gate_generalizes_and_preserves_missing_optics():
    rng = np.random.default_rng(3)
    def sample(n):
        y = rng.integers(0, 2, n)
        cloudy = rng.integers(0, 2, n).astype(bool)
        correct = .15+.7*y
        wrong = 1-correct
        radar = np.where(cloudy, correct, wrong).astype(np.float32)
        optical = np.where(cloudy, wrong, correct).astype(np.float32)
        quality = np.column_stack([cloudy, rng.normal(size=n), rng.normal(size=n)]).astype(np.float32)
        return radar, optical, quality, y
    train = sample(4000)
    gate = fit_gate(*train)
    s, o, q, y = sample(1000)
    available = np.ones(len(y), bool)
    p = fuse(s, o, available, q, gate)
    assert ((p > .5) == y).mean() > .98
    available[::3] = False
    o[~available] = np.nan
    q[~available] = np.nan
    p = fuse(s, o, available, q, gate)
    np.testing.assert_array_equal(p[~available], s[~available])
    assert np.isfinite(p).all()
