"""Create small, attributed demo artifacts from the official Sen1Floods11 chip.

Run with: uv run --project backend --extra demo python scripts/prepare_demo.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "source"
OUTPUT = ROOT / "data" / "demo"
CHIP = "Ghana_313799"
SEED = 2026
SOURCE_ROOT = "https://storage.googleapis.com/sen1floods11/v1.1/data/flood_events/HandLabeled"
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


def main() -> None:
    s1_path = SOURCE / f"{CHIP}_S1Hand.tif"
    label_path = SOURCE / f"{CHIP}_LabelHand.tif"
    if not s1_path.exists() or not label_path.exists():
        raise SystemExit("Download the two official GeoTIFFs first; see README.md")

    with rasterio.open(s1_path) as s1, rasterio.open(label_path) as label:
        assert s1.count == 2 and label.count == 1
        assert s1.width == label.width == 512 and s1.height == label.height == 512
        assert s1.crs == label.crs
        assert all(abs(a - b) < 1e-10 for a, b in zip(s1.transform, label.transform))
        vv, vh = s1.read()
        truth = label.read(1)
        valid = np.isfinite(vv) & np.isfinite(vh) & (truth != -1)
        mask = np.where(valid, truth, 255).astype(np.uint8)
        west, south, east, north = s1.bounds
        rows, cols = np.nonzero(np.isfinite(vv) & np.isfinite(vh))
        rng = np.random.default_rng(SEED)
        chosen = rng.choice(len(rows), size=10, replace=False)
        assets = []
        for number, ((key, name, value, vulnerability), index) in enumerate(zip(ASSET_TYPES, chosen), 1):
            row, col = int(rows[index]), int(cols[index])
            lon, lat = s1.transform * (col + 0.5, row + 0.5)
            # Interface fixture only. It is independent of both S1 and manual labels.
            scenario_p = round(float(rng.uniform(0.12, 0.88)), 3)
            assets.append({
                "id": f"A{number:02d}", "class": key, "name": name,
                "value_rub": value, "vulnerability": vulnerability,
                "longitude": lon, "latitude": lat,
                "scenario_probability": scenario_p,
            })

        to_equal_area = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True)
        x0, y0 = to_equal_area.transform(west, south)
        x1, y1 = to_equal_area.transform(east, north)
        area_km2 = abs((x1 - x0) * (y1 - y0)) / 1_000_000
        valid_pixels = int(valid.sum())
        water_pixels = int((mask == 1).sum())

        OUTPUT.mkdir(parents=True, exist_ok=True)
        (OUTPUT / "water_mask.bin").write_bytes(mask.tobytes())

        # Radar is an actual VH channel rendered with a fixed percentile stretch.
        lo, hi = np.percentile(vh[np.isfinite(vh)], [3, 97])
        gray = np.clip((vh - lo) / (hi - lo), 0, 1)
        gray = (gray * 180 + 24).astype(np.uint8)
        rgba = np.stack((gray, gray + 8, gray + 13, np.where(np.isfinite(vh), 255, 0)), axis=-1).astype(np.uint8)
        Image.fromarray(rgba, "RGBA").save(OUTPUT / "radar.png", optimize=True)
        water = np.zeros((512, 512, 4), dtype=np.uint8)
        water[mask == 1] = [38, 212, 222, 204]
        Image.fromarray(water, "RGBA").save(OUTPUT / "water.png", optimize=True)

    metadata = {
        "chip_id": CHIP,
        "event": "Ghana",
        "observed_at": "2018-09-18",
        "bbox": [west, south, east, north],
        "width": 512,
        "height": 512,
        "area_km2": round(area_km2, 3),
        "valid_pixels": valid_pixels,
        "water_pixels": water_pixels,
        "water_area_km2": round(area_km2 * water_pixels / (512 * 512), 3),
        "portfolio_seed": SEED,
        "assets": assets,
        "source_manifest": [
            {"name": s1_path.name, "url": f"{SOURCE_ROOT}/S1Hand/{s1_path.name}", "sha256": checksum(s1_path)},
            {"name": label_path.name, "url": f"{SOURCE_ROOT}/LabelHand/{label_path.name}", "sha256": checksum(label_path)},
        ],
        "probability_status": "scenario_fixture_independent_of_observation",
        "water_status": "manual_reference_label_not_model_output",
    }
    (OUTPUT / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Prepared {CHIP}: {water_pixels:,} water pixels; {area_km2:.3f} km² chip")


if __name__ == "__main__":
    main()
