"""Permanent water for a region drawn on the map, where Sen1Floods11 has no JRC layer.

The service uses JRC Global Surface Water v1.4 transitions 1984-2021 with the same
rule as Sen1Floods11 JRCWaterHand: water both in the first and in the last year.
Observations end in 2021, before any Sentinel-1 RTC event the map offers (2024 on).

history_permanent() is the experts' strict variant: Sentinel-1 scenes of the year
before the event, water on every season. Checked against JRC on Nigeria_812045 it
finds almost nothing (IoU 0.002): in the dry season the braided Kaduna channel is
sand bars on radar. It stays for scripts/check_s1_permanent.py and is not used by
the service.
"""

from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from math import ceil, floor

import numpy as np
from PIL import Image
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT

from . import catalog
from .catalog import LIVE_DIR, metadata, scene_dir
from .live import CALIBRATION, GRID, SAS, LiveError, _ensemble, _groups, _json, _read_band, harmonize

SCENES = 4
MIN_OBSERVED = 3
MIN_GAP_DAYS = 21
YEAR_DAYS = 365
RULE = (f"Sentinel-1 RTC before the event: one pass of one relative orbit in each of {SCENES} equal windows "
        f"of the {YEAR_DAYS} days ending {MIN_GAP_DAYS} days before it, RTC mapped to GRD dB, the same frozen "
        f"ensemble on each; permanent = water on every scene where observed, observed on at least {MIN_OBSERVED}")


