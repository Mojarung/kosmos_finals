from types import SimpleNamespace
import json

import numpy as np
import pytest
import torch

from floodvalue import refine
from floodvalue.dataset import TensorPart
from floodvalue.finetune import validate_source
from floodvalue.train import TrainConfig, fit


def test_d4_inverts_both_flip_and_rotation(monkeypatch):
    x = np.arange(48, dtype=np.float32).reshape(2, 2, 3, 4)
    monkeypatch.setattr(refine, "normalize", lambda s1, stats: (s1, None))
    monkeypatch.setattr(refine, "predict_logits", lambda model, x: torch.from_numpy(x[:, 0]))
    member = SimpleNamespace(stats=None, model=None)
    for tta in ["none", "rot4", "d4"]:
        assert np.array_equal(refine.predict_transforms(member, list(x), tta), x[:, 0])


def test_finetuning_rejects_wrong_source_training_membership():
    split = {"train": ["A_1"], "valid": ["A_2"]}
    validate_source({"train_chips": ["A_1"], "valid_chips": ["A_2"]}, split)
    with pytest.raises(ValueError, match="train_chips"):
        validate_source({"train_chips": ["A_3"], "valid_chips": ["A_2"]}, split)


def test_initial_checkpoint_can_win_finetuning(tmp_path):
    target = np.zeros((1, 32, 32), np.float32)
    target[:, :, 16:] = 1
    x = np.stack([target*2-1, np.zeros_like(target)], axis=1)
    part = TensorPart(("A_1",), x, target, np.ones_like(target, bool))
    model = torch.nn.Conv2d(2, 1, 1)
    with torch.no_grad():
        model.weight.zero_(); model.weight[0, 0] = 10; model.bias.zero_()
    cfg = TrainConfig("tiny", epochs=2, batch_size=1, crop=32, crops_per_chip=1,
                      eval_every=1, augment=False, lr=1e-4)
    result = fit(cfg, part, part, tmp_path, initial_model=model)
    assert result["best"]["epoch"] == 0
    assert result["best"]["iou"] == 1.
    assert (tmp_path/"model.safetensors").exists()


def test_recipe_geotiffs_preserve_grid_nodata_and_probability_threshold(tmp_path, monkeypatch):
    import rasterio
    from rasterio.transform import from_origin
    s1 = np.zeros((2, 32, 32), np.float32)
    s1[:, 0, 0] = np.nan
    transform = from_origin(100, 200, 10, 10)
    source = tmp_path/"s1.tif"
    with rasterio.open(source, "w", driver="GTiff", width=32, height=32, count=2,
                       dtype="float32", crs="EPSG:6933", transform=transform) as dst:
        dst.write(s1)
    probability = np.linspace(0, 1, 1024, dtype=np.float32).reshape(1, 32, 32)
    recipe = {"threshold": .4, "calibration_method": "isotonic"}
    recipe_path = tmp_path/"recipe.json"
    recipe_path.write_text(json.dumps(recipe))
    monkeypatch.setattr(refine, "predict_recipe", lambda *args: (recipe, probability, probability/10))
    out = tmp_path/"output"
    refine.infer_recipe(source, recipe_path, out)
    arrays = {}
    for filename, nodata, dtype in [("flood_probability.tif", -9999, "float32"),
                                    ("flood_mask.tif", 255, "uint8"),
                                    ("flood_uncertainty.tif", -9999, "float32")]:
        with rasterio.open(out/filename) as src:
            assert src.crs == rasterio.crs.CRS.from_epsg(6933)
            assert src.transform == transform
            assert src.count == 1 and src.dtypes == (dtype,)
            assert src.nodata == nodata
            arrays[filename] = src.read(1)
            assert arrays[filename][0, 0] == nodata
    valid = np.isfinite(s1).all(axis=0)
    assert np.array_equal(arrays["flood_mask.tif"][valid], arrays["flood_probability.tif"][valid] >= .4)


def test_recipe_refuses_changed_weights(tmp_path):
    import hashlib
    member = tmp_path/"member"
    member.mkdir()
    weights = member/"model.safetensors"
    weights.write_bytes(b"original")
    recipe = {"members": [{"member": "member", "tta": "none",
                            "weights_sha256": hashlib.sha256(weights.read_bytes()).hexdigest()}],
              "member_root": "recipe", "weights": [1.]}
    path = tmp_path/"recipe.json"
    path.write_text(json.dumps(recipe))
    weights.write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        refine.predict_recipe(path, [np.zeros((2, 32, 32), np.float32)])


def test_frozen_bn_keeps_statistics_while_updating_weights(tmp_path):
    rng = np.random.default_rng(3)
    x = rng.normal(size=(1, 2, 32, 32)).astype(np.float32)
    target = (x[:, 0] > 0).astype(np.float32)
    part = TensorPart(("A_1",), x, target, np.ones_like(target, bool))
    bn = torch.nn.BatchNorm2d(2)
    model = torch.nn.Sequential(bn, torch.nn.Conv2d(2, 1, 1))
    mean, var, affine = bn.running_mean.clone(), bn.running_var.clone(), bn.weight.detach().clone()
    cfg = TrainConfig("tiny_bn", epochs=2, batch_size=1, crop=32, crops_per_chip=2,
                      eval_every=1, augment=False, lr=1e-3, freeze_bn=True)
    fit(cfg, part, part, tmp_path, initial_model=model)
    assert torch.equal(bn.running_mean.cpu(), mean)
    assert torch.equal(bn.running_var.cpu(), var)
    assert bn.num_batches_tracked == 0
    assert not torch.equal(bn.weight.detach().cpu(), affine)
