"""Validate a FloodValue submission ZIP; run with backend's demo dependencies.

Usage: backend/.venv/bin/python scripts/validate_package.py floodvalue-CHIP.zip
"""

from __future__ import annotations

import csv
import io
import json
import math
import sys
import zipfile
from pathlib import Path

import numpy as np
from rasterio.io import MemoryFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.planning import quote, rectangle_area_km2  # noqa: E402


REQUIRED = {
    "flood_probability.tif", "flood_mask.tif", "flood_uncertainty.tif",
    "baseline_mask.tif", "assets.geojson", "assets.csv", "asset_loss.csv",
    "candidate_orders.geojson", "procurement_plan.csv", "strategy_comparison.csv",
    "strategy_plans.json", "sensitivity.csv", "metrics.csv", "run_metadata.json",
    "source_manifest.json", "analysis.json",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def close(actual: float, expected: float, message: str, tolerance: float = 0.02) -> None:
    require(math.isclose(actual, expected, abs_tol=tolerance),
            f"{message}: {actual} != {expected}")


def rows(archive: zipfile.ZipFile, name: str) -> list[dict]:
    content = archive.read(name).decode("utf-8-sig")
    parsed = list(csv.DictReader(io.StringIO(content)))
    require(all(str(value).strip().lower() not in {"nan", "inf", "-inf", "infinity", "-infinity"}
                for row in parsed for value in row.values()), f"{name}: NaN/Inf")
    return parsed


def document(archive: zipfile.ZipFile, name: str):
    return json.loads(archive.read(name).decode(), parse_constant=lambda value:
                      (_ for _ in ()).throw(ValueError(f"{name}: {value}")))


def validate(path: str | Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        missing = REQUIRED - set(archive.namelist())
        require(not missing, f"Missing files: {sorted(missing)}")
        meta = document(archive, "run_metadata.json")
        analysis = document(archive, "analysis.json")
        assets = rows(archive, "assets.csv")
        losses = rows(archive, "asset_loss.csv")
        sensitivity = rows(archive, "sensitivity.csv")
        orders = rows(archive, "procurement_plan.csv")
        comparison = {row["strategy"]: row for row in rows(archive, "strategy_comparison.csv")}
        plans = document(archive, "strategy_plans.json")
        features = document(archive, "candidate_orders.geojson")["features"]

        require(len(assets) == len(losses) == 10, "Portfolio must contain exactly ten assets")
        require(len({row["asset_id"] for row in assets}) == 10, "Duplicate asset ID")
        require(len({row["asset_class"] for row in assets}) == 10, "Asset classes are not unique")
        require({row["asset_id"] for row in assets} == {row["asset_id"] for row in losses},
                "Loss rows do not match the portfolio")
        require(all(row["chip_id"] == meta["chip_id"] for row in assets + losses),
                "Chip IDs differ")
        by_id = {row["asset_id"]: row for row in assets}
        for row in losses:
            require(row["status"] in {"ok", "partial", "no_data"}, "Invalid loss status")
            if row["status"] == "no_data":
                require(row["p_flood"] == row["expected_loss_rub"] == "", "No-data loss is filled")
                continue
            probability = float(row["p_flood"])
            require(0 <= probability <= 1, "Probability outside [0,1]")
            asset = by_id[row["asset_id"]]
            expected = probability * float(asset["asset_value_rub"]) * float(asset["vulnerability_coef"])
            close(float(row["expected_loss_rub"]), expected, f"EL {row['asset_id']}")
        require(len(sensitivity) >= 60, "Sensitivity is incomplete")
        require({json.loads(row["changed_inputs_json"])["input"] for row in sensitivity
                 if row["scenario_id"].startswith("asset_")} == {"p", "V", "q"},
                "p/V/q sensitivity missing")
        require({json.loads(row["changed_inputs_json"])["input"] for row in sensitivity
                 if row["scenario_id"].startswith("plan_")} == {"budget", "tariff"},
                "Budget/tariff sensitivity missing")

        zones = {}
        for feature in features:
            geometry = feature["geometry"]
            ring = geometry["coordinates"]
            require(geometry["type"] == "Polygon" and len(ring) == 1 and len(ring[0]) == 5,
                    "Zone must be a simple polygon without holes")
            points = ring[0]
            require(points[0] == points[-1], "Zone ring is not closed")
            west, south = points[0]
            east, north = points[2]
            require(west < east and south < north and points == [[west, south], [east, south],
                    [east, north], [west, north], [west, south]], "Zone is not a valid rectangle")
            area = rectangle_area_km2([west, south, east, north])
            require(area >= 1, "Zone is smaller than 1 km² before rounding")
            close(float(feature["properties"]["area_km2"]), area, "Zone area", 0.00051)
            zone_id = feature["properties"]["candidate_id"]
            require(zone_id not in zones, "Duplicate candidate ID")
            zones[zone_id] = feature["properties"]

        require(set(comparison) == set(plans) == {"A", "B", "C"}, "A/B/C strategies missing")
        require(not plans["A"] and float(comparison["A"]["data_cost_rub"]) == 0,
                "A must have no paid imagery")
        for strategy in ("B", "C"):
            selected = plans[strategy]
            require(len(selected) == len(set(selected)) and set(selected) <= set(zones),
                    f"{strategy}: invalid zones")
            priced = quote([zones[zone_id] | {"id": zone_id} for zone_id in selected])
            close(float(comparison[strategy]["data_cost_rub"]), priced["total_rub"],
                  f"{strategy}: total price")
            lines = [line for line in orders if line["strategy"] == strategy]
            require({line["candidate_id"] for line in lines} == set(selected),
                    f"{strategy}: procurement lines differ")
            close(sum(float(line["cost_rub"]) for line in lines), priced["total_rub"],
                  f"{strategy}: line sum")
        budget = float(meta["budget_rub"])
        require(comparison["C"]["budget_feasible"] == "false" or
                float(comparison["C"]["data_cost_rub"]) <= budget, "C exceeds budget")
        require(comparison["B"]["budget_feasible"] == str(
            float(comparison["B"]["data_cost_rub"]) <= budget).lower(), "B budget status wrong")

        with MemoryFile(archive.read("flood_probability.tif")) as memory:
            with memory.open() as src:
                probability = src.read(1)
                p_mask = src.read_masks(1) > 0
                p_crs, p_transform = src.crs, src.transform
        with MemoryFile(archive.read("flood_mask.tif")) as memory:
            with memory.open() as src:
                flood = src.read(1)
                f_mask = src.read_masks(1) > 0
                require(src.crs == p_crs and src.transform == p_transform and
                        src.shape == probability.shape, "Raster grids disagree")
        require(np.array_equal(p_mask, f_mask), "Raster nodata masks disagree")
        require(np.isfinite(probability[p_mask]).all(), "Probability contains NaN/Inf")
        require(((probability[p_mask] >= 0) & (probability[p_mask] <= 1)).all(),
                "Raster probability outside [0,1]")
        require(np.array_equal(flood[p_mask], (probability[p_mask] >= meta["model_threshold"]).astype("uint8")),
                "Flood mask differs from p ≥ frozen threshold")
        return {"status": "ok", "chip_id": meta["chip_id"], "assets": len(assets),
                "zones": len(zones), "valid_pixels": int(p_mask.sum()),
                "budget_rub": budget, "files": len(archive.namelist())}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/validate_package.py floodvalue-CHIP.zip")
    print(json.dumps(validate(sys.argv[1]), ensure_ascii=False, indent=2))
