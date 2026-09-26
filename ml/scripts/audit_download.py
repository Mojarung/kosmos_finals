"""Fetch exactly the frozen evaluation chips, with official object MD5 checks."""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone

from floodvalue import paths
from floodvalue.download import download_object, list_objects
from floodvalue.split import load_split


def main():
    split = load_split(paths.ARTIFACTS_DIR / "split.json")
    names = {name for part in ("test_core", "test_adjacent", "bolivia") for name in split[part]}
    prefixes = ["v1.1/Sen1Floods11_Metadata.geojson"] + [
        f"v1.1/data/flood_events/HandLabeled/{layer}/"
        for layer in ("S1Hand", "S2Hand", "LabelHand")]
    objects = []
    for prefix in prefixes:
        for obj in list_objects(prefix):
            if obj.name.endswith("Metadata.geojson") or obj.name.rsplit("/", 1)[-1].rsplit("_", 1)[0] in names:
                objects.append(obj)
    if len(objects) != 1 + 3 * len(names):
        raise ValueError(f"Incomplete official input list: {len(objects)}")
    print(json.dumps({"chips": len(names), "files": len(objects), "bytes": sum(o.size for o in objects)}), flush=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [pool.submit(download_object, obj, paths.SEN1FLOODS11_DIR) for obj in objects]
        for count, task in enumerate(as_completed(jobs), 1):
            task.result()
            if count % 20 == 0:
                print(f"download {count}/{len(objects)}", flush=True)
    manifest = {"source": "gs://sen1floods11/v1.1", "downloaded_at": datetime.now(timezone.utc).isoformat(),
                "names": sorted(names), "objects": [asdict(o) for o in objects]}
    (paths.SEN1FLOODS11_DIR / "audit_download_manifest.json").write_text(json.dumps(manifest, indent=2))
    print("All inputs downloaded and MD5 verified", flush=True)


if __name__ == "__main__":
    main()
