"""Validate a FloodValue submission ZIP; run with backend's demo dependencies.

Usage: uv run --project backend --extra demo python scripts/validate_package.py floodvalue-CHIP.zip
"""

from __future__ import annotations

import csv
import io
import json
import math
import sys
import zipfile
from datetime import date
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
# The case allows only these; a point on permanent water is ok with the mandatory EL = p·V·q.
LOSS_STATUSES = {"ok", "partial", "no_data"}
# Fields every catalog order carries by the case, plus delivery_hours that timeliness needs.
CASE_FIELDS = ("candidate_id", "sensor_type", "resolution_m", "acquisition_type", "data_role", "observation_at",
               "available_at", "processing_level", "usage_type", "guaranteed_purchase", "area_km2",
               "base_rate_rub_km2", "base_rate_status", "base_rate_source", "delivery_hours")
TABLES = {"acquisition_type": {"new", "operational", "archive"}, "data_role": {"event_observation", "context"},
          "processing_level": {"L0", "L1", "L2"}, "usage_type": {"internal", "limited", "unrestricted"},
          "sensor_type": {"optical", "sar"}}
ORDER_FIELDS = ("candidate_id", "area_km2", "sensor_type", "resolution_m", "processing_level", "usage_type",
                "acquisition_type", "guaranteed_purchase", "observation_at")
LINE_NUMBERS = ("area_km2", "base_rate_rub_km2", "processing_coef", "usage_coef", "freshness_coef", "discount_coef",
                "previous_group_area_km2", "basket_group_area_km2", "group_area_km2", "unit_price_rub_km2", "cost_rub")
OPERATIONAL_DAYS = 90


