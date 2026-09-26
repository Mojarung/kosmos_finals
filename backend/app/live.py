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

from .catalog import LIVE_DIR
from .planning import intersects, rectangle_area_km2
from .scene_builder import ASSET_TYPES

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel-1-rtc"
ROOT = Path(__file__).resolve().parents[2]
MODEL = Path(os.environ.get("MODEL_DIR", ROOT / "ml" / "artifacts" / "model"))
CALIBRATION = Path(os.environ.get("CALIBRATION_FILE", ROOT / "ml" / "artifacts" / "evaluation" / "calibration.json"))
# RTC gamma0 of Planetary Computer is brighter over water than the Sen1Floods11 GRD the model
# learned on; this per-band mapping (fitted on train chips) brings it back to GRD dB.
HARMONIZATION = Path(os.environ.get("RTC_HARMONIZATION_FILE", ROOT / "ml" / "artifacts" / "rtc" / "harmonization.json"))
GRID = 512
PIXEL_M = 10
MAX_SIDE_PIXELS = 2048
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
    if width * 1000 > MAX_SIDE_PIXELS * PIXEL_M or height * 1000 > MAX_SIDE_PIXELS * PIXEL_M:
        raise LiveError("Сторона рамки больше 20 км. Разделите область на соседние участки по 20 км.")
    return area


def grid_size(bounds: list[float]) -> tuple[int, int]:
    """Approximately 10 m per pixel at every location; dimensions vary with the ROI."""
    width_m = GEOD.inv(bounds[0], bounds[1], bounds[2], bounds[1])[2]
    height_m = GEOD.inv(bounds[0], bounds[1], bounds[0], bounds[3])[2]
    return (max(128, int(np.ceil(width_m / PIXEL_M / 32) * 32)),
            max(128, int(np.ceil(height_m / PIXEL_M / 32) * 32)))


def _covers(bounds: list[float], scene: list[float]) -> bool:
    return scene[0] <= bounds[0] and scene[1] <= bounds[1] and scene[2] >= bounds[2] and scene[3] >= bounds[3]


