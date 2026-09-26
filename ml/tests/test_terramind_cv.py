import numpy as np
import torch

from floodvalue import terramind_cv as tm


def test_normalization_matches_pretrained_units_and_masks_missing_optics():
    radar = np.broadcast_to(tm.S1_MEAN[:, None, None], (2, 2, 2)).copy()
    optics = np.broadcast_to((tm.S2_MEAN / 10000)[:, None, None], (12, 2, 2)).copy()
    usable = np.ones((2, 2), bool)
    usable[0, 0] = False
    optics[:, 0, 0] = np.nan
    x = tm.normalized(radar, optics, usable)
    assert x.shape == (15, 2, 2)
    assert np.isfinite(x).all()
    np.testing.assert_allclose(x[:14], 0, atol=1e-6)
    np.testing.assert_array_equal(x[14], usable)


def test_loss_ignores_invalid_targets_and_gradients():
    z = torch.tensor([[0.2, -0.3]], requires_grad=True)
    valid = torch.tensor([[1., 0.]])
    loss = tm.loss_fn(z, torch.tensor([[1., 0.]]), valid)
    altered = tm.loss_fn(z, torch.tensor([[1., 1.]]), valid)
    torch.testing.assert_close(loss, altered)
    loss.backward()
    assert z.grad[0, 0] != 0
    assert z.grad[0, 1] == 0
    empty = tm.loss_fn(z, torch.ones_like(z), torch.zeros_like(z))
    assert empty.item() == 0


def test_optical_dropout_keeps_radar_and_spatial_alignment(monkeypatch):
    monkeypatch.setitem(tm.CONFIG, "optical_dropout", 1.)
    target = np.indices((512, 512)).sum(axis=0) % 2
    x = np.ones((1, 15, 512, 512), np.float16)
    x[0, 0] = target
    arrays = {"x": x, "target": target[None], "valid": target[None]}
    xx, yy, vv = tm.batch(arrays, [0], np.random.default_rng(8), "cpu")
    torch.testing.assert_close(xx[:, 0], yy)
    torch.testing.assert_close(yy, vv)
    assert (xx[:, 1] == 1).all()
    assert (xx[:, 2:] == 0).all()
