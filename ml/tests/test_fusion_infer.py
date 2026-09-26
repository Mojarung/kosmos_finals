import json
import hashlib

import joblib
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from sklearn.dummy import DummyClassifier

from floodvalue import fusion_infer
from floodvalue.optical import infer_optical
from floodvalue.fusion_calibration import fit_branch_calibration, BranchCalibration


def fixture_recipe(tmp_path, monkeypatch):
    member = tmp_path/"tree"
    member.mkdir()
    model = DummyClassifier(strategy="prior").fit(np.zeros((10, 47)), [0]*2+[1]*8)
    joblib.dump(model, member/"model.joblib")
    (member/"config.json").write_text("{}")
    base = tmp_path/"base.json"
    base.write_text("{}")
    recipe = {"fusion_members": [{"name": "tree", "path": "tree", "kind": "tree", "weight": 1.,
                                  "checksums": {f: fusion_infer.sha(member/f) for f in ["config.json", "model.joblib"]}}],
              "base_recipe": "base.json", "base_recipe_sha256": fusion_infer.sha(base),
              "member_root": "recipe", "optical_weight": .75,
              "calibration_method": "platt", "calibration": {"a": 1., "b": 0.}, "threshold": .5}
    path = tmp_path/"recipe.json"
    path.write_text(json.dumps(recipe))
    def radar(_, chips, **kwargs):
        shape = (len(chips), *chips[0].shape[-2:])
        return {}, np.full(shape, .2, np.float32), np.full(shape, .03, np.float32)
    monkeypatch.setattr(fusion_infer, "predict_recipe", radar)
    return path


def test_frozen_fusion_dates_missing_pixels_and_checksums(tmp_path, monkeypatch):
    path = fixture_recipe(tmp_path, monkeypatch)
    s1 = np.full((2, 32, 32), -20., np.float32)
    s2 = np.full((13, 32, 32), .1, np.float32)
    s2[:, 0, 0] = 0
    dates = [{"s1_date": "2018-01-01", "s2_date": "2018-01-01"},
             {"s1_date": "2018-01-01", "s2_date": "2018-01-02"}, None]
    _, p, spread, usage = fusion_infer.predict_fusion_recipe(path, [s1]*3, [s2]*3, dates)
    np.testing.assert_allclose(p[0, 1:, :], .65)
    np.testing.assert_allclose(p[0, 0, 0], .2)
    np.testing.assert_allclose(p[1:], .2)
    np.testing.assert_allclose(spread[0, 1:, :], .3)
    assert [u["optical_used_pixels"] for u in usage] == [1023, 0, 0]
    (tmp_path/"tree/config.json").write_text('{"changed": true}')
    with pytest.raises(ValueError, match="checksum"):
        fusion_infer.predict_fusion_recipe(path, [s1], [s2], dates[:1])


def test_fusion_geotiffs_keep_s1_validity_and_nodata(tmp_path, monkeypatch):
    recipe = fixture_recipe(tmp_path, monkeypatch)
    profile = dict(driver="GTiff", height=32, width=32, crs="EPSG:4326",
                   transform=from_origin(10, 10, .001, .001), dtype="float32")
    s1 = np.full((2, 32, 32), -20., np.float32)
    s1[:, 0, 0] = np.nan
    for filename, array in [("s1.tif", s1), ("s2.tif", np.full((13, 32, 32), 1000., np.float32))]:
        with rasterio.open(tmp_path/filename, "w", count=len(array), **profile) as dst:
            dst.write(array)
    result = infer_optical(tmp_path/"s1.tif", recipe, tmp_path/"output", tmp_path/"s2.tif",
                           {"s1_date": "2018-01-01", "s2_date": "2018-01-01"})
    assert result["valid_pixels"] == 1023
    for filename, nodata, value in [("flood_probability.tif", -9999, .65),
                                    ("flood_mask.tif", 255, 1), ("flood_uncertainty.tif", -9999, .3)]:
        with rasterio.open(tmp_path/"output"/filename) as src:
            assert src.nodata == nodata and src.transform == profile["transform"]
            a = src.read(1)
            assert a[0, 0] == nodata
            np.testing.assert_allclose(a[1:, :], value)


def test_branch_calibration_uses_only_valid_labels_and_input_availability():
    z = np.zeros(300, np.float32)
    available = np.arange(300) < 100
    valid = np.arange(300) < 200
    target = np.zeros(300, bool)
    target[:80] = True
    target[100:120] = True
    first = fit_branch_calibration(z, target, valid, available)
    target[~valid] = True
    assert fit_branch_calibration(z, target, valid, available) == first
    p = BranchCalibration(first, np.array([True, False])).apply(np.zeros(2))
    np.testing.assert_allclose(p, [.8, .2])
    with pytest.raises(ValueError, match="mask shape"):
        BranchCalibration(first, np.array([True])).apply(np.zeros(2))


def test_recipe_branch_calibration_switches_to_fallback_when_s2_is_missing(tmp_path, monkeypatch):
    path = fixture_recipe(tmp_path, monkeypatch)
    recipe = json.loads(path.read_text())
    recipe["calibration_method"] = "branch_isotonic"
    recipe["calibration"] = {"optical": {"type": "isotonic", "x": [-10., 10.], "y": [.8, .8]},
                             "fallback": {"type": "isotonic", "x": [-10., 10.], "y": [.2, .2]}}
    path.write_text(json.dumps(recipe))
    s1 = np.full((2, 32, 32), -20., np.float32)
    s2 = np.full((13, 32, 32), .1, np.float32)
    dates = [{"s1_date": "2018-01-01", "s2_date": "2018-01-01"}]*2
    _, p, _, _ = fusion_infer.predict_fusion_recipe(path, [s1]*2, [s2, None], dates)
    np.testing.assert_allclose(p[0], .8)
    np.testing.assert_allclose(p[1], .2)


def test_zero_weight_radar_member_is_not_loaded(tmp_path, monkeypatch):
    from floodvalue import refine
    active = tmp_path/"active"
    active.mkdir()
    (active/"model.safetensors").write_bytes(b"active weights")
    metadata = {"member": "active", "tta": "none", "weights_sha256": hashlib.sha256(b"active weights").hexdigest()}
    recipe = {"member_root": "recipe", "members": [{"member": "absent", "tta": "rot4", "weights_sha256": "unused"}, metadata],
              "weights": [0., 1.], "space": "probabilities", "calibration_method": "platt", "calibration": {"a": 1., "b": 0.}}
    path = tmp_path/"radar.json"
    path.write_text(json.dumps(recipe))
    loaded = []
    monkeypatch.setattr(refine, "load_member", lambda p: loaded.append(p) or object())
    monkeypatch.setattr(refine, "predict_transforms", lambda *args: np.zeros((1, 8, 8), np.float32))
    _, p, spread = refine.predict_recipe(path, [np.zeros((2, 8, 8), np.float32)])
    assert loaded == [active]
    np.testing.assert_allclose(p, .5)
    np.testing.assert_allclose(spread, 0.)
