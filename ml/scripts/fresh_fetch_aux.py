"""Fetch auxiliary layers for every Sen1Floods11 hand-labelled chip onto the chip grid.

All layers exist globally and before the event date, so a service can fetch the same
layers for any new frame:

* ``static``: Copernicus DEM GLO-30, GLO-30 HAND (height above nearest drainage) and
  ESA WorldCover 2021 -> ``data/aux/static/<chip>.npz``;
* ``rtc``: Sentinel-1 RTC of the event's relative orbit, 5-365 days before the S1 event
  date, ``K`` dates spread over the year (always including the latest one), read from
  the first overview (20 m) -> ``data/aux/rtc/<chip>/<date>__<item>__<pol>.npy``;
* ``aggregate``: per-chip median, 10th and 90th percentile in dB and the latest date
  -> ``data/aux/pre/<chip>.npz``.

The RTC stage opens each (item, polarisation) once and reads the windows of every chip
of the event from that handle, so neighbouring chips share downloaded blocks.
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import planetary_computer as pc
import pystac_client
import rasterio
from rasterio.enums import Resampling
from shapely.geometry import box, shape

from floodvalue.context_sources import (GDAL_ENV, L2A_ASSETS, STAC, aggregate_pre, fetch_static, nanmean,
                                        pick_dates, read_onto, retry)

ROOT = Path(__file__).resolve().parents[1]
S1_DIR = ROOT / "data/sen1floods11/data/flood_events/HandLabeled/S1Hand"
META = ROOT / "data/sen1floods11/Sen1Floods11_Metadata.geojson"
AUX = ROOT / "data/aux"
EVENT_ALIAS = {"Mekong": "Cambodia"}
def chip_grid(path: Path) -> dict:
    with rasterio.open(path) as src:
        return {"crs": src.crs, "transform": src.transform, "width": src.width, "height": src.height,
                "bounds": tuple(src.bounds)}


def all_chips() -> dict[str, list[str]]:
    chips: dict[str, list[str]] = {}
    for path in sorted(S1_DIR.glob("*_S1Hand.tif")):
        name = path.name.removesuffix("_S1Hand.tif")
        chips.setdefault(name.rsplit("_", 1)[0], []).append(name)
    return chips


# ---------------------------------------------------------------- static layers

def fetch_static_chip(name: str, catalog) -> None:
    target = AUX / "static" / f"{name}.npz"
    if target.exists():
        return
    grid = chip_grid(S1_DIR / f"{name}_S1Hand.tif")
    layers = fetch_static(grid, catalog)
    np.savez_compressed(target, dem=layers["dem"], hand=layers["hand"].astype(np.float16),
                        worldcover=layers["worldcover"])


def stage_static(args) -> None:
    (AUX / "static").mkdir(parents=True, exist_ok=True)
    catalog = pystac_client.Client.open(STAC)
    names = [n for ns in all_chips().values() for n in ns]
    started = time.time()
    with ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(fetch_static_chip, n, catalog): n for n in names}
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001 - rerun resumes the failed chips
                print(f"FAILED {futures[fut]}: {exc!r}", flush=True)
            if i % 25 == 0:
                print(f"static {i}/{len(names)} {time.time() - started:.0f}s", flush=True)


# ---------------------------------------------------------------- pre-event RTC

def rtc_tasks(k: int, min_gap: int) -> tuple[list[dict], dict]:
    meta = {f["properties"]["location"]: f["properties"] for f in json.loads(META.read_text())["features"]}
    catalog = pystac_client.Client.open(STAC)
    tasks, plan = [], {}
    for event, names in all_chips().items():
        props = meta[EVENT_ALIAS.get(event, event)]
        day = date.fromisoformat(props["s1_date"].replace("/", "-"))
        grids = {n: chip_grid(S1_DIR / f"{n}_S1Hand.tif") for n in names}
        bbox = [min(g["bounds"][0] for g in grids.values()), min(g["bounds"][1] for g in grids.values()),
                max(g["bounds"][2] for g in grids.values()), max(g["bounds"][3] for g in grids.values())]
        orbit = int(props["rel_orbit_num"])
        window = f"{day - timedelta(days=365)}/{day - timedelta(days=min_gap)}"
        items = [it for it in catalog.search(collections=["sentinel-1-rtc"], bbox=bbox, datetime=window).items()
                 if it.properties["sat:relative_orbit"] == orbit]
        by_date: dict[str, list] = {}
        for it in items:
            by_date.setdefault(str(it.datetime.date()), []).append(it)
        chosen = pick_dates(sorted(by_date), k)
        plan[event] = {"s1_date": str(day), "rel_orbit": orbit, "available_dates": sorted(by_date), "dates": chosen}
        for d in chosen:
            for it in by_date[d]:
                footprint = shape(it.geometry)
                covered = [n for n, g in grids.items() if footprint.intersects(box(*g["bounds"]))]
                if not covered:
                    continue
                for pol in ("vv", "vh"):
                    tasks.append({"event": event, "date": d, "item": it, "pol": pol, "chips": covered, "grids": grids})
    return tasks, plan


def run_rtc_task(task: dict, overview: int | None) -> int:
    item_tag = task["item"].id.split("_")[4]  # frame start time keeps frames of one pass apart
    todo = []
    for name in task["chips"]:
        out = AUX / "rtc" / name / f"{task['date']}__{item_tag}__{task['pol']}.npy"
        if not out.exists():
            todo.append((name, out))
    if not todo:
        return 0
    href = pc.sign(task["item"]).assets[task["pol"]].href

    def run() -> list[tuple[Path, np.ndarray]]:
        results = []
        with rasterio.Env(**GDAL_ENV):
            kwargs = {} if overview is None else {"overview_level": overview}
            with rasterio.open(href, **kwargs) as src:
                for name, out in todo:
                    lin = read_onto(src, task["grids"][name], Resampling.bilinear)
                    lin[~(lin > 0)] = np.nan
                    results.append((out, lin))
        return results

    for out, lin in retry(run):
        out.parent.mkdir(parents=True, exist_ok=True)
        with np.errstate(all="ignore"):
            np.save(out, (10 * np.log10(lin)).astype(np.float16))
    return len(todo)


def stage_rtc(args) -> None:
    tasks, plan = rtc_tasks(args.k, args.min_gap)
    (AUX / "rtc").mkdir(parents=True, exist_ok=True)
    (AUX / "rtc_plan.json").write_text(json.dumps(plan, indent=1))
    tasks.sort(key=lambda t: -len(t["chips"]))
    print(f"{len(tasks)} RTC tasks, {sum(len(t['chips']) for t in tasks)} chip reads", flush=True)
    started = time.time()
    with ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(run_rtc_task, t, args.overview): t for t in tasks}
        for i, fut in enumerate(as_completed(futures), 1):
            t = futures[fut]
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001 - rerun resumes the failed tasks
                import traceback
                print(f"FAILED {t['event']} {t['date']} {t['item'].id} {t['pol']}: {exc!r}", flush=True)
                traceback.print_exc()
            if i % 10 == 0:
                print(f"rtc {i}/{len(tasks)} {time.time() - started:.0f}s", flush=True)


def aggregate_chip(name: str) -> dict:
    folder = AUX / "rtc" / name
    frames: dict[str, dict[str, list[np.ndarray]]] = {}
    for path in sorted(folder.glob("*.npy")) if folder.exists() else []:
        day, _item, pol = path.stem.split("__")
        frames.setdefault(day, {}).setdefault(pol, []).append(np.load(path).astype(np.float32))
    stack, days = [], []
    for day in sorted(frames):
        if set(frames[day]) != {"vv", "vh"}:
            continue
        frame = np.stack([nanmean(frames[day]["vv"]), nanmean(frames[day]["vh"])])
        if np.isfinite(frame).all(axis=0).mean() < 0.05:
            continue
        stack.append(frame)
        days.append(day)
    shape_ = (2, 512, 512)
    if not stack:
        nan = np.full(shape_, np.nan, np.float16)
        np.savez_compressed(AUX / "pre" / f"{name}.npz", pre_med=nan, pre_p10=nan, pre_p90=nan, pre_last=nan,
                            pre_n=np.zeros(shape_[1:], np.uint8))
        return {"dates": []}
    stats = aggregate_pre(stack)
    np.savez_compressed(AUX / "pre" / f"{name}.npz", **{k: v.astype(np.float16) for k, v in stats.items() if k != "pre_n"},
                        pre_n=stats["pre_n"])
    return {"dates": days}


def stage_aggregate(args) -> None:
    (AUX / "pre").mkdir(parents=True, exist_ok=True)
    names = [n for ns in all_chips().values() for n in ns]
    manifest = {}
    with ThreadPoolExecutor(args.workers) as pool:
        for name, info in zip(names, pool.map(aggregate_chip, names)):
            manifest[name] = info
    (AUX / "pre_manifest.json").write_text(json.dumps(manifest, indent=1))
    counts = [len(v["dates"]) for v in manifest.values()]
    print(f"aggregated {len(names)} chips; dates per chip min {min(counts)} median {np.median(counts)}")


# ---------------------------------------------------------------- S2 L2A on the S2Hand date

L2A_BANDS = L2A_ASSETS + ("SCL",)


def l2a_tasks() -> list[dict]:
    meta = {f["properties"]["location"]: f["properties"] for f in json.loads(META.read_text())["features"]}
    catalog = pystac_client.Client.open(STAC)
    tasks = []
    for event, names in all_chips().items():
        props = meta[EVENT_ALIAS.get(event, event)]
        day = props["s2_date"].replace("/", "-")
        grids = {n: chip_grid(S1_DIR / f"{n}_S1Hand.tif") for n in names}
        bbox = [min(g["bounds"][0] for g in grids.values()), min(g["bounds"][1] for g in grids.values()),
                max(g["bounds"][2] for g in grids.values()), max(g["bounds"][3] for g in grids.values())]
        for it in catalog.search(collections=["sentinel-2-l2a"], bbox=bbox, datetime=f"{day}/{day}").items():
            footprint = shape(it.geometry)
            covered = [n for n, g in grids.items() if footprint.intersects(box(*g["bounds"]))]
            for band in L2A_BANDS if covered else ():
                tasks.append({"event": event, "item": it, "band": band, "chips": covered, "grids": grids})
    return tasks


def run_l2a_task(task: dict) -> int:
    item_tag = task["item"].id.split("_")[4] + "_" + task["item"].id.split("_")[3]  # tile + orbit
    todo = [(n, AUX / "s2l2a" / n / f"{item_tag}__{task['band']}.npy") for n in task["chips"]]
    todo = [(n, out) for n, out in todo if not out.exists()]
    if not todo:
        return 0
    href = pc.sign(task["item"]).assets[task["band"]].href
    resampling = Resampling.nearest if task["band"] == "SCL" else Resampling.bilinear

    def run() -> list[tuple[Path, np.ndarray]]:
        with rasterio.Env(**GDAL_ENV), rasterio.open(href) as src:
            return [(out, read_onto(src, task["grids"][n], resampling)) for n, out in todo]

    for out, arr in retry(run):
        if not np.isfinite(arr).any() or np.nanmax(arr) <= 0:
            continue  # the tile does not reach this chip or has no data here
        out.parent.mkdir(parents=True, exist_ok=True)
        np.save(out, np.nan_to_num(arr, nan=0).astype(np.uint16))
    return len(todo)


def stage_l2a(args) -> None:
    tasks = l2a_tasks()
    tasks.sort(key=lambda t: -len(t["chips"]))
    print(f"{len(tasks)} L2A tasks, {sum(len(t['chips']) for t in tasks)} chip reads", flush=True)
    started = time.time()
    with ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(run_l2a_task, t): t for t in tasks}
        for i, fut in enumerate(as_completed(futures), 1):
            t = futures[fut]
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001 - rerun resumes the failed tasks
                print(f"FAILED {t['event']} {t['item'].id} {t['band']}: {exc!r}", flush=True)
            if i % 50 == 0:
                print(f"l2a {i}/{len(tasks)} {time.time() - started:.0f}s", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["static", "rtc", "aggregate", "l2a"])
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--workers", type=int, default=48)
    parser.add_argument("--min-gap", type=int, default=5, help="days before the event that are excluded")
    parser.add_argument("--overview", type=int, default=0, help="COG overview level for RTC; -1 = full 10 m")
    args = parser.parse_args()
    args.overview = None if args.overview < 0 else args.overview
    {"static": stage_static, "rtc": stage_rtc, "aggregate": stage_aggregate, "l2a": stage_l2a}[args.stage](args)


if __name__ == "__main__":
    main()
