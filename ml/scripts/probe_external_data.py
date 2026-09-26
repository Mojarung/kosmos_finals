"""Download <=8 MiB of official Kuro Siwo train data and WorldFloods metadata; no training."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import requests
from rasterio.warp import transform_geom

from floodvalue import paths
from floodvalue.chips import Chip, layer_path
from floodvalue.external_data import first_kuro_sample, geographic_overlaps

OUT = paths.DATA_DIR / "external_catalogs"
REPORT = paths.PIPELINE_ROOT / "research" / "EXTERNAL_DATA_PROBE.json"
KURO = "orion-ai-lab/Kuro-Siwo-Webdataset"
WORLD = "isp-uv-es/WorldFloodsv2"


def fetch(url, destination, maximum=8*1024*1024, prefix=False):
    if destination.exists():
        return
    headers = {"Range": f"bytes=0-{maximum-1}"} if prefix else {}
    with requests.get(url, headers=headers, stream=True, timeout=45) as response:
        response.raise_for_status()
        data = bytearray()
        for block in response.iter_content(65536):
            data.extend(block)
            if len(data) >= maximum:
                if not prefix:
                    raise ValueError("metadata unexpectedly exceeds limit")
                data = data[:maximum]
                break
    destination.write_bytes(data)


def main():
    import rasterio
    from shapely import wkt
    from shapely.geometry import mapping

    OUT.mkdir(parents=True, exist_ok=True)
    for short, repo in (("kuro", KURO), ("worldfloods", WORLD)):
        fetch(f"https://huggingface.co/api/datasets/{repo}", OUT/f"{short}_card.json")
    kuro_rev = json.loads((OUT/"kuro_card.json").read_text())["sha"]
    world_rev = json.loads((OUT/"worldfloods_card.json").read_text())["sha"]
    sample_url = f"https://huggingface.co/datasets/{KURO}/resolve/{kuro_rev}/train_GRD/shard-00000.tar"
    fetch(sample_url, OUT/"kuro_train_head.tar", prefix=True)
    fetch(f"https://huggingface.co/api/datasets/{WORLD}/tree/{world_rev}/train/meta?limit=1000",
          OUT/"worldfloods_train_meta.json")
    entries = json.loads((OUT/"worldfloods_train_meta.json").read_text())
    meta_path = sorted(e["path"] for e in entries if e["type"] == "file")[0]
    meta_url = f"https://huggingface.co/datasets/{WORLD}/resolve/{world_rev}/{meta_path}"
    fetch(meta_url, OUT/"worldfloods_sample_meta.json")
    sample = first_kuro_sample(OUT/"kuro_train_head.tar")
    # Kuro catalogue/catalogue.yaml declares EPSG:3857 for info.json's WKT geometry.
    geometry = transform_geom("EPSG:3857", "EPSG:4326", mapping(wkt.loads(sample["info"]["geom"])))
    world_meta = json.loads((OUT/"worldfloods_sample_meta.json").read_text())
    split = json.loads((paths.ARTIFACTS_DIR/"split.json").read_text())["parts"]
    protected = {}
    for name in sorted({n for values in split.values() for n in values}):
        with rasterio.open(layer_path(paths.SEN1FLOODS11_DIR, Chip.from_name(name), "S1Hand")) as src:
            from rasterio.warp import transform_bounds
            protected[name] = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
    overlap = geographic_overlaps(geometry, protected)
    world_overlap = geographic_overlaps(world_meta["area_of_interest_polygon"], protected)
    report = {
        "training_admission": False,
        "scope": "One deterministic train sample per source; catalogue/probe only; event-level deduplication still required",
        "protected_chips": len(protected),
        "kuro": {"repo": KURO, "revision": kuro_rev, "url": sample_url, "prefix_bytes": (OUT/"kuro_train_head.tar").stat().st_size,
                 "license": "CC-BY-4.0", "key": sample["key"], "event_id": sample["info"]["actid"],
                 "sources": sample["info"]["sources"], "geometry_4326": geometry,
                 "shape": list(sample["target"].shape), "water_pixels": int((sample["target"] & sample["valid"]).sum()),
                 "valid_pixels": int(sample["valid"].sum()), "s1_db_percentiles": np.nanpercentile(sample["s1"], [1, 50, 99]).tolist(),
                 "protected_geographic_overlap": overlap, "label_mapping": "0 dry; 1 permanent water and 2 flood -> water; 3 nodata",
                 "power_conversion_source": "https://github.com/Orion-AI-Lab/KuroSiwo/blob/main/configs/grd_preprocessing.xml"},
        "worldfloods": {"repo": WORLD, "revision": world_rev, "metadata_url": meta_url,
                        "license": "CC-BY-NC-4.0", "train_metadata_entries": len(entries),
                        "event_id": world_meta["event id"], "s2_date": world_meta["s2_date"],
                        "shape": world_meta["shape"], "encoding": world_meta["encoding_values"],
                        "protected_geographic_overlap": world_overlap,
                        "image_downloaded": False, "note": "two GT channels: clear/cloud and land/water; HF auto imagefolder viewer is not the segmentation task"},
        "files_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(OUT.iterdir()) if p.is_file()},
    }
    REPORT.write_text(json.dumps(report, indent=2, allow_nan=False))
    print(json.dumps({"report": str(REPORT), "kuro_overlap": overlap, "worldfloods_overlap": world_overlap,
                      "kuro_water_pixels": report["kuro"]["water_pixels"]}))


if __name__ == "__main__":
    main()
