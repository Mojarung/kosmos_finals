"""Package real Sen1Floods11 S1 and frozen ensemble inference for the web demo.

Run after ml/.venv/bin/floodvalue infer on each chip. This script never trains
or tunes the model. Manual labels enter only the diagnostic metrics.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from shutil import copyfile

import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "source"
INFERENCE = ROOT / "data" / "inference"
OUTPUT = ROOT / "data" / "demo"
SOURCE_ROOT = "https://storage.googleapis.com/sen1floods11/v1.1/data/flood_events/HandLabeled"
SCENES = [
    ("Nigeria_812045", "Нигерия", "2018-09-21", 2026),
    ("Ghana_313799", "Гана", "2018-09-18", 2027),
    ("Mekong_333434", "Меконг · Камбоджа", "2018-08-05", 2028),
]
ASSET_TYPES = [
    ("warehouse", "Склад", 10_000_000, 0.25),
    ("substation", "Подстанция", 20_000_000, 0.40),
    ("road_unit", "Участок дороги", 5_000_000, 0.30),
    ("facility", "Производственный объект", 8_000_000, 0.20),
    ("pumping_station", "Насосная станция", 12_000_000, 0.35),
    ("clinic", "Клиника", 30_000_000, 0.15),
    ("school", "Школа", 18_000_000, 0.20),
    ("workshop", "Мастерская", 6_000_000, 0.45),
    ("telecom_node", "Узел связи", 4_000_000, 0.50),
    ("water_intake", "Водозабор", 16_000_000, 0.30),
]


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def quality_metrics(prediction: np.ndarray, truth: np.ndarray, valid: np.ndarray) -> dict:
    positive = prediction[valid] == 1
    actual = truth[valid] == 1
    tp = int((positive & actual).sum())
    fp = int((positive & ~actual).sum())
    fn = int((~positive & actual).sum())
    tn = int((~positive & ~actual).sum())
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "iou": round(tp / (tp + fp + fn), 4) if tp + fp + fn else None,
        "f1": round(2 * tp / (2 * tp + fp + fn), 4) if 2 * tp + fp + fn else None,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "valid_pixels": int(valid.sum()),
    }


def render_layer(mask: np.ndarray, rgb: tuple[int, int, int], alpha: int, path: Path) -> None:
    layer = np.zeros((*mask.shape, 4), dtype=np.uint8)
    layer[mask] = [*rgb, alpha]
    Image.fromarray(layer, "RGBA").save(path, optimize=True)


def prepare_scene(chip: str, label_name: str, observed_at: str, seed: int) -> dict:
    s1_path = SOURCE / f"{chip}_S1Hand.tif"
    label_path = SOURCE / f"{chip}_LabelHand.tif"
    inferred = INFERENCE / chip
    files = {name: inferred / name for name in (
        "flood_probability.tif", "flood_mask.tif", "flood_uncertainty.tif", "baseline_mask.tif")}
    if not all(path.exists() for path in [s1_path, label_path, *files.values()]):
        raise FileNotFoundError(f"Missing S1, label, or frozen-model inference for {chip}")

    with rasterio.open(s1_path) as s1, rasterio.open(label_path) as label:
        assert s1.count == 2 and label.count == 1 and s1.width == label.width == 512
        assert s1.height == label.height == 512 and s1.crs == label.crs
        assert all(abs(a - b) < 1e-9 for a, b in zip(s1.transform, label.transform))
        vv, vh = s1.read()
        truth = label.read(1)
        west, south, east, north = s1.bounds
        transform = s1.transform
        crs = s1.crs

    arrays = {}
    for name, path in files.items():
        with rasterio.open(path) as raster:
            assert raster.count == 1 and raster.width == 512 and raster.height == 512
            assert raster.crs == crs
            assert all(abs(a - b) < 1e-9 for a, b in zip(raster.transform, transform))
            arrays[name] = raster.read(1)
    prob = arrays["flood_probability.tif"]
    prediction = arrays["flood_mask.tif"]
    uncertainty = arrays["flood_uncertainty.tif"]
    baseline = arrays["baseline_mask.tif"]
    radar_valid = np.isfinite(vv) & np.isfinite(vh)
    valid = radar_valid & (truth != -1)
    assert np.all(prediction[radar_valid] == (prob[radar_valid] >= 0.41))

    folder = OUTPUT / chip
    folder.mkdir(parents=True, exist_ok=True)
    for name, path in files.items():
        copyfile(path, folder / name)
    label_mask = np.where(valid, truth, 255).astype(np.uint8)
    for name, array in (("water_mask.bin", label_mask), ("model_mask.bin", prediction),
                        ("baseline_mask.bin", baseline)):
        (folder / name).write_bytes(array.tobytes())
    (folder / "probability.f32").write_bytes(prob.astype("<f4").tobytes())
    (folder / "uncertainty.f32").write_bytes(uncertainty.astype("<f4").tobytes())

    lo, hi = np.percentile(vh[np.isfinite(vh)], [3, 97])
    gray = np.nan_to_num(np.clip((vh - lo) / (hi - lo), 0, 1))
    gray = (gray * 165 + 30).astype(np.uint8)
    rgba = np.stack((gray, gray + 5, gray + 9, np.where(np.isfinite(vh), 255, 0)), axis=-1)
    Image.fromarray(rgba.astype(np.uint8), "RGBA").save(folder / "radar.png", optimize=True)
    render_layer(prediction == 1, (22, 101, 191), 210, folder / "model.png")
    render_layer(baseline == 1, (224, 132, 47), 190, folder / "baseline.png")
    render_layer(label_mask == 1, (20, 163, 146), 190, folder / "reference.png")
    probability_layer = np.zeros((512, 512, 4), dtype=np.uint8)
    probability_layer[:, :, :3] = [34, 121, 208]
    probability_layer[:, :, 3] = np.where(radar_valid, np.clip(prob, 0, 1) ** 0.7 * 220, 0).astype(np.uint8)
    Image.fromarray(probability_layer, "RGBA").save(folder / "probability.png", optimize=True)
    uncertain = np.where(radar_valid, uncertainty, 0)
    scale = float(np.percentile(uncertain[radar_valid], 98)) or 1
    uncertainty_layer = np.zeros((512, 512, 4), dtype=np.uint8)
    uncertainty_layer[:, :, :3] = [226, 151, 52]
    uncertainty_layer[:, :, 3] = (np.clip(uncertain / scale, 0, 1) * 190).astype(np.uint8)
    Image.fromarray(uncertainty_layer, "RGBA").save(folder / "uncertainty.png", optimize=True)

    rows, cols = np.nonzero(radar_valid)
    rng = np.random.default_rng(seed)
    chosen = rng.choice(len(rows), size=10, replace=False)
    assets = []
    for number, ((kind, name, value, vulnerability), index) in enumerate(zip(ASSET_TYPES, chosen), 1):
        row, col = int(rows[index]), int(cols[index])
        longitude, latitude = transform * (col + 0.5, row + 0.5)
        r0, r1, c0, c1 = max(row - 1, 0), min(row + 2, 512), max(col - 1, 0), min(col + 2, 512)
        window_p = prob[r0:r1, c0:c1]
        window_u = uncertainty[r0:r1, c0:c1]
        good = window_p >= 0
        p = float(window_p[good].mean()) if good.any() else None
        u = float(window_u[good].mean()) if good.any() else None
        assets.append({
            "id": f"A{number:02d}", "class": kind, "name": name,
            "value_rub": value, "vulnerability": vulnerability,
            "longitude": longitude, "latitude": latitude,
            "p_flood": round(p, 5) if p is not None else None,
            "uncertainty": round(u, 5) if u is not None else None,
            "baseline_water_at_point": bool(baseline[row, col] == 1),
        })

    transformer = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True)
    x0, y0 = transformer.transform(west, south)
    x1, y1 = transformer.transform(east, north)
    area = abs((x1 - x0) * (y1 - y0)) / 1_000_000
    pixel_area = area / (512 * 512)
    evaluation = json.loads((ROOT / "ml/artifacts/evaluation/evaluation.json").read_text())
    per_chip = evaluation["parts"]["test_core"]["all_water/main"]["per_chip"]
    assert chip in per_chip, "Demo chips must be independent test_core examples"
    source_manifest = [
        {"name": path.name, "url": f"{SOURCE_ROOT}/{layer}/{path.name}", "sha256": checksum(path)}
        for path, layer in ((s1_path, "S1Hand"), (label_path, "LabelHand"))
    ]
    meta = {
        "chip_id": chip, "event": label_name, "observed_at": observed_at,
        "bbox": [west, south, east, north], "width": 512, "height": 512,
        "area_km2": round(area, 3), "portfolio_seed": seed,
        "valid_pixels": int(valid.sum()),
        "water_pixels": int((label_mask == 1).sum()),
        "water_area_km2": round(int((label_mask == 1).sum()) * pixel_area, 3),
        "model_water_pixels": int((prediction == 1).sum()),
        "model_water_area_km2": round(int((prediction == 1).sum()) * pixel_area, 3),
        "baseline_water_pixels": int((baseline == 1).sum()),
        "baseline_water_area_km2": round(int((baseline == 1).sum()) * pixel_area, 3),
        "model_water_labeled_pixels": int(((prediction == 1) & valid).sum()),
        "baseline_water_labeled_pixels": int(((baseline == 1) & valid).sum()),
        "model_threshold": 0.41, "baseline_threshold_db": -23.6,
        "model_quality": quality_metrics(prediction, truth, valid),
        "baseline_quality": quality_metrics(baseline, truth, valid),
        "assets": assets, "source_manifest": source_manifest,
        "inference_manifest": {name: checksum(path) for name, path in files.items()},
        "probability_status": "frozen_ensemble_calibrated_on_validation",
        "reference_status": "manual_label_for_diagnostics_only",
    }
    (folder / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"chip_id": chip, "event": label_name, "observed_at": observed_at,
            "bbox": meta["bbox"], "model_water_area_km2": meta["model_water_area_km2"],
            "reference_water_area_km2": meta["water_area_km2"],
            "model_iou": meta["model_quality"]["iou"]}


def main() -> None:
    scenes = [prepare_scene(*scene) for scene in SCENES]
    (OUTPUT / "scenes.json").write_text(json.dumps({"default_chip_id": scenes[0]["chip_id"],
                                                   "scenes": scenes}, ensure_ascii=False, indent=2), encoding="utf-8")
    evaluation = json.loads((ROOT / "ml/artifacts/evaluation/evaluation.json").read_text())
    quality = {"threshold": evaluation["threshold"], "calibration": evaluation["platt"],
               "parts": {}}
    for part in ("test_core", "bolivia"):
        methods = evaluation["parts"][part]
        probability = evaluation["probability"][part]
        quality["parts"][part] = {
            "baseline": methods["all_water/baseline"]["overall"]["micro"],
            "model": methods["all_water/main"]["overall"]["micro"],
            "ece_raw": probability["reliability_raw"]["ece"],
            "ece_calibrated": probability["reliability_calibrated"]["ece"],
            "brier": probability["reliability_calibrated"]["brier"],
            "uncertainty_error_auroc": probability["uncertainty_member_std"]["auroc"],
            "uncertainty_error_rate_by_decile": probability["uncertainty_member_std"]["error_rate_by_decile"],
            "chips": methods["all_water/main"]["overall"]["chips"],
        }
    (OUTPUT / "quality.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(scenes, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
