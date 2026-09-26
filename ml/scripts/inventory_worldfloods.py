"""Audit WorldFloods train metadata against every Sen1Floods11 chip before admission."""
import json
from concurrent.futures import ThreadPoolExecutor

import requests
import rasterio
from rasterio.warp import transform_bounds

from floodvalue import paths
from floodvalue.chips import Chip, layer_path
from floodvalue.external_data import geographic_overlaps


def main():
    root = paths.DATA_DIR/"external_catalogs"
    destination = root/"worldfloods_train_metadata"
    destination.mkdir(parents=True, exist_ok=True)
    revision = json.loads((root/"worldfloods_card.json").read_text())["sha"]
    entries = json.loads((root/"worldfloods_train_meta.json").read_text())
    split = json.loads((paths.ARTIFACTS_DIR/"split.json").read_text())["parts"]
    protected = {}
    for name in sorted({n for names in split.values() for n in names}):
        with rasterio.open(layer_path(paths.SEN1FLOODS11_DIR, Chip.from_name(name), "S1Hand")) as src:
            protected[name] = transform_bounds(src.crs, "EPSG:4326", *src.bounds)

    def one(entry):
        name = entry["path"].rsplit("/", 1)[-1]
        path = destination/name
        url = f"https://huggingface.co/datasets/isp-uv-es/WorldFloodsv2/resolve/{revision}/{entry['path']}"
        try:
            if not path.exists():
                response = requests.get(url, timeout=30)
                response.raise_for_status()
                if len(response.content) > 1_000_000:
                    raise ValueError("unexpected metadata size")
                obj = response.json()
                path.write_bytes(response.content)
            else:
                obj = json.loads(path.read_text())
            geometry = obj.get("area_of_interest_polygon")
            if geometry is None:
                raise ValueError("missing geographic footprint")
            overlap = geographic_overlaps(geometry, protected)
            return {"file": entry["path"], "status": "overlap_exclude" if overlap else "geography_screen_pass",
                    "event_id": obj.get("event id"), "ems_code": obj.get("ems_code"),
                    "s2_date": obj.get("s2_date"), "shape": obj.get("shape"),
                    "geographic_overlap": overlap, "source": obj.get("source")}
        except Exception as exc:
            return {"file": entry["path"], "status": "unresolved", "error": type(exc).__name__}

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(one, [e for e in entries if e["type"] == "file"]))
    from collections import Counter
    report = {"dataset": "isp-uv-es/WorldFloodsv2", "revision": revision, "license": "CC-BY-NC-4.0",
              "training_admission": False, "protected_chips": len(protected),
              "rule": "Any footprint intersecting any Sen1Floods11 chip with 0.02 degree buffer is excluded regardless of date. "
                      "Screen-pass still needs event deduplication, image/label date checks and compatible task encoding.",
              "counts": dict(Counter(r["status"] for r in rows)), "rows": rows}
    out = paths.PIPELINE_ROOT/"research/WORLDFLOODS_INVENTORY.json"
    out.write_text(json.dumps(report, indent=2, allow_nan=False))
    print(json.dumps({"report": str(out), **report["counts"]}), flush=True)


if __name__ == "__main__":
    main()
