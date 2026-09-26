import json

import numpy as np
import pytest
import torch

from floodvalue.cv import make_plan, score_arrays
from floodvalue.calibration import Platt
from floodvalue.dataset import TensorPart
from floodvalue.train import crop_pools, masked_lovasz, masked_loss, sample_batch, TrainConfig
from floodvalue.cv_blend import blend_arrays
from floodvalue import cv_tta, cv_blend
from floodvalue.cv_calibration import fit_calibrator


def test_event_cv_excludes_test_and_holds_events_out_everywhere():
    parts = {"train": ["A_1", "B_1", "C_1", "A_2"],
             "valid": ["A_3", "B_3", "C_3"],
             "test_core": ["A_9"], "test_adjacent": ["B_9"], "bolivia": ["Bolivia_1"]}
    plan = make_plan(parts, 3)
    scored = []
    for f in plan["folds"]:
        held = set(f["held_out_events"])
        assert all(n.split('_')[0] not in held for n in f["train"] + f["valid"])
        assert all(n.split('_')[0] in held for n in f["score"])
        assert not set(f["train"] + f["valid"] + f["score"]) & set(plan["excluded_chips"])
        scored.extend(f["score"])
    assert sorted(scored) == sorted(parts["train"] + parts["valid"])
    assert make_plan(parts, 3) == plan
    parts["test_core"].append("A_1")
    with pytest.raises(ValueError, match="contamination"):
        make_plan(parts, 3)


def test_lovasz_ignores_nodata_in_value_and_gradient():
    logits = torch.tensor([[2., -2., 100.]], requires_grad=True)
    target = torch.tensor([[1., 0., 1.]])
    valid = torch.tensor([[True, True, False]])
    assert masked_lovasz(logits, target, valid).item() == 0
    loss = masked_loss(logits, target, valid, .5, "bce_lovasz")
    loss.backward()
    assert logits.grad[0, 2] == 0
    assert torch.isfinite(logits.grad).all()


def test_lovasz_all_invalid_and_all_dry_are_finite():
    logits = torch.tensor([[1., -1.]], requires_grad=True)
    target = torch.zeros_like(logits)
    invalid = torch.zeros_like(logits, dtype=torch.bool)
    loss = masked_loss(logits, target, invalid, .5, "bce_lovasz")
    assert loss.item() == 0
    loss.backward()
    assert (logits.grad == 0).all()
    assert masked_lovasz(logits, target, ~invalid).item() == pytest.approx(2.)


def test_crop_pools_keep_dry_and_water_and_exclude_unlabelled():
    target = np.zeros((3, 64, 64), np.float32)
    target[1] = 1
    valid = np.ones_like(target, bool)
    valid[2] = False
    part = TensorPart(("A_1", "A_2", "A_3"), np.stack([target, valid], 1), target, valid)
    pools = crop_pools(part, 32)
    assert len(pools[0]) == 4 and len(pools[4]) == 4
    assert all(row[0] != 2 for pool in pools for row in pool)
    tensors = {"x": torch.tensor(part.x), "target": torch.tensor(target), "valid": torch.tensor(valid)}
    args = (tensors, torch.tensor([0, 1] * 8), 32)
    x, y, v = sample_batch(*args, torch.Generator().manual_seed(7), pools=pools)
    assert torch.equal(x[:, 0], y)
    assert torch.equal(x[:, 1].bool(), v)
    repeat = sample_batch(*args, torch.Generator().manual_seed(7), pools=pools)
    assert all(torch.equal(a, b) for a, b in zip((x, y, v), repeat))


def test_cv_score_reports_macro_events_not_macro_chips():
    logits = np.array([[[5.]], [[5.]], [[-5.]]], dtype=np.float32)
    target = np.ones_like(logits, bool)
    scored = score_arrays(["A_1", "A_2", "B_1"], logits, target, target, Platt(1., 0.), .5)
    assert scored["overall"]["micro"]["iou"] == pytest.approx(2/3)
    assert scored["event_mean_iou"] == pytest.approx(.5)