def _groups(bounds: list[float], period: str = "2024-01-01/..", limit: int = 80) -> list[dict]:
    query = urlencode({
        "collections": "sentinel-1-rtc", "bbox": ",".join(map(str, bounds)),
        "datetime": period, "sortby": "-datetime", "limit": limit,
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


def _read_band(href: str, token: str, bounds: list[float], shape: tuple[int, int] = (GRID, GRID)) -> np.ndarray:
    host = urlparse(href).hostname or ""
    if not host.endswith(".blob.core.windows.net"):
        raise LiveError("Неподдерживаемый адрес растра в каталоге")
    signed = href + "?" + token
    height, width = shape
    transform = from_bounds(*bounds, width, height)
    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="2"):
            with rasterio.open(signed) as source:
                with WarpedVRT(source, crs="EPSG:4326", transform=transform,
                               width=width, height=height, resampling=Resampling.bilinear) as warped:
                    values = warped.read(1, masked=True).astype("float32")
    except Exception as error:
        raise LiveError(f"Не удалось прочитать снимок Sentinel-1: {error}") from error
    linear = np.asarray(values.filled(np.nan), dtype=np.float32)
    linear[linear <= 0] = np.nan
    return (10 * np.log10(linear)).astype(np.float32)


def harmonization() -> dict | None:
    return json.loads(HARMONIZATION.read_text(encoding="utf-8")) if HARMONIZATION.is_file() else None


def harmonize(vv: np.ndarray, vh: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Map RTC dB to Sen1Floods11 GRD dB band by band; unchanged when no mapping is shipped."""
    table = harmonization()
    if table is None:
        return vv, vh
    mapped = []
    for band, values in (("vv", vv), ("vh", vh)):
        curve = table[band]
        converted = np.interp(values, curve["rtc_db"], curve["grd_db"]).astype(np.float32)
        mapped.append(np.where(np.isfinite(values), converted, np.nan).astype(np.float32))
    return mapped[0], mapped[1]


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
    height, width = vh.shape
    radar = np.nan_to_num((vh + 33) / 28, nan=0.0).clip(0, 1)
    shade = (radar * 205 + 25).astype("uint8")
    _png(folder / "radar.png", np.stack([shade, shade, shade, np.where(valid, 245, 0)], axis=-1))
    for name, water, color in (("model", model, (31, 117, 176)), ("baseline", baseline, (209, 129, 48))):
        rgba = np.zeros((height, width, 4), dtype="uint8")
        rgba[..., :3] = color
        rgba[..., 3] = np.where(water, 230, 0)
        _png(folder / f"{name}.png", rgba)
    rgba = np.zeros((height, width, 4), dtype="uint8")
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
    height, width = values.shape
    with rasterio.open(path, "w", driver="GTiff", height=height, width=width, count=1,
                       dtype=dtype, crs="EPSG:4326", transform=from_bounds(*bounds, width, height),
                       nodata=nodata, compress="deflate") as target:
        target.write(values.astype(dtype), 1)


PORTFOLIO_SEED = 2026
PORTFOLIO_RULE = ("numpy default_rng(2026).choice over pixels with valid VV and VH, 10 without replacement, "
                  "one per case asset type in table order; coordinates at the pixel centre; "
                  "independent of p, masks and labels")
LICENSE = {"id": "CC-BY-4.0", "url": "https://creativecommons.org/licenses/by/4.0/",
           "providers": ["Catalyst", "Microsoft"],
           "attribution": "Sentinel-1 RTC derived from Copernicus Sentinel-1 GRD (ESA), processed by Catalyst"}


def _portfolio(bounds: list[float], probability: np.ndarray, spread: np.ndarray,
               baseline: np.ndarray, valid: np.ndarray) -> list[dict]:
    """Seeded random points over valid pixels, placed at pixel centres so p is read where the point is."""
    rows, cols = np.nonzero(valid)
    chosen = np.random.default_rng(PORTFOLIO_SEED).choice(len(rows), size=len(ASSET_TYPES), replace=False)
    height, width = valid.shape
    dx = (bounds[2] - bounds[0]) / width
    dy = (bounds[3] - bounds[1]) / height
    result = []
    for number, ((kind, name, value, vulnerability), index) in enumerate(zip(ASSET_TYPES, chosen), 1):
        row, col = int(rows[index]), int(cols[index])
        window = (slice(max(0, row - 1), row + 2), slice(max(0, col - 1), col + 2))
        p_values = probability[window][valid[window]]
        result.append({
            "id": f"A{number:02d}", "class": kind, "name": name, "value_rub": value, "vulnerability": vulnerability,
            "longitude": bounds[0] + (col + 0.5) * dx, "latitude": bounds[3] - (row + 0.5) * dy,
            "row": row, "col": col,
            "p_flood": round(float(probability[row, col]), 5),
            "p_window3": round(float(p_values.mean()), 5) if p_values.size else None,
            "uncertainty": round(float(spread[row, col]), 5),
            "baseline_water_at_point": bool(baseline[row, col]),
            "permanent_water_at_point": None,
            "status": "ok", "portfolio_status": "scenario_locations_and_values",
        })
    return result


def _passport(source: dict, vv: np.ndarray, vh: np.ndarray) -> list[dict]:
    """What each Sentinel-1 RTC input is, when it was observed and under which licence."""
    props = source["properties"]
    digest = hashlib.sha256(np.ascontiguousarray(np.stack([vv, vh])).tobytes()).hexdigest()
    return [{
        "name": f"Sentinel-1 RTC {band.upper()} · {source['id']}", "url": source["assets"][band]["href"],
        "stac_item_id": source["id"], "collection": "sentinel-1-rtc", "band": band.upper(),
        "observed_at": props.get("datetime"), "platform": props.get("platform"),
        "orbit_state": props.get("sat:orbit_state"), "relative_orbit": props.get("sat:relative_orbit"),
        "absolute_orbit": props.get("sat:absolute_orbit"), "datatake_id": props.get("s1:datatake_id"),
        "license": LICENSE["id"], "license_url": LICENSE["url"], "providers": LICENSE["providers"],
        "attribution": LICENSE["attribution"], "role": "model_input",
        "window_sha256": digest, "window_rule": "VV and VH warped to an approximately 10m EPSG:4326 grid, dB",
    } for band in ("vv", "vh")]


def _infer_tiled(s1: np.ndarray, progress=None) -> tuple[np.ndarray, np.ndarray]:
    """Run unchanged 512px model on a variable-size ~10m grid with a 32px halo."""
    import torch
    from floodvalue.ensemble import run_ensemble
    from floodvalue.infer import load_calibration
    height, width = s1.shape[1:]
    probability = np.zeros((height, width), dtype=np.float32)
    spread = np.zeros_like(probability)
    calibration, _ = load_calibration(CALIBRATION)
    step, halo = GRID - 64, 32
    tiles = [(y, x) for y in range(0, height, step) for x in range(0, width, step)]
    torch.set_num_threads(2)
    for index, (y, x) in enumerate(tiles, 1):
        patch = np.full((2, GRID, GRID), np.nan, dtype=np.float32)
        y0, x0 = max(0, y - halo), max(0, x - halo)
        y1, x1 = min(height, y + step + halo), min(width, x + step + halo)
        patch[:, y0 - y + halo:y1 - y + halo, x0 - x + halo:x1 - x + halo] = s1[:, y0:y1, x0:x1]
        with torch.inference_mode():
            result = run_ensemble(_ensemble(), [patch], calibration)
        h, w = min(step, height - y), min(step, width - x)
        probability[y:y+h, x:x+w] = result.prob[0, halo:halo+h, halo:halo+w]
        spread[y:y+h, x:x+w] = result.member_std[0, halo:halo+h, halo:halo+w]
        if progress:
            progress("inference", index / len(tiles), f"Модель: фрагмент {index} из {len(tiles)}")
    return probability, spread


def _cached_scene(folder: Path) -> dict | None:
    path = folder / "metadata.json"
    if not path.is_file():
        return None
    info = json.loads(path.read_text(encoding="utf-8"))
    # The reference layer is absent on live RTC scenes, but its nodata mask
    # still has to match the variable-sized radar grid for region analysis.
    expected = info["width"] * info["height"]
    water = folder / "water_mask.bin"
    if not water.is_file() or water.stat().st_size != expected:
        replacement = folder / "water_mask.bin.partial"
        replacement.write_bytes(np.full((info["height"], info["width"]), 255, dtype="uint8").tobytes())
        os.replace(replacement, water)
    return info


def prepare(bounds: list[float], item_id: str, progress=None) -> dict:
    area = validate_area(bounds)
    features = _features(bounds, item_id)
    feature = features[0]
    cache_key = b"live-v5-10m:" + ("|".join(item["id"] for item in features) + ":" +
                             ",".join(f"{n:.6f}" for n in bounds)).encode()
    cache_key += hashlib.sha256((MODEL / "model_card.json").read_bytes() + CALIBRATION.read_bytes()
                                + (HARMONIZATION.read_bytes() if HARMONIZATION.is_file() else b"")).digest()
    digest = hashlib.sha256(cache_key).hexdigest()[:16]
    scene_id = f"live_{digest}"
    folder = LIVE_DIR / scene_id
    cached = _cached_scene(folder)
    if cached is not None:
        if progress: progress("ready", 1, "Готовый расчёт взят из кэша")
        return cached
    with _lock:
        cached = _cached_scene(folder)
        if cached is not None:
            if progress: progress("ready", 1, "Готовый расчёт взят из кэша")
            return cached
        width, height = grid_size(bounds)
        token = _json(SAS)["token"]
        vv = np.full((height, width), np.nan, dtype=np.float32)
        vh = np.full((height, width), np.nan, dtype=np.float32)
        valid = np.zeros((height, width), dtype=bool)
        used_features = []
        passport = []
        for source_number, source in enumerate(features, 1):
            if progress: progress("reading", (source_number - 1) / len(features),
                                  f"Читаем VV/VH · кадр {source_number} из {len(features)}")
            source_vv = _read_band(source["assets"]["vv"]["href"], token, bounds, (height, width))
            source_vh = _read_band(source["assets"]["vh"]["href"], token, bounds, (height, width))
            fill = np.isfinite(source_vv) & np.isfinite(source_vh) & ~valid
            vv[fill], vh[fill] = source_vv[fill], source_vh[fill]
            valid |= fill
            if fill.any():
                used_features.append(source)
                passport += _passport(source, np.where(fill, source_vv, np.nan), np.where(fill, source_vh, np.nan))
            if valid.all():
                break
        vv, vh = harmonize(vv, vh)
        s1 = np.stack([vv, vh])
        if valid.mean() < 0.05:
            raise LiveError("На этом снимке радар не дал сигнала в рамке. Выберите другую дату.")
        if progress: progress("inference", 0, "Радар прочитан. Модель ищет воду")
        from floodvalue.infer import load_calibration
        _, threshold = load_calibration(CALIBRATION)
        probability, spread = _infer_tiled(s1, progress)
        model = valid & (probability >= threshold)
        baseline = valid & (vh < -23.6)
        probability[~valid] = 0
        spread[~valid] = 0
        if progress: progress("saving", 0.95, "Сохраняем слои и считаем объекты")
        folder.mkdir(parents=True, exist_ok=True)
        _overlays(folder, vh, probability, spread, model, baseline, valid)
        (folder / "model_mask.bin").write_bytes(np.where(valid, model, 255).astype("uint8").tobytes())
        (folder / "baseline_mask.bin").write_bytes(np.where(valid, baseline, 255).astype("uint8").tobytes())
        (folder / "water_mask.bin").write_bytes(np.full((height, width), 255, dtype="uint8").tobytes())
        (folder / "probability.f32").write_bytes(probability.tobytes())
        (folder / "uncertainty.f32").write_bytes(spread.tobytes())
        _raster(folder / "flood_probability.tif", np.where(valid, probability, -9999), bounds, "float32", -9999)
        _raster(folder / "flood_uncertainty.tif", np.where(valid, spread, -9999), bounds, "float32", -9999)
        _raster(folder / "flood_mask.tif", np.where(valid, model, 255), bounds, "uint8", 255)
        _raster(folder / "baseline_mask.tif", np.where(valid, baseline, 255), bounds, "uint8", 255)
        metadata_record = {
            "chip_id": scene_id, "event": "Выбранная область", "observed_at": feature["properties"]["datetime"],
            "bbox": bounds, "width": width, "height": height, "area_km2": round(area, 3),
            "valid_pixels": int(valid.sum()), "water_pixels": None, "water_area_km2": None,
            "model_water_pixels": int(model.sum()), "model_water_area_km2": round(float(model.sum() / (width*height) * area), 3),
            "baseline_water_pixels": int(baseline.sum()), "baseline_water_area_km2": round(float(baseline.sum() / (width*height) * area), 3),
            "model_threshold": threshold, "baseline_threshold_db": -23.6,
            "portfolio_seed": PORTFOLIO_SEED, "portfolio_rule": PORTFOLIO_RULE, "assets": _portfolio(bounds, probability, spread, baseline, valid),
            "source_kind": "live_sentinel1_rtc", "source_item_id": feature["id"],
            "source_item_ids": [source["id"] for source in used_features],
            "source_manifest": passport, "source_license": LICENSE,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "pixel_size_m_approx": round((area * 1_000_000 / (width * height)) ** 0.5, 1),
            "target_resolution_m": PIXEL_M, "grid_rule": "10m target, dimensions rounded up to a multiple of 32; 512px tiles with 32px halo",
            "portfolio_status": "scenario_locations_and_values",
            "reference_status": "no_manual_label",
            "radiometry": "rtc_harmonized_to_sen1floods11_grd" if harmonization() else "rtc_as_is",
            "permanent_status": "not_separated",
        }
        (folder / "metadata.json").write_text(json.dumps(metadata_record, ensure_ascii=False, indent=2), encoding="utf-8")
        if progress: progress("ready", 1, "Анализ готов")
        return metadata_record