def freshness(order: dict, calculation_date: date) -> float:
    """Т by the 90-day rule, written out apart from the price engine: new 1.8, up to 90 days 1, older 0.6."""
    if order["acquisition_type"] == "new":
        return 1.8
    age = (calculation_date - date.fromisoformat(str(order["observation_at"])[:10])).days
    return 1.0 if age <= OPERATIONAL_DAYS else 0.6


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
            require(row["status"] in LOSS_STATUSES,
                    f"{row['asset_id']}: invalid loss status {row['status']!r}, expected one of {sorted(LOSS_STATUSES)}")
            if row["status"] == "no_data" or (row["status"] == "partial" and not row["p_flood"]):
                require(row["p_flood"] == row["expected_loss_rub"] == row["rank"] == "",
                        f"{row['asset_id']}: unestimated p, EL and rank must be empty, not 0")
            else:
                probability = float(row["p_flood"])
                require(0 <= probability <= 1, "Probability outside [0,1]")
                asset = by_id[row["asset_id"]]
                expected = probability * float(asset["asset_value_rub"]) * float(asset["vulnerability_coef"])
                close(float(row["expected_loss_rub"]), expected, f"EL {row['asset_id']}")
            if "flood_expected_loss_rub" in row:
                # Additional flood-only model: 0 on JRC permanent water, the mandatory EL elsewhere.
                if row.get("permanent_water_at_point") == "True":
                    require(row["flood_expected_loss_rub"] != "" and float(row["flood_expected_loss_rub"]) == 0,
                            f"{row['asset_id']}: flood-only loss on permanent water must be 0")
                elif row["expected_loss_rub"] == "":
                    require(row["flood_expected_loss_rub"] == "", f"{row['asset_id']}: flood-only loss without EL")
                else:
                    close(float(row["flood_expected_loss_rub"]), float(row["expected_loss_rub"]),
                          f"{row['asset_id']}: flood-only loss off permanent water must equal EL")
        require({json.loads(row["changed_inputs_json"])["input"] for row in sensitivity
                 if row["scenario_id"].startswith("plan_")} == {"budget", "tariff"},
                "Budget/tariff sensitivity missing")

        calculation_date = date.fromisoformat(meta["pricing"]["calculation_date"])
        deadline = int(meta["deadline_hours"])
        zones = {}
        for feature in features:
            properties = feature["properties"]
            missing = [field for field in CASE_FIELDS if field not in properties]
            require(not missing, f"{properties.get('candidate_id')}: catalog order lacks {missing}")
            name = properties["candidate_id"]
            for field, allowed in TABLES.items():
                require(properties[field] in allowed, f"{name}: {field} {properties[field]!r} is outside {sorted(allowed)}")
            require(isinstance(properties["guaranteed_purchase"], bool), f"{name}: guaranteed_purchase must be true/false")
            require(properties["base_rate_status"] == "scenario"
                    or (properties["base_rate_status"] == "official" and properties["base_rate_source"]),
                    f"{name}: an official base rate needs its source")
            if properties["acquisition_type"] != "new":
                require(properties["guaranteed_purchase"] is False, f"{name}: existing imagery is never a guaranteed purchase")
                age = (calculation_date - date.fromisoformat(str(properties["observation_at"])[:10])).days
                require(age >= 0 and (properties["acquisition_type"] == "operational") == (age <= OPERATIONAL_DAYS),
                        f"{name}: {properties['acquisition_type']} but acquired {age} days before the calculation date")
            if properties["acquisition_type"] == "archive":
                require(properties["data_role"] == "context",
                        f"{name}: an archive frame is context, never an observation of current water")
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
        pricing = meta["pricing"]
        basket_groups = {}
        for strategy in ("B", "C"):
            selected = plans[strategy]
            require(len(selected) == len(set(selected)) and set(selected) <= set(zones),
                    f"{strategy}: invalid zones")
            lines = [line for line in orders if line["strategy"] == strategy]
            require([line["candidate_id"] for line in lines] == selected,
                    f"{strategy}: procurement lines differ from strategy_plans.json")
            # Re-price the basket with the price engine from the catalog conditions alone.
            previous = {line["discount_group_id"][len(strategy) + 1:]: float(line["previous_group_area_km2"])
                        for line in lines if line["discount_group_id"].startswith(f"{strategy}_")}
            priced = quote([{key: zones[zone_id][key] for key in ORDER_FIELDS} for zone_id in selected],
                           strategy=strategy, calculation_date=calculation_date, previous_group_area_km2=previous)
            for line, expected in zip(lines, priced["lines"], strict=True):
                zone_id, catalog = line["candidate_id"], zones[line["candidate_id"]]
                require(line["discount_group_id"] == expected["discount_group_id"],
                        f"{strategy}: {zone_id} discount group {line['discount_group_id']} != {expected['discount_group_id']}")
                for key in LINE_NUMBERS:
                    close(float(line[key]), expected[key], f"{strategy}: {zone_id} {key}", 1e-6)
                for key in ("sensor_type", "processing_level", "usage_type", "acquisition_type"):
                    require(line[key] == catalog[key], f"{strategy}: {zone_id} {key} differs from the catalog")
                require(line["guaranteed_purchase"] == str(catalog["guaranteed_purchase"]).lower()
                        and float(line["resolution_m"]) == float(catalog["resolution_m"]),
                        f"{strategy}: {zone_id} product differs from the catalog")
                close(float(line["freshness_coef"]), freshness(catalog, calculation_date),
                      f"{strategy}: {zone_id} freshness_coef breaks the 90-day rule", 1e-9)
                close(float(line["previous_group_area_km2"]), float(pricing["previous_group_area_km2"]),
                      f"{strategy}: {zone_id} earlier group area differs from run metadata", 1e-9)
            groups = {line["discount_group_id"] for line in lines}
            for group_id in groups:
                require(group_id.startswith(f"{strategy}_"), f"{strategy}: discount group {group_id} is not specific to basket {strategy}")
                members = [line for line in lines if line["discount_group_id"] == group_id]
                area = sum(float(line["area_km2"]) for line in members) + float(members[0]["previous_group_area_km2"])
                require(len({(line["group_area_km2"], line["discount_coef"]) for line in members}) == 1,
                        f"{strategy}: lines of discount group {group_id} disagree on S or P")
                close(float(members[0]["group_area_km2"]), area, f"{strategy}: S of discount group {group_id}", 0.0005)
            require(groups == set(pricing["discount_groups"][strategy]),
                    f"{strategy}: discount groups differ between lines and run metadata")
            basket_groups[strategy] = groups
            close(float(comparison[strategy]["data_cost_rub"]), priced["total_rub"],
                  f"{strategy}: total price")
            close(sum(float(line["cost_rub"]) for line in lines), priced["total_rub"],
                  f"{strategy}: line sum")
        require(not basket_groups["B"] & basket_groups["C"], "B and C share a discount group")
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
            pixel_p = float(probability[pixel_row, pixel_col])
            chance = 1 - pixel_p if flood[pixel_row, pixel_col] == 1 else pixel_p
            close(float(row["uncertainty"]), chance,
                  f"{row['asset_id']}: uncertainty is not the map error chance at the asset pixel", 1e-4)
        if len(basket_groups["C"]) == 1:
            require(basket_groups["C"] == {pricing["discount_group_id"]}, "Discount group of C differs from run metadata")
        decisions = {row["candidate_id"]: row for row in rows(archive, "zone_decisions.csv")}
        require(set(decisions) == set(zones), "Zone decisions do not match the candidate catalog")
        require({key for key, row in decisions.items() if row["selected_in_c"] == "true"} == set(plans["C"]),
                "Zone decisions disagree with plan C")
        region_assets = {row["asset_id"]: row for row in losses if row["in_region"] == "true"}
        estimated = sum(float(row["expected_loss_rub"]) for row in region_assets.values() if row["expected_loss_rub"])

        def observes(zone_id: str) -> bool:
            """Only an observation of the event delivered by the deadline covers anything."""
            return (zones[zone_id]["data_role"] == "event_observation"
                    and float(zones[zone_id]["delivery_hours"]) <= deadline)

        for strategy in ("B", "C"):
            row = comparison[strategy]
            covered = {asset_id for asset_id, asset in region_assets.items() if any(
                float(zones[zone_id]["_bounds"][0]) <= float(by_id[asset_id]["longitude"]) <= float(zones[zone_id]["_bounds"][2])
                and float(zones[zone_id]["_bounds"][1]) <= float(by_id[asset_id]["latitude"]) <= float(zones[zone_id]["_bounds"][3])
                for zone_id in plans[strategy] if observes(zone_id))}
            # expected_loss_rub of asset_loss.csv is the mandatory EL, so this is the mandatory covered EL.
            covered_loss = sum(float(region_assets[a]["expected_loss_rub"] or 0) for a in covered)
            rule = "context (archive) and late orders add no coverage"
            close(float(row["covered_expected_loss_rub"]), covered_loss,
                  f"{strategy}: covered EL must sum the mandatory EL of covered assets; {rule}")
            require(int(row["covered_asset_count"]) == len(covered), f"{strategy}: covered assets; {rule}")
            if row.get("covered_flood_loss_rub", "") != "":
                flood = sum(float(region_assets[a].get("flood_expected_loss_rub") or 0) for a in covered)
                close(float(row["covered_flood_loss_rub"]), flood, f"{strategy}: covered flood-only loss")
            share = row["coverage_share"]
            require((share == "") == (estimated == 0), f"{strategy}: coverage share must be empty only without exposure")
            if share:
                close(float(share), float(row["covered_expected_loss_rub"]) / estimated,
                      f"{strategy}: coverage share", 0.0001)
            else:
                require(row.get("coverage_status", "") != "", f"{strategy}: empty coverage share needs its reason")
            doubt = row["covered_doubt_share"]
            require(doubt == "" or 0 <= float(doubt) <= 1, f"{strategy}: doubt share outside [0,1]")
        # The declared product rule: per footprint optics if on time, else radar if on time; archive never bought.
        footprints: dict[str, list[dict]] = {}
        for properties in zones.values():
            footprints.setdefault(properties.get("footprint_id", properties["candidate_id"]), []).append(properties)
        for strategy in ("B", "C"):
            for zone_id in plans[strategy]:
                require(zones[zone_id]["data_role"] == "event_observation",
                        f"{strategy}: {zone_id} is context imagery; the product rule never buys it as an observation")
                offers = sorted((item for item in footprints[zones[zone_id].get("footprint_id", zone_id)]
                                 if item["data_role"] == "event_observation" and item["acquisition_type"] == "new"),
                                key=lambda item: item["sensor_type"] != "optical")
                pick = next((item for item in offers if float(item["delivery_hours"]) <= deadline), offers[0])
                require(pick["candidate_id"] == zone_id,
                        f"{strategy}: {zone_id} breaks the product rule, which takes {pick['candidate_id']}")
        estimated_rows = [row for row in region_assets.values() if row["expected_loss_rub"] != ""]
        region_ok = [row["asset_id"] for row in estimated_rows]
        by_loss = [row["asset_id"] for row in sorted(
            estimated_rows, key=lambda row: (-float(row["expected_loss_rub"]), row["asset_id"]))]
        ranked = sorted((row for row in losses if row["rank"]), key=lambda row: int(row["rank"]))
        require([int(row["rank"]) for row in ranked] == list(range(1, len(ranked) + 1))
                and [row["asset_id"] for row in ranked] == by_loss,
                "Ranks must run 1..n over estimated region assets by expected loss, ties by asset_id")
        asset_scenarios = [row for row in sensitivity if row["scenario_id"].startswith("asset_")]
        require({json.loads(row["changed_inputs_json"])["asset_id"] for row in asset_scenarios} == set(region_ok)
                and len(asset_scenarios) == 6 * len(region_ok), "Sensitivity must cover every region asset ±20% p/V/q")
        require(not region_ok or {json.loads(row["changed_inputs_json"])["input"] for row in asset_scenarios}
                == {"p", "V", "q"}, "p/V/q sensitivity missing")
        return {"status": "ok", "chip_id": meta["chip_id"], "assets": len(assets),
                "zones": len(zones), "footprints": len(footprints), "valid_pixels": int(p_mask.sum()),
                "budget_rub": budget, "deadline_hours": deadline, "calculation_date": calculation_date.isoformat(),
                "plans": {key: plans[key] for key in ("B", "C")},
                "costs_rub": {key: float(comparison[key]["data_cost_rub"]) for key in ("B", "C")},
                "discount_groups": {key: sorted(basket_groups[key]) for key in ("B", "C")},
                "files": len(archive.namelist())}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/validate_package.py floodvalue-CHIP.zip")
    print(json.dumps(validate(sys.argv[1]), ensure_ascii=False, indent=2))
