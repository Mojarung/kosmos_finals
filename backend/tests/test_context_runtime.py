"""The live context model runs only when its fold weights are really on disk."""

def test_live_frames_skip_the_context_model_without_its_weights(tmp_path, monkeypatch):
    import json

    from app import model_runtime
    recipe = tmp_path / "recipe.json"
    (tmp_path / "m").mkdir()
    (tmp_path / "m" / "fold0.safetensors").write_bytes(b"version https://git-lfs.github.com/spec/v1\n")
    recipe.write_text(json.dumps({"members": {"m": {"weights": [{"path": "m/fold0.safetensors"}]}}}))
    monkeypatch.setattr(model_runtime, "CONTEXT_RECIPE", recipe)
    assert model_runtime.context_enabled() is False
    (tmp_path / "m" / "fold0.safetensors").write_bytes(b"0" * 2_000_000)
    assert model_runtime.context_enabled() is True
    monkeypatch.setenv("FLOODVALUE_LIVE_MODEL", "next_optical")
    assert model_runtime.context_enabled() is False
