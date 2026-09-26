"""Fetch official optical inputs separately, without replacing the S1 manifest."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
import json

from floodvalue import paths
from floodvalue.chips import Chip
from floodvalue.download import download_object, list_objects
from floodvalue.split import load_split


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parts", nargs="+", default=["train", "valid"])
    args = parser.parse_args()
    split = load_split(paths.ARTIFACTS_DIR/"split.json")
    names = {n for part in args.parts for n in split[part]}
    objects = [o for o in list_objects("v1.1/data/flood_events/HandLabeled/S2Hand/")
               if Chip.from_filename(o.name).name in names]
    if {Chip.from_filename(o.name).name for o in objects} != names:
        raise ValueError("incomplete official optical inventory")
    print(json.dumps({"objects": len(objects), "bytes": sum(o.size for o in objects)}), flush=True)
    with ThreadPoolExecutor(max_workers=12) as pool:
        for i, _ in enumerate(pool.map(lambda o: download_object(o, paths.SEN1FLOODS11_DIR), objects), 1):
            if i % 40 == 0:
                print("downloaded", i, flush=True)
    result = {"source": "https://github.com/cloudtostreet/Sen1Floods11", "parts": args.parts,
              "downloaded_at": datetime.now(timezone.utc).isoformat(),
              "objects": [{**asdict(o), "url": o.url} for o in objects]}
    path = paths.SEN1FLOODS11_DIR/("s2_manifest_"+"_".join(args.parts)+".json")
    path.write_text(json.dumps(result, indent=2))
    print(str(path), flush=True)


if __name__ == "__main__":
    main()
