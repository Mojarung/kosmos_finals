"""Black-box smoke path against the running containerized service.

Usage: python3 scripts/e2e_live.py [http://127.0.0.1:18000] [west,south,east,north] [scene_index]
Requires network access to Microsoft Planetary Computer on the first run.
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def request(base: str, path: str, params: dict | None = None, method: str = "GET", binary: bool = False):
    url = base + path + ("?" + urlencode(params) if params else "")
    with urlopen(Request(url, method=method), timeout=240) as response:
        data = response.read()
    return data if binary else json.loads(data)


def main() -> None:
    base = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:18000"
    bbox = sys.argv[2] if len(sys.argv) > 2 else "5.8,9.07,5.83,9.1"
    scene_index = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    scenes = request(base, "/api/live/discover", {"bbox": bbox})["scenes"]
    assert scenes and all(item["polarizations"] == ["VV", "VH"] for item in scenes)
    item = scenes[scene_index]
    prepared = request(base, "/api/live/prepare", {"bbox": bbox, "item_id": item["id"]}, "POST")
    scene_id = prepared["scene_id"]
    params = {"bbox": bbox, "scene_id": scene_id, "budget": 6500, "deadline": 8, "priority": "risk"}
    analysis = request(base, "/api/analysis", params)
    assert analysis["chip"]["source_kind"] == "live_sentinel1_rtc"
    assert analysis["chip"]["source_item_id"] == item["id"]
    assert analysis["region"]["data_status"] in {"covered", "partial_coverage"}
    assert analysis["region"]["valid_pixels"] > 0
    assert len(analysis["assets"]) == 10
    assert all(asset["portfolio_status"] == "scenario_locations_and_values" for asset in analysis["assets"])
    assert analysis["strategies"]["C"]["data_cost_rub"] <= 6500
    assert analysis["region"]["reference_pixels"] == 0
    assert analysis["region"]["model_quality"] is None
    low = request(base, "/api/analysis", {**params, "budget": 5000, "must_cover": analysis["assets"][0]["id"]})
    assert low["inputs"]["budget_rub"] == 5000
    assert low["inputs"]["must_cover"] == analysis["assets"][0]["id"]
    assert low["strategies"]["C"]["data_cost_rub"] <= 5000
    west, south, east, north = [float(value) for value in bbox.split(",")]
    pixel = request(base, "/api/pixel", {"scene_id": scene_id,
                                           "longitude": (west + east) / 2,
                                           "latitude": (south + north) / 2})
    assert pixel["status"] in {"ok", "no_valid_pixel"}
    if pixel["status"] == "ok":
        assert pixel["reference_water"] is None
    for layer in ("radar", "model", "probability", "uncertainty"):
        image = request(base, f"/api/demo/{scene_id}/{layer}.png", binary=True)
        assert image.startswith(b"\x89PNG\r\n\x1a\n")
    package = zipfile.ZipFile(io.BytesIO(request(base, "/api/export", params, binary=True)))
    assert package.testzip() is None
    assert {"analysis.json", "source_manifest.json", "flood_probability.tif", "flood_mask.tif",
            "flood_uncertainty.tif", "baseline_mask.tif"}.issubset(package.namelist())
    manifest = json.loads(package.read("source_manifest.json"))
    assert all(row["version"] == "Sentinel-1 RTC" for row in manifest)
    assert len(manifest) == 2 * len(prepared["source_item_ids"])
    try:
        request(base, "/api/live/discover", {"bbox": "5,9,6,10"})
        raise AssertionError("Oversized area was accepted")
    except HTTPError as error:
        assert error.code == 422
    print(json.dumps({"scene_id": scene_id, "observed_at": prepared["observed_at"],
                      "source_items": len(prepared["source_item_ids"]),
                      "valid_pixels": analysis["region"]["valid_pixels"],
                      "water_km2": analysis["region"]["model_water_area_km2"],
                      "assets": len(analysis["assets"]), "zones": len(analysis["zones"]),
                      "zip_files": len(package.namelist())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
