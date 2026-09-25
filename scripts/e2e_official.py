"""Black-box official-chip ingest → analysis → export against the running service.

Usage: python3 scripts/e2e_official.py [http://127.0.0.1:18000] [Ghana_1078550]
The first run downloads S1 and its diagnostic label from Sen1Floods11.
"""

from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def get(url: str) -> dict:
    with urlopen(url, timeout=30) as response:
        return json.load(response)


def main() -> None:
    base = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:18000"
    chip_id = sys.argv[2] if len(sys.argv) > 2 else "Ghana_1078550"
    catalog = get(f"{base}/api/chips")
    assert chip_id in {chip["chip_id"] for chip in catalog["chips"]}
    payload = json.dumps({"chip_id": chip_id}).encode()
    request = Request(f"{base}/api/scenes", data=payload, method="POST",
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:
        job = json.load(response)
    deadline = time.monotonic() + 240
    while job["stage"] not in {"done", "error"} and time.monotonic() < deadline:
        time.sleep(1)
        job = get(f"{base}/api/jobs/{job['id']}")
    assert job["stage"] == "done", job
    params = urlencode({"scene_id": chip_id, "budget": 6500, "deadline": 8, "priority": "risk"})
    analysis = get(f"{base}/api/analysis?{params}")
    assert analysis["chip"]["chip_id"] == chip_id
    assert analysis["region"]["valid_pixels"] > 0
    assert len(analysis["assets"]) == 10
    assert analysis["strategies"]["C"]["data_cost_rub"] <= 6500
    with urlopen(f"{base}/api/export?{params}", timeout=30) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    assert archive.testzip() is None
    assert {"analysis.json", "source_manifest.json", "flood_probability.tif", "flood_mask.tif",
            "flood_uncertainty.tif", "baseline_mask.tif"}.issubset(archive.namelist())
    print(json.dumps({"scene_id": chip_id, "valid_pixels": analysis["region"]["valid_pixels"],
                      "assets": len(analysis["assets"]), "zones": len(analysis["zones"]),
                      "zip_files": len(archive.namelist()), "job_timings": job["timings"]}))


if __name__ == "__main__":
    main()
