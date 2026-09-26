"""Bounded multi-shard Kuro Siwo sample, screened before any transfer-learning pilot.

Equally spaced offsets per train shard, 8 MiB each. This is a feasibility sample,
not a representative external validation set. No Sen1Floods11 labels are read.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import tarfile
import time

import numpy as np
import rasterio
from rasterio.warp import transform_bounds, transform_geom
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from shapely import wkt
from shapely.geometry import mapping

from floodvalue import paths
from floodvalue.chips import Chip, layer_path
from floodvalue.external_data import geographic_overlaps, kuro_water_target, linear_power_db

ROOT = paths.DATA_DIR/"external_catalogs"
DEST = paths.DATA_DIR/"kuro_pilot"
LENGTH = 8*1024*1024


def read_samples(data):
    """Scan aligned tar headers; never extract archive paths or deserialize pickle."""
    current, fields = None, {}
    position = 0
    required = {"flood_vv", "flood_vh", "sec1_vv", "sec1_vh", "sec2_vv", "sec2_vh", "dem", "mask", "valid_mask", "info"}
    while position+512 <= len(data):
        try:
            entry = tarfile.TarInfo.frombuf(data[position:position+512], "utf-8", "replace")
        except (tarfile.HeaderError, ValueError):
            position += 512
            continue
        if not entry.isfile() or "." not in entry.name or not 0 < entry.size < 4_000_000:
            position += 512
            continue
        end = position+512+entry.size
        if end > len(data):
            break
        key, suffix = entry.name.split(".", 1)
        if key != current:
            current, fields = key, {}
        payload = data[position+512:end]
        if suffix.endswith(".npy"):
            fields[suffix[:-4]] = np.load(io.BytesIO(payload), allow_pickle=False)
        elif suffix == "info.json":
            fields["info"] = json.loads(payload)
        position += 512+((entry.size+511)//512)*512
        if required <= fields.keys():
            yield key, fields
            fields = {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ranges-per-shard", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.ranges_per_shard <= 30:
        parser.error("ranges-per-shard must be between 1 and 30 (at most 1.2 GiB)")
    DEST.mkdir(parents=True, exist_ok=True)
    raw = DEST/"ranges"
    raw.mkdir(exist_ok=True)
    revision = json.loads((ROOT/"kuro_card.json").read_text())["sha"]
    entries = json.loads((ROOT/"kuro_web.json").read_text())
    shards = [r for r in entries if r["path"].startswith("train_GRD/") and r["path"].endswith(".tar")]
    fractions = [i / args.ranges_per_shard for i in range(args.ranges_per_shard)]
    tasks = [(r, (int((r["size"]-LENGTH)*fraction)//512)*512) for r in shards for fraction in fractions]

    def fetch(task):
        entry, start = task
        path = raw/f"{Path(entry['path']).stem}_{start}.bin"
        url = f"https://huggingface.co/datasets/orion-ai-lab/Kuro-Siwo-Webdataset/resolve/{revision}/{entry['path']}"
        if not path.exists():
            session = requests.Session()
            session.mount("https://", HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1,
                                                                   status_forcelist=[429, 500, 502, 503, 504])))
            with session:
                for attempt in range(4):
                    try:
                        with session.get(url, headers={"Range": f"bytes={start}-{start+LENGTH-1}"}, stream=True, timeout=60) as response:
                            response.raise_for_status()
                            expected_range = f"bytes {start}-{start+LENGTH-1}/"
                            if response.status_code != 206 or not response.headers.get("Content-Range", "").startswith(expected_range):
                                raise ValueError("server ignored requested range")
                            data = bytearray()
                            for block in response.iter_content(65536):
                                data.extend(block)
                                if len(data) > LENGTH:
                                    raise ValueError("range exceeded byte limit")
                            if len(data) != LENGTH:
                                raise requests.exceptions.ChunkedEncodingError("incomplete range")
                        break
                    except requests.RequestException:
                        if attempt == 3:
                            raise
                        time.sleep(attempt + 1)
            path.with_suffix(".tmp").write_bytes(data)
            path.with_suffix(".tmp").replace(path)
        if path.stat().st_size != LENGTH:
            raise ValueError(f"cached range has wrong length: {path}")
        print("range", path.name, flush=True)
        return entry["path"], start, path

    with ThreadPoolExecutor(max_workers=3) as pool:
        fetched = list(pool.map(fetch, tasks))
    protected = {}
    split = json.loads((paths.ARTIFACTS_DIR/"split.json").read_text())["parts"]
    for name in sorted({n for ns in split.values() for n in ns}):
        with rasterio.open(layer_path(paths.SEN1FLOODS11_DIR, Chip.from_name(name), "S1Hand")) as src:
            protected[name] = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
    rows, seen = [], set()
    samples = DEST/"samples"
    samples.mkdir(exist_ok=True)
    for shard, start, path in fetched:
        for key, fields in read_samples(path.read_bytes()):
            info = fields["info"]
            sample_id = info["grid_id"]
            if sample_id in seen:
                continue
            seen.add(sample_id)
            geometry = transform_geom("EPSG:3857", "EPSG:4326", mapping(wkt.loads(info["geom"])))
            overlaps = geographic_overlaps(geometry, protected)
            after_2019 = date.fromisoformat(info["flood_date"][:10]) >= date(2020, 1, 1)
            # Conservative temporal screen prevents reuse of Sen1Floods11's 2016-2019 events.
            admitted = not overlaps and after_2019
            target, valid = kuro_water_target(fields["mask"][0], fields["valid_mask"][0])
            s1 = linear_power_db(np.concatenate([fields["flood_vv"], fields["flood_vh"]]))
            pre1 = linear_power_db(np.concatenate([fields["sec1_vv"], fields["sec1_vh"]]))
            pre2 = linear_power_db(np.concatenate([fields["sec2_vv"], fields["sec2_vh"]]))
            valid &= np.isfinite(s1).all(axis=0)
            if admitted:
                np.savez_compressed(samples/(sample_id+".npz"), s1=s1, pre1=pre1, pre2=pre2,
                                    dem=fields["dem"][0], target=target, valid=valid)
            rows.append({"id": sample_id, "event": info["actid"], "flood_date": info["flood_date"],
                         "shard": shard, "range_start": start, "sample_key": key, "geometry_4326": geometry,
                         "sources": info["sources"], "overlaps": overlaps, "after_2019": after_2019,
                         "admitted_to_training_pool": admitted, "valid_pixels": int(valid.sum()),
                         "water_pixels": int((target & valid).sum())})
    from collections import Counter
    report = {"revision": revision, "license": "CC-BY-4.0", "selection": f"fixed equally spaced i/{args.ranges_per_shard} offsets, 8 MiB each, all five train GRD shards",
              "admission_rule": "flood date >=2020-01-01 and no overlap with any Sen1Floods11 chip buffered by 0.02 degrees",
              "interpretation": "retrospective transfer training pool, not historically available training data for the earlier target events",
              "trained": False, "sampled": len(rows), "admitted": sum(r["admitted_to_training_pool"] for r in rows),
              "admitted_events": dict(Counter(str(r["event"]) for r in rows if r["admitted_to_training_pool"])),
              "download_bytes": sum(p.stat().st_size for _, _, p in fetched),
              "range_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for _, _, p in fetched}, "rows": rows}
    filename = "KURO_PILOT_MANIFEST.json" if args.ranges_per_shard == 3 else "KURO_POOL_MANIFEST.json"
    out = paths.PIPELINE_ROOT/"research"/filename
    out.write_text(json.dumps(report, indent=2, allow_nan=False))
    print(json.dumps({k: report[k] for k in ("sampled", "admitted", "admitted_events", "download_bytes")}), flush=True)


if __name__ == "__main__":
    main()
