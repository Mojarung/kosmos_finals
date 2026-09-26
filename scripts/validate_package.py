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
from rasterio.transform import rowcol

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.planning import quote, rectangle_area_km2  # noqa: E402


REQUIRED = {
    "flood_probability.tif", "flood_mask.tif", "flood_uncertainty.tif",
    "baseline_mask.tif", "assets.geojson", "assets.csv", "asset_loss.csv",
    "candidate_orders.geojson", "procurement_plan.csv", "strategy_comparison.csv",
    "strategy_plans.json", "sensitivity.csv", "metrics.csv", "run_metadata.json",
    "source_manifest.json", "analysis.json", "zone_decisions.csv",
}
LOSS_STATUSES = {"ok", "partial", "no_data", "permanent_water"}


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
            require(row["status"] in LOSS_STATUSES, "Invalid loss status")
            if row["status"] == "no_data":
                require(row["p_flood"] == row["expected_loss_rub"] == "", "No-data loss is filled")
                continue
            if row["status"] == "permanent_water":
                require(float(row["expected_loss_rub"]) == 0 and row["permanent_water_at_point"] == "True",
                        f"{row['asset_id']}: loss on permanent water must be 0")
                continue
            probability = float(row["p_flood"])
            require(0 <= probability <= 1, "Probability outside [0,1]")
            asset = by_id[row["asset_id"]]
            expected = probability * float(asset["asset_value_rub"]) * float(asset["vulnerability_coef"])
            close(float(row["expected_loss_rub"]), expected, f"EL {row['asset_id']}")
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
            zones[zone_id] = {**feature["properties"], "_bounds": [west, south, east, north]}

        require(set(comparison) == set(plans) == {"A", "B", "C"}, "A/B/C strategies missing")
        require(not plans["A"] and float(comparison["A"]["data_cost_rub"]) == 0,
                "A must have no paid imagery")
        for strategy in ("B", "C"):
            selected = plans[strategy]
            require(len(selected) == len(set(selected)) and set(selected) <= set(zones),
                    f"{strategy}: invalid zones")
            priced = quote([{"id": zone_id, "area_km2": zones[zone_id]["area_km2"]} for zone_id in selected])
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
        for row in losses:
            if row["status"] != "ok":
                continue
            asset = by_id[row["asset_id"]]
            pixel_row, pixel_col = rowcol(p_transform, float(asset["longitude"]), float(asset["latitude"]))
            require(0 <= pixel_row < probability.shape[0] and 0 <= pixel_col < probability.shape[1]
                    and p_mask[pixel_row, pixel_col], f"{row['asset_id']}: asset pixel outside valid S1")
            close(float(row["p_flood"]), float(probability[pixel_row, pixel_col]),
                  f"{row['asset_id']}: p_flood differs from flood_probability.tif at the asset pixel", 1e-5)
        groups = {line["discount_group_id"] for line in orders}
        require(len(groups) <= 1 and (not groups or groups == {meta["pricing"]["discount_group_id"]}),
                "Discount group differs between lines and run metadata")
        decisions = {row["candidate_id"]: row for row in rows(archive, "zone_decisions.csv")}
        require(set(decisions) == set(zones), "Zone decisions do not match the candidate catalog")
        require({key for key, row in decisions.items() if row["selected_in_c"] == "true"} == set(plans["C"]),
                "Zone decisions disagree with plan C")
        region_assets = {row["asset_id"]: row for row in losses if row["in_region"] == "true"}
        estimated = sum(float(row["expected_loss_rub"]) for row in region_assets.values() if row["expected_loss_rub"])
        for strategy in ("B", "C"):
            covered = {asset_id for asset_id, row in region_assets.items() if any(
                float(zones[zone_id]["_bounds"][0]) <= float(by_id[asset_id]["longitude"]) <= float(zones[zone_id]["_bounds"][2])
                and float(zones[zone_id]["_bounds"][1]) <= float(by_id[asset_id]["latitude"]) <= float(zones[zone_id]["_bounds"][3])
                for zone_id in plans[strategy])}
            covered_loss = sum(float(region_assets[a]["expected_loss_rub"] or 0) for a in covered)
            if strategy == "C" or comparison["B"]["covered_expected_loss_rub"] not in {"0", "0.0"}:
                close(float(comparison[strategy]["covered_expected_loss_rub"]), covered_loss, f"{strategy}: covered EL")
                require(int(comparison[strategy]["covered_asset_count"]) == len(covered), f"{strategy}: covered assets")
            share = comparison[strategy]["coverage_share"]
            require((share == "") == (estimated == 0), f"{strategy}: coverage share must be empty only without exposure")
            if share:
                close(float(share), float(comparison[strategy]["covered_expected_loss_rub"]) / estimated,
                      f"{strategy}: coverage share", 0.0001)
            doubt = comparison[strategy]["covered_doubt_share"]
            require(doubt == "" or 0 <= float(doubt) <= 1, f"{strategy}: doubt share outside [0,1]")
        region_ok = [row["asset_id"] for row in losses if row["in_region"] == "true" and row["status"] != "no_data"]
        ranked = sorted((row for row in losses if row["rank"]), key=lambda row: int(row["rank"]))
        require([row["asset_id"] for row in ranked] == region_ok and all(
            float(a["expected_loss_rub"]) >= float(b["expected_loss_rub"]) for a, b in zip(ranked, ranked[1:])),
            "Ranks must order region assets by expected loss")
        asset_scenarios = [row for row in sensitivity if row["scenario_id"].startswith("asset_")]
        require({json.loads(row["changed_inputs_json"])["asset_id"] for row in asset_scenarios} == set(region_ok)
                and len(asset_scenarios) == 6 * len(region_ok), "Sensitivity must cover every region asset ±20% p/V/q")
        require(not region_ok or {json.loads(row["changed_inputs_json"])["input"] for row in asset_scenarios}
                == {"p", "V", "q"}, "p/V/q sensitivity missing")
        return {"status": "ok", "chip_id": meta["chip_id"], "assets": len(assets),
                "zones": len(zones), "valid_pixels": int(p_mask.sum()),
                "budget_rub": budget, "files": len(archive.namelist())}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/validate_package.py floodvalue-CHIP.zip")
    print(json.dumps(validate(sys.argv[1]), ensure_ascii=False, indent=2))
