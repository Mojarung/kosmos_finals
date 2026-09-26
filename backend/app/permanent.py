"""Permanent water from Sentinel-1 before the event, for regions without the JRC layer.

The experts' rule: take radar scenes from before the flood, find water that is
always there and subtract it. Here: up to SCENES passes of one relative orbit
spread over the year before the event (at least MIN_GAP_DAYS before it), the same
frozen ensemble on each, and a pixel is permanent water when it is water on every
scene where it was observed and it was observed at least MIN_OBSERVED times.
"All scenes" mirrors the JRC rule (water both in 1984 and in 2018); four scenes
give one per season, so seasonal floodplains do not count as permanent.
"""

from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np
from PIL import Image
import rasterio
from rasterio.transform import from_bounds

from . import catalog
from .catalog import LIVE_DIR, metadata, scene_dir
from .live import CALIBRATION, GRID, SAS, LiveError, _ensemble, _groups, _json, _read_band

SCENES = 4
MIN_OBSERVED = 3
MIN_GAP_DAYS = 21
YEAR_DAYS = 365
RULE = (f"Sentinel-1 RTC before the event: up to {SCENES} passes of one relative orbit spread over "
        f"{YEAR_DAYS} days ending {MIN_GAP_DAYS} days before it; the same frozen ensemble on each; "
        f"permanent = water on every scene where observed, observed on at least {MIN_OBSERVED}")


def _parse(value: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def pick_passes(bounds: list[float], event_at: str, relative_orbit: int | None = None) -> list[dict]:
    """Evenly spaced full-coverage passes of one orbit from the year before the event."""
    event = _parse(event_at)
    period = f"{(event - timedelta(days=YEAR_DAYS)).isoformat()}/{(event - timedelta(days=MIN_GAP_DAYS)).isoformat()}"
    groups = [group for group in _groups(bounds, period, limit=250) if group["full_bbox"]]
    if not groups:
        return []
    orbits = Counter(group["features"][0]["properties"].get("sat:relative_orbit") for group in groups)
    orbit = relative_orbit if relative_orbit in orbits else orbits.most_common(1)[0][0]
    same = sorted((group for group in groups if group["features"][0]["properties"].get("sat:relative_orbit") == orbit),
                  key=lambda group: group["features"][0]["properties"]["datetime"])
    if len(same) <= SCENES:
        return same
    positions = np.unique(np.linspace(0, len(same) - 1, SCENES).round().astype(int))
    return [same[index] for index in positions]


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


def _write(folder, layer: np.ndarray, bounds: list[float]) -> None:
    (folder / "permanent_mask.bin").write_bytes(np.ascontiguousarray(layer).tobytes())
    with rasterio.open(folder / "permanent_water.tif", "w", driver="GTiff", height=GRID, width=GRID, count=1,
                       dtype="uint8", crs="EPSG:4326", transform=from_bounds(*bounds, GRID, GRID),
                       nodata=255, compress="deflate") as target:
        target.write(layer, 1)
    rgba = np.zeros((GRID, GRID, 4), dtype=np.uint8)
    rgba[layer == 1] = (18, 58, 118, 220)
    Image.fromarray(rgba, "RGBA").save(folder / "permanent.png", optimize=True)


def separate_live(scene_id: str) -> dict:
    """Run the history rule for a live RTC scene and store it next to the scene."""
    if not scene_id.startswith("live_"):
        raise LiveError("Постоянную воду по снимкам до события считаем только для выбранной на карте области.")
    info = metadata(scene_id)
    if info.get("permanent_status") == "s1_history":
        return {"scene_id": scene_id, "status": "s1_history", "items": info.get("permanent_items", [])}
    orbit = next((item.get("relative_orbit") for item in info.get("source_manifest", []) if item.get("relative_orbit")), None)
    result = history_permanent(info["bbox"], info["observed_at"], orbit)
    folder = scene_dir(scene_id)
    if folder.parent != LIVE_DIR:
        raise LiveError("Сцена не найдена среди выбранных областей.")
    _write(folder, result["mask"], info["bbox"])
    permanent = result["mask"] == 1
    model = np.frombuffer((folder / "model_mask.bin").read_bytes(), dtype=np.uint8).reshape(GRID, GRID) == 1
    pixel_area = info["area_km2"] / (GRID * GRID)
    assets = [{**asset, "permanent_water_at_point": bool(permanent[asset["row"], asset["col"]])} for asset in info["assets"]]
    updated = {**info, "assets": assets, "permanent_status": "s1_history", "permanent_rule": RULE,
               "permanent_items": result["items"],
               "permanent_water_pixels": int(permanent.sum()),
               "permanent_water_area_km2": round(int(permanent.sum()) * pixel_area, 3),
               "flood_water_area_km2": round(int((model & ~permanent).sum()) * pixel_area, 3)}
    (folder / "metadata.json").write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    catalog.refresh()
    return {"scene_id": scene_id, "status": "s1_history", "items": result["items"],
            "permanent_water_area_km2": updated["permanent_water_area_km2"]}