def _parse(value: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def pick_passes(bounds: list[float], event_at: str, relative_orbit: int | None = None) -> list[dict]:
    """One full-coverage pass of one orbit per season of the year before the event.

    The catalog is asked window by window: one query for the whole year returns
    only the latest months, and four summer scenes would call a seasonal river permanent.
    """
    event = _parse(event_at)
    start = event - timedelta(days=YEAR_DAYS)
    stop = event - timedelta(days=MIN_GAP_DAYS)
    step = (stop - start) / SCENES
    windows = []
    for number in range(SCENES):
        low, high = start + step * number, start + step * (number + 1)
        groups = [group for group in _groups(bounds, f"{low.isoformat()}/{high.isoformat()}", limit=100) if group["full_bbox"]]
        windows.append((low + step / 2, groups))
    orbits = Counter(group["features"][0]["properties"].get("sat:relative_orbit") for _, groups in windows for group in groups)
    if not orbits:
        return []
    orbit = relative_orbit if relative_orbit in orbits else orbits.most_common(1)[0][0]
    chosen = []
    for middle, groups in windows:
        same = [group for group in groups if group["features"][0]["properties"].get("sat:relative_orbit") == orbit]
        if same:
            chosen.append(min(same, key=lambda group: abs(_parse(group["features"][0]["properties"]["datetime"]) - middle)))
    return chosen


def _read_pass(group: dict, token: str, bounds: list[float]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    vv = np.full((GRID, GRID), np.nan, dtype=np.float32)
    vh = np.full((GRID, GRID), np.nan, dtype=np.float32)
    valid = np.zeros((GRID, GRID), dtype=bool)
    for source in group["features"]:
        source_vv = _read_band(source["assets"]["vv"]["href"], token, bounds)
        source_vh = _read_band(source["assets"]["vh"]["href"], token, bounds)
        fill = np.isfinite(source_vv) & np.isfinite(source_vh) & ~valid
        vv[fill], vh[fill] = source_vv[fill], source_vh[fill]
        valid |= fill
        if valid.all():
            break
    return vv, vh, valid


def history_permanent(bounds: list[float], event_at: str, relative_orbit: int | None = None) -> dict:
    """Permanent water mask (1 permanent, 0 not, 255 not observed enough) on the GRID over bounds."""
    passes = pick_passes(bounds, event_at, relative_orbit)
    if len(passes) < MIN_OBSERVED:
        raise LiveError("До даты паводка нашлось меньше трёх снимков той же орбиты: постоянную воду не отделить.")
    token = _json(SAS)["token"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        reads = list(pool.map(lambda group: _read_pass(group, token, bounds), passes))
    import torch
    from floodvalue.ensemble import run_ensemble
    from floodvalue.infer import load_calibration
    calibration, threshold = load_calibration(CALIBRATION)
    observed = np.zeros((GRID, GRID), dtype=np.int16)
    water = np.zeros((GRID, GRID), dtype=np.int16)
    for vv, vh, valid in reads:
        vv, vh = harmonize(vv, vh)
        with torch.inference_mode():
            result = run_ensemble(_ensemble(), [np.stack([vv, vh])], calibration)
        wet = valid & (np.asarray(result.prob[0]) >= threshold)
        observed += valid
        water += wet
    enough = observed >= MIN_OBSERVED
    permanent = enough & (water == observed)
    return {
        "mask": np.where(enough, permanent, 255).astype(np.uint8),
        "items": [{"stac_item_ids": [f["id"] for f in group["features"]],
                   "observed_at": group["features"][0]["properties"]["datetime"],
                   "relative_orbit": group["features"][0]["properties"].get("sat:relative_orbit"),
                   "platform": group["features"][0]["properties"].get("platform")} for group in passes],
        "rule": RULE, "threshold": threshold,
    }


GSW_URL = ("https://storage.googleapis.com/global-surface-water/downloads2021/transitions/"
           "transitions_{lon}_{lat}v1_4_2021.tif")
# Water in both 1984 and 2021: permanent, seasonal, seasonal to permanent, permanent to seasonal.
GSW_CLASSES = (1, 4, 7, 8)
GSW_RULE = ("JRC Global Surface Water v1.4 transitions 1984-2021 (Pekel et al., Nature 2016): water in both "
            "1984 and 2021, classes 1, 4, 7, 8; the Sen1Floods11 JRCWaterHand rule; observations end 2021, "
            "before the event")


def _label(value: int, positive: str, negative: str) -> str:
    return f"{abs(value)}{positive if value >= 0 else negative}"


def gsw_permanent(bounds: list[float], width: int = GRID, height: int = GRID) -> tuple[np.ndarray, list[str]]:
    """JRC GSW permanent water on the scene grid over bounds."""
    transform = from_bounds(*bounds, width, height)
    result = np.zeros((height, width), dtype=bool)
    urls = []
    for west in range(floor(bounds[0] / 10) * 10, ceil(bounds[2] / 10) * 10, 10):
        for north in range(ceil(bounds[3] / 10) * 10, floor(bounds[1] / 10) * 10, -10):
            url = GSW_URL.format(lon=_label(west, "E", "W"), lat=_label(north, "N", "S"))
            try:
                with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="2"):
                    with rasterio.open("/vsicurl/" + url) as source:
                        with WarpedVRT(source, crs="EPSG:4326", transform=transform, width=width, height=height,
                                       resampling=Resampling.nearest) as warped:
                            result |= np.isin(warped.read(1), GSW_CLASSES)
            except Exception as error:
                raise LiveError(f"Архив постоянной воды JRC сейчас недоступен: {error}") from error
            urls.append(url)
    return result, urls


def _write(folder, layer: np.ndarray, bounds: list[float]) -> None:
    height, width = layer.shape
    (folder / "permanent_mask.bin").write_bytes(np.ascontiguousarray(layer).tobytes())
    with rasterio.open(folder / "permanent_water.tif", "w", driver="GTiff", height=height, width=width, count=1,
                       dtype="uint8", crs="EPSG:4326", transform=from_bounds(*bounds, width, height),
                       nodata=255, compress="deflate") as target:
        target.write(layer, 1)
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    rgba[layer == 1] = (18, 58, 118, 220)
    Image.fromarray(rgba, "RGBA").save(folder / "permanent.png", optimize=True)


def separate_live(scene_id: str) -> dict:
    """Subtract JRC GSW permanent water from a live RTC scene and store it next to the scene."""
    if not scene_id.startswith("live_"):
        raise LiveError("Постоянную воду из архива JRC добавляем только для выбранной на карте области.")
    info = metadata(scene_id)
    if info.get("permanent_status") == "jrc_gsw":
        return {"scene_id": scene_id, "status": "jrc_gsw", "sources": info.get("permanent_sources", [])}
    folder = scene_dir(scene_id)
    if folder.parent != LIVE_DIR:
        raise LiveError("Сцена не найдена среди выбранных областей.")
    permanent, urls = gsw_permanent(info["bbox"], info["width"], info["height"])
    model_mask = np.frombuffer((folder / "model_mask.bin").read_bytes(), dtype=np.uint8).reshape(info["height"], info["width"])
    valid = model_mask != 255
    permanent &= valid
    _write(folder, np.where(valid, permanent, 255).astype(np.uint8), info["bbox"])
    pixel_area = info["area_km2"] / (info["width"] * info["height"])
    assets = [{**asset, "permanent_water_at_point": bool(permanent[asset["row"], asset["col"]])} for asset in info["assets"]]
    water = model_mask == 1
    # Terms as stated on the JRC GSW download page; the exporter adds the quote and the check date.
    references = [{"name": url.rsplit("/", 1)[1], "url": url, "role": "permanent_water_reference",
                   "version": "JRC GSW v1.4 (1984-2021)",
                   "license": "Copernicus Programme data, free of charge, without restriction of use",
                   "license_url": "https://global-surface-water.appspot.com/download", "observed_at": "1984/2021"}
                  for url in urls]
    updated = {**info, "assets": assets, "permanent_status": "jrc_gsw", "permanent_rule": GSW_RULE,
               "permanent_sources": urls,
               "source_manifest": [item for item in info["source_manifest"]
                                   if item.get("role") != "permanent_water_reference"] + references,
               "permanent_water_pixels": int(permanent.sum()),
               "permanent_water_area_km2": round(int(permanent.sum()) * pixel_area, 3),
               "flood_water_area_km2": round(int((water & ~permanent).sum()) * pixel_area, 3)}
    (folder / "metadata.json").write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    catalog.refresh()
    return {"scene_id": scene_id, "status": "jrc_gsw", "sources": urls,
            "permanent_water_area_km2": updated["permanent_water_area_km2"]}
