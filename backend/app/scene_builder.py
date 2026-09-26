"""Build one map-ready scene from an official Sentinel-1 chip and frozen-model rasters.

Shared by ``scripts/prepare_demo.py`` (the committed demo scenes) and the live
ingest API. Nothing here trains or tunes the model; the manual label only feeds
diagnostic metrics and is optional.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from shutil import copyfile

import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer

SOURCE_ROOT = "https://storage.googleapis.com/sen1floods11/v1.1/data/flood_events/HandLabeled"
DATASET_VERSION = "v1.1"
MODEL_THRESHOLD = 0.41
BASELINE_THRESHOLD_DB = -23.6
SIZE = 512
INFERENCE_FILES = ("flood_probability.tif", "flood_mask.tif", "flood_uncertainty.tif", "baseline_mask.tif")
# Parts the frozen model never saw while training or choosing thresholds, and
# not spatially adjacent to training chips. None means the chip is outside the
# authors' hand-labeled split lists, so it was not used for training either.
INDEPENDENT_PARTS = {"test_core", "bolivia", None}
PROBABILITY_RULE = ("flood_probability.tif value in the pixel that contains the asset point; "
                    "valid 3x3 mean kept as a robustness check (p_window3)")
PERMANENT_RULE = ("JRCWaterHand of Sen1Floods11 v1.1: 1 = water both in 1984 and in 2018 by JRC Global "
                  "Surface Water (Bai et al., Remote Sensing 2021, 13, 2220). Temporary flood = model water "
                  "minus this layer; a portfolio point on it gets EL = 0 with status permanent_water")
PERMANENT_PERIOD_NOTE = ("Observations up to 2018: fully before 2019 events; for 2016-2018 events the period ends "
                         "near the event, but water in both 1984 and 2018 selects long-lived water bodies")
PORTFOLIO_RULE = ("numpy default_rng(seed).choice over pixels with finite VV and VH, 10 without "
                  "replacement, one per case asset type in table order; independent of p, masks and labels")
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


@dataclass(frozen=True)
class SceneSpec:
    chip_id: str
    event: str
    observed_at: str
    seed: int
    s1_path: Path
    label_path: Path | None
    inference_dir: Path
    split_part: str | None = None
    origin: str = "demo"
    # file name -> {"generation", "updated", "md5_b64"} from ml/data/.../download_manifest.json
    source_meta: dict = field(default_factory=dict)
    jrc_path: Path | None = None


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_source_meta(manifest_path: Path) -> dict:
    """Index a download manifest of the official bucket by file name."""
    if not manifest_path.exists():
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {Path(item["name"]).name: {key: item.get(key) for key in ("generation", "updated", "md5_b64")}
            for item in manifest.get("objects", [])}


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


def _same_grid(raster, transform, crs) -> bool:
    return (raster.width == SIZE and raster.height == SIZE and raster.crs == crs
            and all(abs(a - b) < 1e-9 for a, b in zip(raster.transform, transform)))


def _read_inputs(spec: SceneSpec) -> tuple[dict, np.ndarray, np.ndarray, np.ndarray, dict]:
    files = {name: spec.inference_dir / name for name in INFERENCE_FILES}
    missing = [str(path) for path in [spec.s1_path, *files.values()] if not path.exists()]
    if spec.label_path is not None and not spec.label_path.exists():
        missing.append(str(spec.label_path))
    if missing:
        raise FileNotFoundError(f"Missing S1, label or frozen-model inference for {spec.chip_id}: {missing}")
    with rasterio.open(spec.s1_path) as s1:
        if s1.count != 2 or s1.width != SIZE or s1.height != SIZE:
            raise ValueError(f"{spec.s1_path.name}: expected a 2-band {SIZE}x{SIZE} VV/VH chip")
        vv, vh = s1.read().astype(np.float32)
        grid = {"bounds": list(s1.bounds), "transform": s1.transform, "crs": s1.crs}
    truth = np.full((SIZE, SIZE), -1, dtype=np.int16)
    if spec.label_path is not None:
        with rasterio.open(spec.label_path) as label:
            if label.count != 1 or not _same_grid(label, grid["transform"], grid["crs"]):
                raise ValueError(f"{spec.label_path.name}: label grid differs from S1")
            truth = label.read(1)
    arrays = {}
    for name, path in files.items():
        with rasterio.open(path) as raster:
            if raster.count != 1 or not _same_grid(raster, grid["transform"], grid["crs"]):
                raise ValueError(f"{name}: inference grid differs from S1")
            arrays[name] = raster.read(1)
    return arrays, vv, vh, truth, {**grid, "files": files}


def _portfolio(spec: SceneSpec, radar_valid: np.ndarray, prob: np.ndarray, uncertainty: np.ndarray,
               baseline: np.ndarray, transform) -> list[dict]:
    rows, cols = np.nonzero(radar_valid)
    rng = np.random.default_rng(spec.seed)
    chosen = rng.choice(len(rows), size=len(ASSET_TYPES), replace=False)
    assets = []
    for number, ((kind, name, value, vulnerability), index) in enumerate(zip(ASSET_TYPES, chosen), 1):
        row, col = int(rows[index]), int(cols[index])
        longitude, latitude = transform @ (col + 0.5, row + 0.5)
        pixel_p = float(prob[row, col])
        pixel_u = float(uncertainty[row, col])
        window_p = prob[max(row - 1, 0):min(row + 2, SIZE), max(col - 1, 0):min(col + 2, SIZE)]
        window_u = uncertainty[max(row - 1, 0):min(row + 2, SIZE), max(col - 1, 0):min(col + 2, SIZE)]
        good = window_p >= 0
        valid_pixel = pixel_p >= 0
        assets.append({
            "id": f"A{number:02d}", "class": kind, "name": name,
            "value_rub": value, "vulnerability": vulnerability,
            "longitude": longitude, "latitude": latitude, "row": row, "col": col,
            "p_flood": round(pixel_p, 5) if valid_pixel else None,
            "uncertainty": round(pixel_u, 5) if valid_pixel else None,
            "p_window3": round(float(window_p[good].mean()), 5) if good.any() else None,
            "uncertainty_window3": round(float(window_u[good].mean()), 5) if good.any() else None,
            "baseline_water_at_point": bool(baseline[row, col] == 1),
            "status": "ok" if valid_pixel else "no_data",
        })
    return assets


def _render(folder: Path, vh: np.ndarray, radar_valid: np.ndarray, prediction: np.ndarray,
            baseline: np.ndarray, label_mask: np.ndarray, prob: np.ndarray, uncertainty: np.ndarray) -> None:
    lo, hi = np.percentile(vh[np.isfinite(vh)], [3, 97])
    gray = np.nan_to_num(np.clip((vh - lo) / (hi - lo), 0, 1))
    gray = (gray * 165 + 30).astype(np.uint8)
    rgba = np.stack((gray, gray + 5, gray + 9, np.where(np.isfinite(vh), 255, 0)), axis=-1)
    Image.fromarray(rgba.astype(np.uint8), "RGBA").save(folder / "radar.png", optimize=True)
    render_layer(prediction == 1, (22, 101, 191), 210, folder / "model.png")
    render_layer(baseline == 1, (224, 132, 47), 190, folder / "baseline.png")
    render_layer(label_mask == 1, (20, 163, 146), 190, folder / "reference.png")
    probability_layer = np.zeros((SIZE, SIZE, 4), dtype=np.uint8)
    probability_layer[:, :, :3] = [34, 121, 208]
    probability_layer[:, :, 3] = np.where(radar_valid, np.clip(prob, 0, 1) ** 0.7 * 220, 0).astype(np.uint8)
    Image.fromarray(probability_layer, "RGBA").save(folder / "probability.png", optimize=True)
    uncertain = np.where(radar_valid, uncertainty, 0)
    scale = float(np.percentile(uncertain[radar_valid], 98)) or 1
    uncertainty_layer = np.zeros((SIZE, SIZE, 4), dtype=np.uint8)
    uncertainty_layer[:, :, :3] = [226, 151, 52]
    uncertainty_layer[:, :, 3] = (np.clip(uncertain / scale, 0, 1) * 190).astype(np.uint8)
    Image.fromarray(uncertainty_layer, "RGBA").save(folder / "uncertainty.png", optimize=True)


def build_scene(spec: SceneSpec, out_dir: Path) -> dict:
    """Write rasters, map layers, the seeded 10-asset portfolio and metadata.json into out_dir."""
    arrays, vv, vh, truth, grid = _read_inputs(spec)
    prob = arrays["flood_probability.tif"]
    prediction = arrays["flood_mask.tif"]
    uncertainty = arrays["flood_uncertainty.tif"]
    baseline = arrays["baseline_mask.tif"]
    radar_valid = np.isfinite(vv) & np.isfinite(vh)
    if not radar_valid.any():
        raise ValueError(f"{spec.chip_id}: the S1 chip has no valid VV/VH pixels")
    valid = radar_valid & (truth != -1)
    if not np.all(prediction[radar_valid] == (prob[radar_valid] >= MODEL_THRESHOLD)):
        raise ValueError(f"{spec.chip_id}: flood_mask.tif is not p >= {MODEL_THRESHOLD}")

    out_dir.mkdir(parents=True, exist_ok=True)
    for name, path in grid["files"].items():
        if path.resolve() != (out_dir / name).resolve():
            copyfile(path, out_dir / name)
    label_mask = np.where(valid, truth, 255).astype(np.uint8)
    for name, array in (("water_mask.bin", label_mask), ("model_mask.bin", prediction),
                        ("baseline_mask.bin", baseline)):
        (out_dir / name).write_bytes(np.ascontiguousarray(array, dtype=np.uint8).tobytes())
    (out_dir / "probability.f32").write_bytes(prob.astype("<f4").tobytes())
    (out_dir / "uncertainty.f32").write_bytes(uncertainty.astype("<f4").tobytes())
    _render(out_dir, vh, radar_valid, prediction, baseline, label_mask, prob, uncertainty)

    transform = grid["transform"]
    assets = _portfolio(spec, radar_valid, prob, uncertainty, baseline, transform)
    west, south, east, north = grid["bounds"]
    to_equal_area = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True)
    x0, y0 = to_equal_area.transform(west, south)
    x1, y1 = to_equal_area.transform(east, north)
    area = abs((x1 - x0) * (y1 - y0)) / 1_000_000
    pixel_area = area / (SIZE * SIZE)
    has_label = bool(valid.any())
    sources = [(spec.s1_path, "S1Hand", "model_input")]
    if spec.label_path is not None:
        sources.append((spec.label_path, "LabelHand", "diagnostic_label_only"))
    source_manifest = [{
        "name": path.name, "url": f"{SOURCE_ROOT}/{layer}/{path.name}", "sha256": checksum(path),
        "version": DATASET_VERSION, "layer": layer, "role": role,
        "gcs_generation": spec.source_meta.get(path.name, {}).get("generation"),
        "published_at": spec.source_meta.get(path.name, {}).get("updated"),
    } for path, layer, role in sources]
    independent = spec.split_part in INDEPENDENT_PARTS
    meta = {
        "chip_id": spec.chip_id, "event": spec.event, "observed_at": spec.observed_at,
        "bbox": [west, south, east, north], "width": SIZE, "height": SIZE,
        "area_km2": round(area, 3), "portfolio_seed": spec.seed, "portfolio_rule": PORTFOLIO_RULE,
        "probability_rule": PROBABILITY_RULE,
        "split_part": spec.split_part, "metrics_independent": independent, "origin": spec.origin,
        "valid_pixels": int(valid.sum()),
        "water_pixels": int((label_mask == 1).sum()),
        "water_area_km2": round(int((label_mask == 1).sum()) * pixel_area, 3),
        "model_water_pixels": int((prediction == 1).sum()),
        "model_water_area_km2": round(int((prediction == 1).sum()) * pixel_area, 3),
        "baseline_water_pixels": int((baseline == 1).sum()),
        "baseline_water_area_km2": round(int((baseline == 1).sum()) * pixel_area, 3),
        "model_water_labeled_pixels": int(((prediction == 1) & valid).sum()),
        "baseline_water_labeled_pixels": int(((baseline == 1) & valid).sum()),
        "model_threshold": MODEL_THRESHOLD, "baseline_threshold_db": BASELINE_THRESHOLD_DB,
        "model_quality": quality_metrics(prediction, truth, valid) if has_label else None,
        "baseline_quality": quality_metrics(baseline, truth, valid) if has_label else None,
        "assets": assets, "source_manifest": source_manifest,
        "inference_manifest": {name: checksum(out_dir / name) for name in INFERENCE_FILES},
        "probability_status": "frozen_ensemble_calibrated_on_validation",
        "reference_status": "manual_label_for_diagnostics_only" if spec.label_path else "no_manual_label",
    }
    if spec.jrc_path is not None:
        meta = add_permanent_water(out_dir, spec.jrc_path, meta, spec.source_meta)
    (out_dir / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"chip_id": spec.chip_id, "event": spec.event, "observed_at": spec.observed_at,
            "bbox": meta["bbox"], "model_water_area_km2": meta["model_water_area_km2"],
            "reference_water_area_km2": meta["water_area_km2"],
            "model_iou": meta["model_quality"]["iou"] if meta["model_quality"] else None,
            "flood_water_area_km2": meta.get("flood_water_area_km2"),
            "split_part": spec.split_part, "metrics_independent": independent, "origin": spec.origin}


def add_permanent_water(out_dir: Path, jrc_path: Path, meta: dict, source_meta: dict | None = None) -> dict:
    """Subtract JRC permanent water from a built scene; returns the updated metadata.

    Writes permanent_water.tif (same grid as the model rasters, 255 where S1 is invalid),
    permanent_mask.bin for the API and permanent.png for the map. The model rasters
    and their checksums do not change.
    """
    with rasterio.open(out_dir / "flood_mask.tif") as model_raster:
        prediction = model_raster.read(1)
        profile = model_raster.profile
        transform, crs = model_raster.transform, model_raster.crs
    with rasterio.open(jrc_path) as jrc:
        if jrc.count != 1 or not _same_grid(jrc, transform, crs):
            raise ValueError(f"{jrc_path.name}: JRC grid differs from S1")
        permanent_raw = jrc.read(1)
    radar_valid = prediction != 255
    permanent = radar_valid & (permanent_raw == 1)
    layer = np.where(radar_valid, permanent, 255).astype(np.uint8)
    (out_dir / "permanent_mask.bin").write_bytes(np.ascontiguousarray(layer).tobytes())
    with rasterio.open(out_dir / "permanent_water.tif", "w", **{**profile, "dtype": "uint8", "nodata": 255,
                                                                "compress": "deflate"}) as target:
        target.write(layer, 1)
    render_layer(permanent, (18, 58, 118), 220, out_dir / "permanent.png")
    pixel_area = meta["area_km2"] / (meta["width"] * meta["height"])
    water = prediction == 1
    assets = []
    for asset in meta["assets"]:
        on_permanent = bool(permanent[asset["row"], asset["col"]]) if asset.get("p_flood") is not None else None
        assets.append({**asset, "permanent_water_at_point": on_permanent})
    meta_source = (source_meta or {}).get(jrc_path.name, {})
    manifest = [item for item in meta["source_manifest"] if item.get("layer") != "JRCWaterHand"]
    manifest.append({
        "name": jrc_path.name, "url": f"{SOURCE_ROOT}/JRCWaterHand/{jrc_path.name}", "sha256": checksum(jrc_path),
        "version": DATASET_VERSION, "layer": "JRCWaterHand", "role": "permanent_water_reference",
        "gcs_generation": meta_source.get("generation"), "published_at": meta_source.get("updated"),
    })
    return {
        **meta, "assets": assets, "source_manifest": manifest,
        "permanent_rule": PERMANENT_RULE, "permanent_period_note": PERMANENT_PERIOD_NOTE,
        "permanent_water_pixels": int(permanent.sum()),
        "permanent_water_area_km2": round(int(permanent.sum()) * pixel_area, 3),
        "flood_water_pixels": int((water & ~permanent).sum()),
        "flood_water_area_km2": round(int((water & ~permanent).sum()) * pixel_area, 3),
        "permanent_manifest": {"permanent_water.tif": checksum(out_dir / "permanent_water.tif")},
    }
