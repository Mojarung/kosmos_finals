"""Pinned, resumable development-only L2A acquisition for the boosting comparison."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds

from floodvalue import paths
from floodvalue.chips import Chip, layer_path
from floodvalue.context_sources import GDAL_ENV, L2A_ASSETS, SCL_UNUSABLE, l2a_offset, merge_l2a, read_onto
from floodvalue.optical import event_dates, optical_date_allowed

ROOT = paths.DATA_DIR / "l2a_boost"
PLAN = ROOT / "acquisition.json"


def development():
    return json.loads((paths.PIPELINE_ROOT/"research/fresh/folds.json").read_text())["folds"]


def grid(name):
    with rasterio.open(layer_path(paths.SEN1FLOODS11_DIR, Chip.from_name(name), "S1Hand")) as src:
        return dict(crs=src.crs, transform=src.transform, width=src.width, height=src.height, bounds=tuple(src.bounds))


def prepare():
    from shapely.geometry import shape, box
    from floodvalue.context_sources import catalog

    names = sorted(development())
    parts = json.loads((paths.ARTIFACTS_DIR/"split.json").read_text())["parts"]
    if not set(names) <= set(parts["train"]+parts["valid"]):
        raise ValueError("non-development chip in L2A plan")
    ROOT.mkdir(parents=True, exist_ok=True)
    if PLAN.exists():
        plan = json.loads(PLAN.read_text())
        if plan["names"] != names:
            raise ValueError("development plan changed")
        return plan
    dates = event_dates(paths.SEN1FLOODS11_DIR)
    client = catalog()
    items, per_chip = {}, {}
    for event in sorted({Chip.from_name(n).event for n in names}):
        selected = [n for n in names if Chip.from_name(n).event == event]
        if not optical_date_allowed(**dates[event]):
            per_chip.update({n: [] for n in selected})
            continue
        bounds = {n: transform_bounds(g["crs"], "EPSG:4326", *g["bounds"]) for n in selected for g in [grid(n)]}
        bbox = [min(b[0] for b in bounds.values()), min(b[1] for b in bounds.values()),
                max(b[2] for b in bounds.values()), max(b[3] for b in bounds.values())]
        day = dates[event]["s2_date"].replace("/", "-")
        found = sorted(client.search(collections=["sentinel-2-l2a"], bbox=bbox, datetime=f"{day}/{day}").items(), key=lambda x: x.id)
        for n in selected:
            per_chip[n] = []
        for item in found:
            covered = [n for n in selected if shape(item.geometry).intersects(box(*bounds[n]))]
            if covered:
                items[item.id] = {"stac": item.to_dict(), "chips": covered, "offset": l2a_offset(item)}
                for n in covered:
                    per_chip[n].append(item.id)
        print("catalog", event, len(selected), len(found), flush=True)
    plan = {"version": 1, "names": names, "dates": dates, "items": items, "per_chip": per_chip,
            "rule": "development only; exact allowed S2Hand calendar date; service SCL mask and baseline offset"}
    PLAN.write_text(json.dumps(plan, indent=2, allow_nan=False))
    return plan


def raw_path(name, item_id, band):
    return ROOT/"raw"/name/item_id/(band+".npy")


def download_task(item_id, entry, band):
    import planetary_computer as pc
    import pystac

    todo = [n for n in entry["chips"] if not raw_path(n, item_id, band).exists()]
    if not todo:
        return
    item = pystac.Item.from_dict(entry["stac"])
    grids = {n: grid(n) for n in todo}
    env = {**GDAL_ENV, "GDAL_HTTP_TIMEOUT": "45", "GDAL_HTTP_MAX_RETRY": "1"}
    for attempt in range(3):
        try:
            href = pc.sign(item).assets[band].href
            with rasterio.Env(**env), rasterio.open(href) as src:
                for n in todo:
                    path = raw_path(n, item_id, band)
                    if path.exists():
                        continue
                    arr = read_onto(src, grids[n], Resampling.nearest if band == "SCL" else Resampling.bilinear)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.with_suffix(".tmp").open("wb") as file:
                        np.save(file, np.nan_to_num(arr, nan=0).astype(np.uint16))
                    path.with_suffix(".tmp").replace(path)
            return
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2*(attempt+1))


def assemble(plan):
    signature = hashlib.sha256(PLAN.read_bytes()).hexdigest()
    folder = ROOT/"chips"
    folder.mkdir(exist_ok=True)
    summary = {}
    for name in plan["names"]:
        path = folder/(name+".npz")
        if path.exists():
            with np.load(path) as z:
                if str(z["plan_sha256"]) != signature:
                    raise ValueError("chip acquisition changed")
                summary[name] = float(z["usable"].mean())
            continue
        tiles = []
        for item_id in plan["per_chip"][name]:
            files = [raw_path(name, item_id, b) for b in (*L2A_ASSETS, "SCL")]
            if not all(p.exists() for p in files):
                raise FileNotFoundError(f"incomplete acquisition for {name}/{item_id}")
            bands = np.stack([np.load(p) for p in files[:-1]]).astype(np.float32)
            scl = np.load(files[-1])
            offset = plan["items"][item_id]["offset"]
            usable = ~np.isin(scl, SCL_UNUSABLE) & (bands > offset).all(axis=0)
            tiles.append(((bands-offset)/10000., usable))
        if tiles:
            reflectance, usable = merge_l2a(tiles)
        else:
            g = grid(name)
            reflectance = np.zeros((12, g["height"], g["width"]), np.float32)
            usable = np.zeros(reflectance.shape[1:], bool)
        with path.with_suffix(".tmp").open("wb") as f:
            np.savez_compressed(f, reflectance=reflectance.astype(np.float16), usable=usable,
                                name=name, plan_sha256=signature)
        path.with_suffix(".tmp").replace(path)
        summary[name] = float(usable.mean())
    result = {"plan_sha256": signature, "chips": summary, "n": len(summary),
              "without_optics": sum(v == 0 for v in summary.values())}
    (ROOT/"ready.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "chips"}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    plan = prepare()
    tasks = [(key, entry, band) for key, entry in plan["items"].items() for band in (*L2A_ASSETS, "SCL")]
    tasks.sort(key=lambda t: -len(t[1]["chips"]))
    print("download tasks", len(tasks), flush=True)
    errors = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(download_task, *task): (task[0], task[2]) for task in tasks}
        for i, future in enumerate(as_completed(futures), 1):
            try:
                future.result()
            except Exception as exc:
                errors.append({"task": futures[future], "error": type(exc).__name__})
                print("FAILED", futures[future], type(exc).__name__, flush=True)
            if i % 20 == 0:
                print("download", i, "/", len(tasks), flush=True)
    if errors:
        (ROOT/"errors.json").write_text(json.dumps(errors, indent=2))
        raise RuntimeError(f"{len(errors)} incomplete downloads; rerun resumes; no ready cache written")
    assemble(plan)


if __name__ == "__main__":
    main()
