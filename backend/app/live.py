"""Acquire a real Sentinel-1 RTC observation and run the frozen flood model.

The acquisition and model output are cached by STAC item and geographic rectangle.
The asset portfolio is a disclosed scenario because no operator inventory is supplied.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen

import numpy as np
import rasterio
from PIL import Image
from pyproj import Geod
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT

from .catalog import LIVE_DIR, metadata
from .planning import intersects, rectangle_area_km2

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel-1-rtc"
ROOT = Path(__file__).resolve().parents[2]
MODEL = Path(os.environ.get("MODEL_DIR", ROOT / "ml" / "artifacts" / "model"))
CALIBRATION = Path(os.environ.get("CALIBRATION_FILE", ROOT / "ml" / "artifacts" / "evaluation" / "calibration.json"))
GRID = 512
GEOD = Geod(ellps="WGS84")
_lock = threading.Lock()
_members = None


class LiveError(Exception):
    pass


def _json(url: str) -> dict:
    try:
        with urlopen(url, timeout=25) as response:
            return json.load(response)
    except Exception as error:
        raise LiveError(f"Каталог Sentinel-1 сейчас недоступен: {error}") from error


def validate_area(bounds: list[float]) -> float:
    area = rectangle_area_km2(bounds)
    width = GEOD.inv(bounds[0], bounds[1], bounds[2], bounds[1])[2] / 1000
    height = GEOD.inv(bounds[0], bounds[1], bounds[0], bounds[3])[2] / 1000
    if area > 300:
        raise LiveError("Область больше 300 км². Приблизьте карту и выделите меньший участок.")
    if width < 1.25 or height < 1.25:
        raise LiveError("Для зон съёмки нужна рамка не меньше 1,25 км по каждой стороне.")
    return area


def _covers(bounds: list[float], scene: list[float]) -> bool:
    return scene[0] <= bounds[0] and scene[1] <= bounds[1] and scene[2] >= bounds[2] and scene[3] >= bounds[3]


def _groups(bounds: list[float]) -> list[dict]:
    query = urlencode({
        "collections": "sentinel-1-rtc", "bbox": ",".join(map(str, bounds)),
        "datetime": "2024-01-01/..", "sortby": "-datetime", "limit": 80,
    })
    features = _json(f"{STAC}?{query}").get("features", [])
    by_pass: dict[tuple, list[dict]] = {}
    for feature in features:
        if not {"vv", "vh"}.issubset(feature.get("assets", {})) or not intersects(bounds, feature["bbox"]):
            continue
        props = feature["properties"]
        key = (props.get("platform"), props.get("sat:absolute_orbit"), props.get("s1:datatake_id"))
        if key[1] is None or key[2] is None:
            key = (feature["id"],)
        by_pass.setdefault(key, []).append(feature)
    result = []
    for group in by_pass.values():
        group.sort(key=lambda item: item["properties"]["datetime"], reverse=True)
        union = [min(item["bbox"][0] for item in group), min(item["bbox"][1] for item in group),
                 max(item["bbox"][2] for item in group), max(item["bbox"][3] for item in group)]
        overlap = [max(bounds[0], union[0]), max(bounds[1], union[1]),
                   min(bounds[2], union[2]), min(bounds[3], union[3])]
        result.append({
            "features": group, "bbox": union, "full_bbox": _covers(bounds, union),
            "bbox_coverage_estimate": min(1.0, rectangle_area_km2(overlap) / rectangle_area_km2(bounds)),
        })
    result.sort(key=lambda row: (row["full_bbox"], row["bbox_coverage_estimate"],
                                  row["features"][0]["properties"]["datetime"]), reverse=True)
    return result


def discover(bounds: list[float]) -> list[dict]:
    validate_area(bounds)
    result = []
    for row in _groups(bounds)[:6]:
        first = row["features"][0]
        result.append({
            "id": first["id"], "datetime": first["properties"]["datetime"],
            "bbox": row["bbox"], "platform": first["properties"].get("platform", "Sentinel-1"),
            "collection": "sentinel-1-rtc", "polarizations": ["VV", "VH"],
            "item_count": len(row["features"]), "full_bbox": row["full_bbox"],
            "bbox_coverage_estimate": round(row["bbox_coverage_estimate"], 3),
        })
    return result


def _features(bounds: list[float], item_id: str) -> list[dict]:
    for row in _groups(bounds):
        if any(feature["id"] == item_id for feature in row["features"]):
            return row["features"]
    raise LiveError("Выбранный снимок не найден для этой области. Обновите список снимков.")


def _read_band(href: str, token: str, bounds: list[float]) -> np.ndarray:
    host = urlparse(href).hostname or ""
    if not host.endswith(".blob.core.windows.net"):
        raise LiveError("Неподдерживаемый адрес растра в каталоге")
    signed = href + "?" + token
    transform = from_bounds(*bounds, GRID, GRID)
    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="2"):
            with rasterio.open(signed) as source:
                with WarpedVRT(source, crs="EPSG:4326", transform=transform,
                               width=GRID, height=GRID, resampling=Resampling.bilinear) as warped:
                    values = warped.read(1, masked=True).astype("float32")
    except Exception as error:
        raise LiveError(f"Не удалось прочитать Sentinel-1 COG: {error}") from error
    linear = np.asarray(values.filled(np.nan), dtype=np.float32)
    linear[linear <= 0] = np.nan
    return (10 * np.log10(linear)).astype(np.float32)


def _ensemble():
    global _members
    if _members is None:
        from floodvalue.ensemble import load_members
        dirs = sorted((MODEL / "members").glob("m*"))
        if len(dirs) != 5 or any((path / "model.safetensors").stat().st_size < 1_000_000 for path in dirs):
            raise LiveError("Веса ансамбля не найдены. Проверьте Git LFS в каталоге ml/artifacts/model.")
        _members = load_members(dirs)
    return _members


def _png(path: Path, rgba: np.ndarray) -> None:
    Image.fromarray(rgba.astype("uint8"), "RGBA").save(path)


def _overlays(folder: Path, vh: np.ndarray, probability: np.ndarray,
              uncertainty: np.ndarray, model: np.ndarray, baseline: np.ndarray, valid: np.ndarray) -> None:
    radar = np.nan_to_num((vh + 33) / 28, nan=0.0).clip(0, 1)
    shade = (radar * 205 + 25).astype("uint8")
    _png(folder / "radar.png", np.stack([shade, shade, shade, np.where(valid, 245, 0)], axis=-1))
    for name, water, color in (("model", model, (31, 117, 176)), ("baseline", baseline, (209, 129, 48))):
        rgba = np.zeros((GRID, GRID, 4), dtype="uint8")
        rgba[..., :3] = color
        rgba[..., 3] = np.where(water, 230, 0)
        _png(folder / f"{name}.png", rgba)
    rgba = np.zeros((GRID, GRID, 4), dtype="uint8")
    rgba[..., 0] = 22 + probability * 43
    rgba[..., 1] = 92 + probability * 77
    rgba[..., 2] = 165 + probability * 70
    rgba[..., 3] = np.where(valid, 80 + probability * 160, 0)
    _png(folder / "probability.png", rgba)
    rgba[..., 0] = 231
    rgba[..., 1] = 151
    rgba[..., 2] = 61
    rgba[..., 3] = np.where(valid, np.clip(uncertainty / 0.25, 0, 1) * 235, 0)
    _png(folder / "uncertainty.png", rgba)
    _png(folder / "reference.png", np.zeros((GRID, GRID, 4), dtype="uint8"))


def _raster(path: Path, values: np.ndarray, bounds: list[float], dtype: str, nodata: float) -> None:
    with rasterio.open(path, "w", driver="GTiff", height=GRID, width=GRID, count=1,
                       dtype=dtype, crs="EPSG:4326", transform=from_bounds(*bounds, GRID, GRID),
                       nodata=nodata, compress="deflate") as target:
        target.write(values.astype(dtype), 1)


def _portfolio(bounds: list[float], probability: np.ndarray, spread: np.ndarray,
               baseline: np.ndarray, valid: np.ndarray) -> list[dict]:
    templates = metadata("Nigeria_812045")["assets"]
    rows = [0.12, 0.32, 0.52, 0.72, 0.88]
    sites = [(x, y) for y in (0.27, 0.73) for x in rows]
    result = []
    for template, (xf, yf) in zip(templates, sites):
        row, col = int(yf * (GRID - 1)), int(xf * (GRID - 1))
        neighborhood = valid[max(0, row-1):row+2, max(0, col-1):col+2]
        p_values = probability[max(0, row-1):row+2, max(0, col-1):col+2][neighborhood]
        result.append({
            **{key: template[key] for key in ("id", "class", "name", "value_rub", "vulnerability")},
            "longitude": bounds[0] + (bounds[2] - bounds[0]) * xf,
            "latitude": bounds[3] - (bounds[3] - bounds[1]) * yf,
            "p_flood": round(float(probability[row, col]), 5) if valid[row, col] else None,
            "p_window3": round(float(p_values.mean()), 5) if p_values.size else None,
            "uncertainty": round(float(spread[row, col]), 5) if valid[row, col] else None,
            "baseline_water_at_point": bool(baseline[row, col]) if valid[row, col] else None,
            "portfolio_status": "scenario_locations_and_values",
        })
    return result


def prepare(bounds: list[float], item_id: str) -> dict:
    area = validate_area(bounds)
    features = _features(bounds, item_id)
    feature = features[0]
    cache_key = b"live-v3:" + ("|".join(item["id"] for item in features) + ":" +
                             ",".join(f"{n:.6f}" for n in bounds)).encode()
    cache_key += hashlib.sha256((MODEL / "model_card.json").read_bytes() + CALIBRATION.read_bytes()).digest()
    digest = hashlib.sha256(cache_key).hexdigest()[:16]
    scene_id = f"live_{digest}"
    folder = LIVE_DIR / scene_id
    if (folder / "metadata.json").is_file():
        return json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    with _lock:
        if (folder / "metadata.json").is_file():
            return json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        token = _json(SAS)["token"]
        vv = np.full((GRID, GRID), np.nan, dtype=np.float32)
        vh = np.full((GRID, GRID), np.nan, dtype=np.float32)
        valid = np.zeros((GRID, GRID), dtype=bool)
        used_features = []
        for source in features:
            source_vv = _read_band(source["assets"]["vv"]["href"], token, bounds)
            source_vh = _read_band(source["assets"]["vh"]["href"], token, bounds)
            fill = np.isfinite(source_vv) & np.isfinite(source_vh) & ~valid
            vv[fill], vh[fill] = source_vv[fill], source_vh[fill]
            valid |= fill
            if fill.any():
                used_features.append(source)
            if valid.all():
                break
        s1 = np.stack([vv, vh])
        if valid.mean() < 0.05:
            raise LiveError("На выбранном снимке нет валидных VV/VH пикселей в области. Выберите другую дату.")
        import torch
        from floodvalue.ensemble import run_ensemble
        from floodvalue.infer import load_calibration
        torch.set_num_threads(2)
        calibration, threshold = load_calibration(CALIBRATION)
        with torch.inference_mode():
            result = run_ensemble(_ensemble(), [s1], calibration)
        probability = np.asarray(result.prob[0], dtype=np.float32)
        spread = np.asarray(result.member_std[0], dtype=np.float32)
        model = valid & (probability >= threshold)
        baseline = valid & (vh < -23.6)
        probability[~valid] = 0
        spread[~valid] = 0
        folder.mkdir(parents=True, exist_ok=True)
        _overlays(folder, vh, probability, spread, model, baseline, valid)
        (folder / "model_mask.bin").write_bytes(np.where(valid, model, 255).astype("uint8").tobytes())
        (folder / "baseline_mask.bin").write_bytes(np.where(valid, baseline, 255).astype("uint8").tobytes())
        (folder / "water_mask.bin").write_bytes(np.full((GRID, GRID), 255, dtype="uint8").tobytes())
        (folder / "probability.f32").write_bytes(probability.tobytes())
        (folder / "uncertainty.f32").write_bytes(spread.tobytes())
        _raster(folder / "flood_probability.tif", np.where(valid, probability, -9999), bounds, "float32", -9999)
        _raster(folder / "flood_uncertainty.tif", np.where(valid, spread, -9999), bounds, "float32", -9999)
        _raster(folder / "flood_mask.tif", np.where(valid, model, 255), bounds, "uint8", 255)
        _raster(folder / "baseline_mask.tif", np.where(valid, baseline, 255), bounds, "uint8", 255)
        metadata_record = {
            "chip_id": scene_id, "event": "Выбранная область", "observed_at": feature["properties"]["datetime"],
            "bbox": bounds, "width": GRID, "height": GRID, "area_km2": round(area, 3),
            "valid_pixels": int(valid.sum()), "water_pixels": None, "water_area_km2": None,
            "model_water_pixels": int(model.sum()), "model_water_area_km2": round(float(model.sum() / (GRID*GRID) * area), 3),
            "baseline_water_pixels": int(baseline.sum()), "baseline_water_area_km2": round(float(baseline.sum() / (GRID*GRID) * area), 3),
            "model_threshold": threshold, "baseline_threshold_db": -23.6,
            "portfolio_seed": 2026, "assets": _portfolio(bounds, probability, spread, baseline, valid),
            "source_kind": "live_sentinel1_rtc", "source_item_id": feature["id"],
            "source_item_ids": [source["id"] for source in used_features],
            "source_manifest": [{"name": f"Sentinel-1 RTC {band.upper()} · {source['id']}",
                                 "url": source["assets"][band]["href"]}
                                for source in used_features for band in ("vv", "vh")],
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "pixel_size_m_approx": round((area * 1_000_000 / (GRID * GRID)) ** 0.5, 1),
            "portfolio_status": "scenario_locations_and_values",
            "reference_status": "no_manual_label",
        }
        (folder / "metadata.json").write_text(json.dumps(metadata_record, ensure_ascii=False, indent=2), encoding="utf-8")
        return metadata_record
