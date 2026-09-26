import numpy as np
import pytest

from floodvalue.service_oof import validate_prediction
from floodvalue import service_oof


def test_rejects_wrong_fold_and_corrupt_probabilities(tmp_path):
    p = tmp_path / "chip.npz"
    np.savez(p, name="chip", fold=2, probability=np.full((2, 2), .5))
    np.testing.assert_array_equal(validate_prediction(p, "chip", 2, (2, 2)), .5)
    with pytest.raises(ValueError, match="fold mismatch"):
        validate_prediction(p, "chip", 1, (2, 2))
    np.savez(p, name="chip", fold=2, probability=np.array([[np.nan, .5], [.5, .5]]))
    with pytest.raises(ValueError, match="invalid probabilities"):
        validate_prediction(p, "chip", 2, (2, 2))


def test_expert_routing_uses_only_optical_availability(tmp_path, monkeypatch):
    folders = {}
    for name, value in (("regular", .8), ("radar_expert", .2)):
        folders[name] = tmp_path / name
        folders[name].mkdir()
        np.savez(folders[name] / "chip.npz", name="chip", fold=0, probability=np.full((2,2), value))
    monkeypatch.setattr(service_oof, "MEMBERS", folders)
    records = [{"name": "chip", "fold": 0, "valid": np.array([[True, True], [False, True]]),
                "usable": np.array([True, False, True])}]
    p = service_oof.probabilities(records, {"regular": 1.}, {"radar_expert": 1.})
    np.testing.assert_allclose(p, [.8, .2, .8])