def test_invalid_training_options_fail_before_training():
    with pytest.raises(ValueError):
        TrainConfig("bad", sampler="typo")
    with pytest.raises(ValueError):
        TrainConfig("bad", large_crop=384, large_crop_epoch=0)


def test_blends_preserve_identity_and_reject_misaligned_pixels():
    a = {"names": np.array(["A_1"]), "logits": np.array([[[-2., 1.]]], np.float32),
         "target": np.array([[[False, True]]]), "valid": np.ones((1, 1, 2), bool)}
    b = {k: v.copy() for k, v in a.items()}
    b["logits"] *= -1
    assert np.allclose(blend_arrays([a, b], [1, 0], "probabilities"), a["logits"], atol=1e-6)
    assert np.allclose(blend_arrays([a, b], [1, 1]), 0)
    b["valid"][0, 0, 0] = False
    with pytest.raises(ValueError, match="incompatible"):
        blend_arrays([a, b], [1, 1])


def test_tta_inverts_spatial_transforms_before_averaging(monkeypatch):
    x = np.arange(24, dtype=np.float32).reshape(2, 2, 2, 3)
    monkeypatch.setattr(cv_tta, "predict_logits", lambda model, batch: torch.from_numpy(batch[:, 0]))
    result = cv_tta.average_rotation_logits(None, x, x[:, 0])
    assert np.array_equal(result, x[:, 0])


def test_temperature_reduces_overconfidence_without_changing_ranking():
    rng = np.random.default_rng(3)
    z = rng.normal(size=10_000)
    y = rng.random(z.size) < 1 / (1 + np.exp(-z))
    overconfident = 3*z
    fit = fit_calibrator(overconfident, y, "temperature")
    bce = lambda a: np.mean(np.logaddexp(0, a*overconfident) - y*a*overconfident)
    assert 0 < fit.a < 1 and fit.b == 0
    assert bce(fit.a) < bce(1.)
    assert np.all(np.diff(fit.apply(np.sort(z))) >= 0)


def test_isotonic_is_monotonic_bounded_and_clips_outside_fit_range():
    z = np.array([-2., -1., 0., 1., 2.])
    fit = fit_calibrator(z, np.array([0., 0., 1., 0., 1.]), "isotonic")
    p = fit.apply(np.array([[-100., -2., 0., 2., 100.]]))
    assert p.shape == (1, 5) and p.dtype == np.float32
    assert np.all(np.diff(p) >= 0) and np.all((p >= 0) & (p <= 1))
    assert p[0, 0] == p[0, 1] and p[0, -1] == p[0, -2]


def test_blend_calibration_fits_inner_validation_only(tmp_path, monkeypatch):
    runs = [tmp_path / "a", tmp_path / "b"]
    for run in runs:
        run.mkdir()
        (run / "cv_result.json").write_text(json.dumps({"fold": 0, "plan_sha256": "same"}))
        for part, name, logits in [("valid", "A_1", [-2., 2.]), ("score", "B_1", [3., -3.])]:
            np.savez(run / (part + "_predictions.npz"), names=[name],
                     logits=np.array([[logits]], np.float32), target=np.array([[[False, True]]]),
                     valid=np.ones((1, 1, 2), bool))
    calls = []
    def fit_inner(z, y, method):
        assert np.array_equal(z, [-2., 2.])
        assert np.array_equal(y, [False, True])
        calls.append(method)
        return fit_calibrator(z, y, method)
    monkeypatch.setattr(cv_blend, "fit_calibrator", fit_inner)
    result = cv_blend.evaluate_blend(runs, [.5, .5], "logits", "isotonic")
    assert calls == ["isotonic"]
    assert result["calibration"]["isotonic"]["type"] == "isotonic"
    assert result["score"]["event_mean_iou"] == 0  # Outer labels deliberately reverse the inner rule.
