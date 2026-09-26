"""Add JRC permanent water to already built scenes without re-running the model.

Usage: backend/.venv/bin/python scripts/add_permanent_water.py [data/demo]

For each scene the official JRCWaterHand layer is taken from the local
Sen1Floods11 download or fetched from the public bucket, checked against the
model grid and written next to the scene (permanent_water.tif, permanent_mask.bin,
permanent.png). The frozen model rasters and their SHA-256 do not change.
"""

from __future__ import annotations

import json
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.scene_builder import SOURCE_ROOT, add_permanent_water, load_source_meta  # noqa: E402

LOCAL = ROOT / "ml" / "data" / "sen1floods11" / "data" / "flood_events" / "HandLabeled" / "JRCWaterHand"
MANIFEST = ROOT / "ml" / "data" / "sen1floods11" / "download_manifest.json"


def jrc_file(chip_id: str, scratch: Path) -> tuple[Path, dict]:
    name = f"{chip_id}_JRCWaterHand.tif"
    if (LOCAL / name).exists():
        return LOCAL / name, load_source_meta(MANIFEST)
    target = scratch / name
    with urllib.request.urlopen(f"{SOURCE_ROOT}/JRCWaterHand/{name}", timeout=60) as response:
        target.write_bytes(response.read())
        meta = {"generation": response.headers.get("x-goog-generation"),
                "updated": response.headers.get("Last-Modified")}
    return target, {name: meta}


def main() -> None:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "demo"
    catalog_path = folder / "scenes.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    rows = []
    with tempfile.TemporaryDirectory() as scratch:
        for row in catalog["scenes"]:
            scene = folder / row["chip_id"]
            meta = json.loads((scene / "metadata.json").read_text(encoding="utf-8"))
            path, source_meta = jrc_file(row["chip_id"], Path(scratch))
            meta = add_permanent_water(scene, path, meta, source_meta)
            (scene / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            rows.append({**row, "flood_water_area_km2": meta["flood_water_area_km2"]})
            print(row["chip_id"], "permanent", meta["permanent_water_area_km2"], "km², flood",
                  meta["flood_water_area_km2"], "km²")
    catalog_path.write_text(json.dumps({**catalog, "scenes": rows}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
