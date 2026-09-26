import importlib
import json
from pathlib import Path

import numpy as np
import pytest


def test_parallel_optical_cache_requires_freeze_and_resumes_without_repredicting(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/"scripts"))
    module = importlib.import_module("next_evaluate")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path/"release_audit.json").write_text(json.dumps({"recipe_sha256": "frozen-source"}))
    recipe = tmp_path/"recipe.json"
    recipe.write_text(json.dumps({"source_recipe_sha256": "wrong-source"}))
    with pytest.raises(ValueError, match="not frozen"):
        module.optical_worker(recipe, "test_core", 0, 2)
    recipe.write_text(json.dumps({"source_recipe_sha256": "frozen-source"}))
    monkeypatch.setattr(module, "load_split", lambda _: {"test_core": ["India_1", "India_2"]})
    monkeypatch.setattr(module, "event_dates", lambda _: {"India": {"s1_date": "2018-01-01", "s2_date": "2018-01-01"}})
    monkeypatch.setattr(module, "read_s1", lambda *_: np.zeros((2, 8, 8), np.float32))
    monkeypatch.setattr(module, "read_optical", lambda *_: np.ones((13, 8, 8), np.float32))
    monkeypatch.setattr(module, "optical_ensemble", lambda *_: (np.full((8, 8), .8, np.float32),
                                                              np.ones((8, 8), bool), np.zeros((8, 8), np.float32)))
    module.optical_worker(recipe, "test_core", 0, 2)
    cache = tmp_path/"optical_test_cache/test_core"
    assert sorted(p.name for p in cache.iterdir()) == ["India_1.npz"]
    with np.load(cache/"India_1.npz") as a:
        np.testing.assert_allclose(a["probability"], .8)
        assert a["available"].all() and str(a["recipe_sha256"]) == module.sha(recipe)
    def forbidden(*_):
        raise AssertionError("a completed matching cache must not be recomputed")
    monkeypatch.setattr(module, "optical_ensemble", forbidden)
    module.optical_worker(recipe, "test_core", 0, 2)
    recipe.write_text(json.dumps({"source_recipe_sha256": "frozen-source", "changed": True}))
    with pytest.raises(ValueError, match="another frozen recipe"):
        module.optical_worker(recipe, "test_core", 0, 2)
