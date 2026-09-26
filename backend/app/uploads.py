"""User supplied, georeferenced VV/VH GeoTIFF inference with the frozen S1 recipe.

The upload is a two-band analysis-ready raster, not an arbitrary image or a raw
Sentinel-1 SAFE package. Its provenance and radiometry stay explicit in the export.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds

from .catalog import LIVE_DIR, refresh
from .live import (_infer_tiled, _overlays, _portfolio, _raster, grid_size,
                   harmonize, validate_area, LiveError, PORTFOLIO_RULE, PORTFOLIO_SEED)
from .model_runtime import identity

MAX_BYTES = 100 * 1024 * 1024
RADIOMETRIES = {"grd_db", "rtc_linear"}
_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="uploaded-s1")


class UploadError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def incoming_path() -> Path:
    folder = LIVE_DIR / "_incoming"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / (uuid.uuid4().hex + ".tif")


def _source(path: Path, radiometry: str) -> tuple[np.ndarray, np.ndarray, list[float], np.ndarray]:
    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
            with rasterio.open(path) as source:
                if source.driver != "GTiff" or source.count != 2 or source.crs is None:
                    raise UploadError("Нужен GeoTIFF с двумя каналами VV, VH и геопривязкой CRS.")
                if source.width > 100_000 or source.height > 100_000:
                    raise UploadError("Размер растра слишком велик. Выгрузите участок до 300 км².")
                descriptions = [value.upper() if value else None for value in source.descriptions]
                if all(descriptions) and descriptions != ["VV", "VH"]:
                    raise UploadError("Порядок каналов должен быть VV, затем VH.")
                bounds = list(transform_bounds(source.crs, "EPSG:4326", *source.bounds, densify_pts=21))
                if not all(np.isfinite(bounds)) or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
                    raise UploadError("Геопривязка снимка не образует допустимую область карты.")
                if not (-180 <= bounds[0] < bounds[2] <= 180 and -85 <= bounds[1] < bounds[3] <= 85):
                    raise UploadError("Поддерживаются участки между 85° ю. ш. и 85° с. ш. без пересечения 180°.")
                try:
                    area = validate_area(bounds)
                except LiveError as error:
                    raise UploadError(str(error)) from error
                width, height = grid_size(bounds)
                from rasterio.transform import from_bounds
                with WarpedVRT(source, crs="EPSG:4326", transform=from_bounds(*bounds, width, height),
                               width=width, height=height, resampling=Resampling.bilinear) as warped:
                    values = warped.read([1, 2], masked=True).astype("float32").filled(np.nan)
    except UploadError:
        raise
    except (rasterio.errors.RasterioError, OSError, ValueError) as error:
        raise UploadError(f"Не удалось прочитать GeoTIFF: {error}") from error
    vv, vh = values[0], values[1]
    if radiometry == "rtc_linear":
        vv = np.where(vv > 0, 10 * np.log10(np.maximum(vv, 1e-20)), np.nan).astype("float32")
        vh = np.where(vh > 0, 10 * np.log10(np.maximum(vh, 1e-20)), np.nan).astype("float32")
        vv, vh = harmonize(vv, vh)
    else:
        valid_before = np.isfinite(vv) & np.isfinite(vh)
        if valid_before.any() and float(np.nanmedian(vh[valid_before])) > 5:
            raise UploadError("VH похож на линейную шкалу. Выберите режим «RTC linear» или загрузите значения dB.")
    valid = np.isfinite(vv) & np.isfinite(vh)
    if float(valid.mean()) < 0.05 or int(valid.sum()) < 10:
        raise UploadError("На снимке слишком мало пикселей с двумя действительными каналами VV/VH.")
    return vv, vh, bounds, valid


def process(path: Path, filename: str, observed_at: str, radiometry: str, progress=None) -> dict:
    progress = progress or (lambda *_: None)
    if radiometry not in RADIOMETRIES:
        raise UploadError("Шкала должна быть grd_db или rtc_linear.")
    try:
        date.fromisoformat(observed_at)
    except ValueError as error:
        raise UploadError("Укажите дату наблюдения снимка в формате ГГГГ-ММ-ДД.") from error
    if date.fromisoformat(observed_at) > datetime.now(timezone.utc).date():
        raise UploadError("Дата снимка не может быть в будущем.")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    key = hashlib.sha256(f"{sha}:{observed_at}:{radiometry}:{identity()['recipe_sha256']}".encode()).hexdigest()[:16]
    scene_id = f"live_upload_{key}"
    folder = LIVE_DIR / scene_id
    if (folder / "metadata.json").is_file():
        return json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    progress("reading", 0.1, "Проверяем геопривязку и каналы VV/VH")
    vv, vh, bounds, valid = _source(path, radiometry)
    progress("inference", 0.3, "Модель обрабатывает загруженный снимок")
    probability, spread = _infer_tiled(np.stack([vv, vh]), progress)
    threshold = identity()["threshold"]
    model = valid & (probability >= threshold)
    baseline = valid & (vh < -23.6)
    probability[~valid] = 0
    spread[~valid] = 0
    height, width = vh.shape
    area = validate_area(bounds)
    staging = LIVE_DIR / f".{scene_id}.partial"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        progress("saving", 0.95, "Сохраняем карту, объекты и паспорт источника")
        _overlays(staging, vh, probability, spread, model, baseline, valid)
        (staging / "model_mask.bin").write_bytes(np.where(valid, model, 255).astype("uint8").tobytes())
        (staging / "baseline_mask.bin").write_bytes(np.where(valid, baseline, 255).astype("uint8").tobytes())
        (staging / "water_mask.bin").write_bytes(np.full((height, width), 255, dtype="uint8").tobytes())
        (staging / "probability.f32").write_bytes(probability.astype("float32").tobytes())
        (staging / "uncertainty.f32").write_bytes(spread.astype("float32").tobytes())
        _raster(staging / "flood_probability.tif", np.where(valid, probability, -9999), bounds, "float32", -9999)
        _raster(staging / "flood_uncertainty.tif", np.where(valid, spread, -9999), bounds, "float32", -9999)
        _raster(staging / "flood_mask.tif", np.where(valid, model, 255), bounds, "uint8", 255)
        _raster(staging / "baseline_mask.tif", np.where(valid, baseline, 255), bounds, "uint8", 255)
        shutil.copyfile(path, staging / "source_s1.tif")
        info = {
            "chip_id": scene_id, "event": "Загруженный снимок", "observed_at": observed_at,
            "bbox": bounds, "width": width, "height": height, "area_km2": round(area, 3),
            "valid_pixels": int(valid.sum()), "water_pixels": None, "water_area_km2": None,
            "model_water_pixels": int(model.sum()),
            "model_water_area_km2": round(float(model.sum() / (width * height) * area), 3),
            "baseline_water_pixels": int(baseline.sum()),
            "baseline_water_area_km2": round(float(baseline.sum() / (width * height) * area), 3),
            "model_threshold": threshold, "baseline_threshold_db": -23.6,
            "model": {**identity(), "mode": "S1 fallback", "optical_used_pixels": 0},
            "portfolio_seed": PORTFOLIO_SEED, "portfolio_rule": PORTFOLIO_RULE,
            "assets": _portfolio(bounds, probability, spread, baseline, valid),
            "source_kind": "uploaded_sentinel1_geotiff", "origin": "upload",
            "source_filename": Path(filename).name, "source_sha256": sha,
            "source_radiometry": radiometry, "processed_at": _now(),
            "pixel_size_m_approx": round((area * 1_000_000 / (width * height)) ** 0.5, 1),
            "target_resolution_m": 10, "grid_rule": "~10m EPSG:4326, dimensions divisible by 32; 512px model tiles with halo",
            "portfolio_status": "scenario_locations_and_values", "reference_status": "no_manual_label",
            "split_part": "user_upload_unlabelled", "metrics_independent": False,
            "source_manifest": [{"name": Path(filename).name, "url": None, "sha256": sha,
                                 "file_in_package": "source_s1.tif", "layer": "uploaded_S1",
                                 "observed_at": observed_at, "uploaded_at": _now(),
                                 "radiometry": radiometry, "role": "model_input",
                                 "license": "user_supplied_unverified"}],
            "radiometry": "rtc_harmonized_to_sen1floods11_grd" if radiometry == "rtc_linear" else "grd_db_as_supplied",
            "permanent_status": "not_separated",
        }
        (staging / "metadata.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(staging, folder)
        refresh()
        progress("ready", 1, "Анализ загруженного снимка готов")
        return info
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _work(job_id: str, path: Path, filename: str, observed_at: str, radiometry: str) -> None:
    def progress(stage: str, fraction: float, message: str) -> None:
        with _lock:
            _jobs[job_id].update(stage=stage, progress=fraction, message=message)
    try:
        info = process(path, filename, observed_at, radiometry, progress)
        with _lock:
            _jobs[job_id].update(stage="done", progress=1, scene_id=info["chip_id"],
                                 bbox=info["bbox"], message="Снимок обработан", finished_at=_now())
    except Exception as error:
        with _lock:
            _jobs[job_id].update(stage="error", error=str(error), message=str(error), finished_at=_now())
    finally:
        path.unlink(missing_ok=True)


def start(path: Path, filename: str, observed_at: str, radiometry: str) -> dict:
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "stage": "queued", "progress": 0, "message": "Снимок поставлен в очередь",
           "scene_id": None, "error": None, "created_at": _now(), "finished_at": None}
    with _lock:
        _jobs[job_id] = job
    _executor.submit(_work, job_id, path, filename, observed_at, radiometry)
    return dict(job)


def get(job_id: str) -> dict | None:
    with _lock:
        return dict(_jobs[job_id]) if job_id in _jobs else None
