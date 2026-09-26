import numpy as np
import torch
import pytest
from copy import deepcopy
from safetensors.torch import load_file

from floodvalue.dataset import TensorPart
from floodvalue.train import TrainConfig, checkpoint_confusion, fit
from floodvalue import refine


def test_checkpoint_selection_is_not_tied_to_logit_zero():
    z = torch.tensor([-4., -3., -2., -1.]).reshape(1, 2, 2)
    target = torch.tensor([0., 0., 1., 1.]).reshape(1, 2, 2)
    valid = torch.ones_like(target, dtype=torch.bool)
    fixed, _ = checkpoint_confusion(z, target, valid, "p05")
    tuned, threshold = checkpoint_confusion(z, target, valid, "best_threshold")
    assert fixed.iou == 0 and tuned.iou == 1 and threshold < .5


def test_ema_and_threshold_checkpoint_with_batchnorm(tmp_path):
    rng = np.random.default_rng(4)
    x = rng.normal(size=(2, 2, 32, 32)).astype(np.float32)
    target = (x[:, 0] > 0).astype(np.float32)
    part = TensorPart(("A_1", "A_2"), x, target, np.ones_like(target, bool))
    model = torch.nn.Sequential(torch.nn.BatchNorm2d(2), torch.nn.Conv2d(2, 1, 1))
    cfg = TrainConfig("tiny_ema", epochs=2, batch_size=2, crop=32, crops_per_chip=2,
                      eval_every=1, augment=False, lr=.001, ema_decay=.9,
                      checkpoint_metric="best_threshold", grad_clip=1.)
    result = fit(cfg, part, part, tmp_path, initial_model=model)
    assert all("ema_valid_iou" in h for h in result["history"])
    assert result["best"]["raw_threshold"] > 0
    assert result["best"]["variant"] in {"raw", "ema"}


def test_overlapping_tiles_have_no_seams_for_pointwise_model(monkeypatch):
    x = np.random.default_rng(2).normal(size=(2, 2, 512, 512)).astype(np.float32)
    monkeypatch.setattr(refine, "predict_logits", lambda model, a: torch.from_numpy(a[:, 0]))
    for mode in ["tiles256", "tiles384"]:
        actual = refine.predict_spatial(None, x, mode)
        np.testing.assert_allclose(actual, x[:, 0], rtol=2e-6, atol=2e-6)


def test_multiscale_restores_input_grid(monkeypatch):
    monkeypatch.setattr(refine, "predict_logits", lambda model, a: torch.from_numpy(a[:, 0]))
    x = np.full((2, 2, 512, 512), 3., np.float32)
    for mode in ["scale050", "scale075", "scale125"]:
        actual = refine.predict_spatial(None, x, mode)
        assert actual.shape == (2, 512, 512)
        np.testing.assert_allclose(actual, 3., atol=1e-6)


def test_interrupted_training_resumes_optimizer_ema_and_random_crops(tmp_path, monkeypatch):
    from floodvalue import train
    rng = np.random.default_rng(8)
    x = rng.normal(size=(2, 2, 64, 64)).astype(np.float32)
    target = (x[:, 0] > 0).astype(np.float32)
    part = TensorPart(("A_1", "A_2"), x, target, np.ones_like(target, bool))
    model = torch.nn.Sequential(torch.nn.BatchNorm2d(2), torch.nn.Dropout2d(.2), torch.nn.Conv2d(2, 1, 1))
    cfg = TrainConfig("tiny_resume", epochs=3, batch_size=2, crop=32, crops_per_chip=2,
                      eval_every=1, lr=.001, ema_decay=.9, resume_training=True)
    reference, interrupted = tmp_path/"reference", tmp_path/"interrupted"
    reference.mkdir(); interrupted.mkdir()
    expected = fit(cfg, part, part, reference, initial_model=deepcopy(model))
    original = train.sample_batch
    calls = 0

    def interrupt(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("simulated process interruption")
        return original(*args, **kwargs)

    monkeypatch.setattr(train, "sample_batch", interrupt)
    with pytest.raises(RuntimeError, match="simulated"):
        fit(cfg, part, part, interrupted, initial_model=deepcopy(model))
    monkeypatch.setattr(train, "sample_batch", original)
    actual = fit(cfg, part, part, interrupted, initial_model=deepcopy(model))
    assert expected["best"] == actual["best"]
    assert [h["loss"] for h in expected["history"]] == [h["loss"] for h in actual["history"]]
    a, b = load_file(str(reference/"model.safetensors")), load_file(str(interrupted/"model.safetensors"))
    for key in a:
        torch.testing.assert_close(a[key], b[key], rtol=0, atol=0)
