import json

import numpy as np
import rasterio
from rasterio.transform import from_origin

from app.scene_builder import ASSET_TYPES, SceneSpec, build_scene


def _raster(path, array, dtype, nodata, count=1):
    profile = {"driver": "GTiff", "width": 512, "height": 512, "count": count, "dtype": dtype,
               "crs": "EPSG:4326", "transform": from_origin(5.0, 9.1, 0.0000898, 0.0000898),
               "nodata": nodata}
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array if count > 1 else array[None])


def _synthetic_chip(tmp_path, with_label=True):
    rng = np.random.default_rng(0)
    vv = rng.normal(-12, 3, (512, 512)).astype("float32")
    vh = rng.normal(-20, 3, (512, 512)).astype("float32")
    vv[:, :40] = np.nan
    vh[:, :40] = np.nan
    prob = np.clip(rng.random((512, 512)), 0, 1).astype("float32")
    prob[:, :40] = -9999
    mask = np.where(prob >= 0.41, 1, 0).astype("uint8")
    mask[:, :40] = 255
    unc = (rng.random((512, 512)) * 0.2).astype("float32")
    unc[:, :40] = -9999
    base = (vh < -23.6).astype("uint8")
    base[:, :40] = 255
    inference = tmp_path / "inf"
    inference.mkdir()
    _raster(tmp_path / "X_1_S1Hand.tif", np.stack([vv, vh]), "float32", None, count=2)
    _raster(inference / "flood_probability.tif", prob, "float32", -9999)
    _raster(inference / "flood_mask.tif", mask, "uint8", 255)
    _raster(inference / "flood_uncertainty.tif", unc, "float32", -9999)
    _raster(inference / "baseline_mask.tif", base, "uint8", 255)
    label = None
    if with_label:
        label = tmp_path / "X_1_LabelHand.tif"
        _raster(label, np.where(prob > 0.5, 1, 0).astype("int16"), "int16", -1)
    spec = SceneSpec(chip_id="X_1", event="Тест", observed_at="2018-09-21", seed=7,
                     s1_path=tmp_path / "X_1_S1Hand.tif", label_path=label,
                     inference_dir=inference, split_part="train")
    return spec, prob


def test_assets_use_pixel_probability_and_valid_area(tmp_path):
    spec, prob = _synthetic_chip(tmp_path)
    row = build_scene(spec, tmp_path / "out")
    meta = json.loads((tmp_path / "out" / "metadata.json").read_text(encoding="utf-8"))
    assert [asset["class"] for asset in meta["assets"]] == [kind[0] for kind in ASSET_TYPES]
    for asset in meta["assets"]:
        assert asset["col"] >= 40
        assert asset["p_flood"] == round(float(prob[asset["row"], asset["col"]]), 5)
        assert asset["status"] == "ok"
    assert row["split_part"] == "train" and meta["split_part"] == "train"
    assert meta["metrics_independent"] is False


def test_scene_without_label_builds(tmp_path):
    spec, _ = _synthetic_chip(tmp_path, with_label=False)
    build_scene(spec, tmp_path / "out")
    meta = json.loads((tmp_path / "out" / "metadata.json").read_text(encoding="utf-8"))
    assert meta["model_quality"] is None and meta["reference_status"] == "no_manual_label"
    assert (tmp_path / "out" / "water_mask.bin").stat().st_size == 512 * 512
