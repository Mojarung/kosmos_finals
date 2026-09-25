import json

from app.scene_builder import ASSET_TYPES, SceneSpec, build_scene
from synthetic import NODATA_COLUMNS, make_chip


def _spec(tmp_path, with_label=True):
    files = make_chip(tmp_path / "source", "X_1", with_label=with_label)
    spec = SceneSpec(chip_id="X_1", event="Тест", observed_at="2018-09-21", seed=7,
                     s1_path=files["s1"], label_path=files["label"],
                     inference_dir=files["inference"], split_part="train")
    return spec, files["prob"]


def test_assets_use_pixel_probability_and_valid_area(tmp_path):
    spec, prob = _spec(tmp_path)
    row = build_scene(spec, tmp_path / "out")
    meta = json.loads((tmp_path / "out" / "metadata.json").read_text(encoding="utf-8"))
    assert [asset["class"] for asset in meta["assets"]] == [kind[0] for kind in ASSET_TYPES]
    for asset in meta["assets"]:
        assert asset["col"] >= NODATA_COLUMNS
        assert asset["p_flood"] == round(float(prob[asset["row"], asset["col"]]), 5)
        assert asset["status"] == "ok"
    assert row["split_part"] == "train" and meta["split_part"] == "train"
    assert meta["metrics_independent"] is False


def test_scene_without_label_builds(tmp_path):
    spec, _ = _spec(tmp_path, with_label=False)
    build_scene(spec, tmp_path / "out")
    meta = json.loads((tmp_path / "out" / "metadata.json").read_text(encoding="utf-8"))
    assert meta["model_quality"] is None and meta["reference_status"] == "no_manual_label"
    assert (tmp_path / "out" / "water_mask.bin").stat().st_size == 512 * 512
