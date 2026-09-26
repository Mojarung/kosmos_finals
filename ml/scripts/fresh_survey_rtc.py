"""Survey Planetary Computer Sentinel-1 RTC coverage around each Sen1Floods11 event.

For every event the union of its hand-labelled chip footprints is searched for the
year before the S1 event date. The output lists acquisitions per relative orbit and
whether the event acquisition itself is present in RTC.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pystac_client
import planetary_computer as pc
import rasterio

ROOT = Path(__file__).resolve().parents[1]
S1_DIR = ROOT / "data/sen1floods11/data/flood_events/HandLabeled/S1Hand"
META = ROOT / "data/sen1floods11/Sen1Floods11_Metadata.geojson"
EVENT_ALIAS = {"Mekong": "Cambodia"}


def event_meta() -> dict[str, dict]:
    features = json.loads(META.read_text())["features"]
    return {f["properties"]["location"]: f["properties"] for f in features}


def event_bboxes() -> dict[str, list[float]]:
    boxes: dict[str, list[float]] = {}
    for path in sorted(S1_DIR.glob("*_S1Hand.tif")):
        event = path.name.rsplit("_", 2)[0]
        with rasterio.open(path) as src:
            b = src.bounds
        box = boxes.setdefault(event, [b.left, b.bottom, b.right, b.top])
        box[0], box[1] = min(box[0], b.left), min(box[1], b.bottom)
        box[2], box[3] = max(box[2], b.right), max(box[3], b.top)
    return boxes


def main() -> None:
    meta = event_meta()
    catalog = pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace
    )
    report = {}
    for event, bbox in event_bboxes().items():
        props = meta[EVENT_ALIAS.get(event, event)]
        day = date.fromisoformat(props["s1_date"].replace("/", "-"))
        window = f"{day - timedelta(days=400)}/{day + timedelta(days=1)}"
        items = list(catalog.search(collections=["sentinel-1-rtc"], bbox=bbox, datetime=window).items())
        orbits = Counter(it.properties["sat:relative_orbit"] for it in items)
        same = [it for it in items if it.properties["sat:relative_orbit"] == int(props["rel_orbit_num"])]
        on_day = [it.id for it in same if it.datetime.date() == day]
        report[event] = {
            "s1_date": str(day),
            "rel_orbit": int(props["rel_orbit_num"]),
            "bbox": bbox,
            "items": len(items),
            "orbits": dict(orbits),
            "same_orbit_items": len(same),
            "same_orbit_dates": sorted({str(it.datetime.date()) for it in same}),
            "event_items": on_day,
        }
        print(event, day, "items", len(items), "same orbit", len(same), "event", on_day, dict(orbits), flush=True)
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "research/fresh/rtc_survey.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
