import numpy as np
import hashlib
import json

from floodvalue.optical import optical_date_allowed, spectral_features


def test_optical_dates_do_not_allow_future_or_stale_images():
    assert optical_date_allowed("2018/05/07", "2018/05/05")
    assert optical_date_allowed("2018-05-07", "2018-05-07")
    assert not optical_date_allowed("2018/05/07", "2018/05/08")
    assert not optical_date_allowed("2018/05/07", "2018/05/04")


def test_missing_optical_pixel_is_marked_without_dropping_s1_grid():
    s1 = np.full((2, 32, 32), -20., np.float32)
    s2 = np.full((13, 32, 32), .1, np.float32)
    s2[:, 8, 9] = 0
    features, available = spectral_features(s1, s2)
    assert features.shape == (32, 32, 47)
    assert available.sum() == 32*32-1 and not available[8, 9]
    assert np.isfinite(features[:, :, :23]).all()
    assert np.isnan(features[8, 9, 23:]).all()


def test_future_and_missing_optics_fall_back_without_using_optical_classifier(tmp_path, monkeypatch):
    from floodvalue import optical
    member = tmp_path/"optical"
    member.mkdir()
    (member/"model.joblib").write_bytes(b"test checkpoint")
    (tmp_path/"base.json").write_text("{}")
    recipe = {"optical_member": "optical", "base_recipe": "base.json", "member_root": "recipe",
              "optical_weight": .75, "weights_sha256": hashlib.sha256(b"test checkpoint").hexdigest(),
              "calibration_method": "platt", "calibration": {"a": 1., "b": 0.}}
    path = tmp_path/"recipe.json"
    path.write_text(json.dumps(recipe))
    base = np.full((2, 32, 32), .2, np.float32)
    monkeypatch.setattr(optical, "predict_recipe", lambda *a, **kw: ({}, base.copy(), base.copy()/10))
    monkeypatch.setattr(optical.joblib, "load", lambda p: None)
    def forbidden(*args):
        raise AssertionError("unavailable optics must never enter the classifier")
    monkeypatch.setattr(optical, "predict_optical", forbidden)
    s1 = [np.zeros((2, 32, 32), np.float32)]*2
    s2 = [np.ones((13, 32, 32), np.float32), None]
    dates = [{"s1_date": "2018-01-01", "s2_date": "2018-01-02"}, None]
    _, probability, spread, usage = optical.predict_optical_recipe(path, s1, s2, dates)
    np.testing.assert_allclose(probability, base)
    np.testing.assert_allclose(spread, base/10)
    assert all(u["optical_used_pixels"] == 0 for u in usage)
