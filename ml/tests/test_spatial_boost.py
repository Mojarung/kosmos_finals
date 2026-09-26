import numpy as np

from floodvalue.optical import spectral_features
from floodvalue.spatial_boost import features


def test_multiscale_preserves_control_and_missing_optics():
    rng = np.random.default_rng(1)
    s1 = rng.normal(-15, 3, (2, 96, 96)).astype(np.float32)
    s2 = rng.uniform(.01, .5, (13, 96, 96)).astype(np.float32)
    s2[:, 20:30, 40:50] = np.nan
    base, mask = spectral_features(s1, s2)
    extended, new_mask = features(s1, s2)
    np.testing.assert_array_equal(mask, new_mask)
    np.testing.assert_array_equal(base, extended[..., :47])
    assert extended.shape == (96, 96, 65)
    assert np.isnan(extended[~mask, 47:]).all()
    assert np.isfinite(extended[mask]).all()


def test_multiscale_constant_scene_has_no_false_texture():
    s1 = np.full((2, 96, 96), -15, np.float32)
    s2 = np.full((13, 96, 96), .2, np.float32)
    s2[:, 40:60, 40:60] = np.nan
    f, mask = features(s1, s2)
    np.testing.assert_allclose(f[mask, 47:], 0, atol=1e-6)
