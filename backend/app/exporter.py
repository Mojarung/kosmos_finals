"""Build a single, internally consistent case package for the selected chip."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone

from .analysis import context
from .catalog import metadata, scene_dir
from .planning import (BASE_RATE, FRESHNESS_COEF, LEGAL_EDITION, PROCESSING_COEF, SUITABILITY_RULE, USAGE_COEF,
                       as_polygon)
from .scene_builder import PROBABILITY_RULE
from .sensitivity import asset_plan_sensitivity
from .zoning import DOUBT_RULE, DOUBT_THRESHOLD, ZONE_RULE

REASONS = {
    "selected": "in basket C",
    "late": "delivery after the decision deadline",
    "over_budget": "adding it to basket C exceeds the budget",
    "nothing_new": "adds nothing to the goal: its assets and doubtful area are already covered",
    "cheaper_equal": "an equal-value basket is cheaper",
    "plan_infeasible": "no basket satisfies the constraints",
}
# Every number the plan depends on, where it comes from and why this value.
PARAMETERS = [
    {"name": "model_threshold", "value": 0.41, "source": "ml/REPORT.md §7",
     "basis": "max IoU on validation; IoU curve is flat between 0.3 and 0.5"},
    {"name": "baseline_threshold_db", "value": -23.6, "source": "ml/artifacts/baseline/baseline.json",
     "basis": "VH threshold fitted on train, channel chosen on validation"},
    {"name": "doubt_threshold", "value": DOUBT_THRESHOLD, "source": "ml/artifacts/evaluation/doubt_threshold.json",
     "basis": "top decile of the map error chance on validation pixels; checked on independent test_core: "
              "29.4% of doubtful pixels are wrong vs 4.6% overall"},
    {"name": "uncertainty_zone_sides_km", "value": "1.12, 1.68, 2.24", "source": "app/zoning.py",
     "basis": "1.12 km square is the 1.25 km² object zone above the 1 km² minimum; 1.5x and 2x keep zones within a 6-7 thousand RUB budget"},
    {"name": "max_uncertainty_zones", "value": 5, "source": "app/zoning.py",
     "basis": "compute limit: exact search over 2^16 baskets with 10 object zones and the broad zone"},
    {"name": "base_rate_rub_km2", "value": float(BASE_RATE), "source": "case control example",
     "basis": "scenario rate; no supplier quote; status scenario everywhere"},
    {"name": "processing_coef_O", "value": float(PROCESSING_COEF), "source": "PP RF 840, L2",
     "basis": "the operator needs an orthorectified product ready for analysis"},
    {"name": "usage_coef_P", "value": float(USAGE_COEF), "source": "PP RF 840, internal use",
     "basis": "the imagery stays inside the operator; results are shared, the image is not"},
    {"name": "freshness_coef_T", "value": float(FRESHNESS_COEF), "source": "PP RF 840, new acquisition",
     "basis": "the flood is happening now; archive imagery cannot show current water"},
    {"name": "resolution_m", "value": 1, "source": "team choice",
     "basis": "1 m optics separates a building from water at its wall; Sentinel-1 at 10 m cannot"},
    {"name": "delivery_hours", "value": 6, "source": "scenario",
     "basis": "assumed tasking and delivery time inside the 6-12 h decision window of the case"},
    {"name": "budget_rub", "value": None, "source": "operator input",
     "basis": "default 6500 RUB, about one sixth of a broad purchase of the chip; the frontier shows other budgets"},
]


def _csv(headers: list[str], rows: list[list[object]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return output.getvalue()


def _blank(value: object) -> object:
    return "" if value is None else value


def _strategy_status(key: str, item: dict) -> str:
    if key == "A":
        return "no_paid_data"
    if not item.get("timely", True):
        return "late_for_deadline"
    if not item["budget_feasible"]:
        return "counterfactual_over_budget"
    return "within_budget" if item.get("constraints_feasible", True) else "constraints_infeasible"


def package(result: dict) -> io.BytesIO:
    scene_id = result["chip"]["chip_id"]
    info = metadata(scene_id)
    inputs = result["inputs"]
    ctx = context(scene_id, inputs.get("bbox"))
    all_assets = ctx.portfolio
    region_ids = [asset["id"] for asset in result["assets"]]
    region_rank = {asset_id: index for index, asset_id in enumerate(
        [asset["id"] for asset in result["assets"] if asset["loss_status"] != "no_data"], 1)}
    zones = result["zones"]
    strategies = result["strategies"]
    zone_by_id = {zone["id"]: zone for zone in zones}
    decisions = {row["zone_id"]: row for row in result["zone_decisions"]}
    live = info.get("source_kind") == "live_sentinel1_rtc"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        def json_file(name: str, value: object) -> None:
            archive.writestr(name, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))

        json_file("analysis.json", result)
        json_file("source_manifest.json", [{
            "version": "Sentinel-1 RTC" if live else "Sen1Floods11_v1.1",
            "observed_at": info["observed_at"], "published_at": None, "available_at": None,
            "license": "CC-BY-4.0" if live else "not_confirmed_by_dataset_publisher",
            "role": "model_input" if live or "S1Hand" in item["name"] else "diagnostic_label_only",
            "limitation": "No field-verified flood label for this region" if live else "Historical 512x512 chip; not a live flood observation",
            **item,
        } for item in info["source_manifest"]])
        json_file("selected_region.geojson", {"type": "FeatureCollection", "features": [{
            "type": "Feature", "geometry": as_polygon(result["region"]["bounds"]),
            "properties": {"area_km2": result["region"]["area_km2"],
                           "data_status": result["region"]["data_status"],
                           "permanent_status": result["region"]["permanent_status"]},
        }]})
        json_file("assets.geojson", {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point",
              "coordinates": [asset["longitude"], asset["latitude"]]},
             "properties": {"asset_id": asset["id"], "chip_id": scene_id,
                            "asset_class": asset["class"], "asset_value_rub": asset["value_rub"],
                            "vulnerability_coef": asset["vulnerability"],
                            "in_region": asset["id"] in region_ids}}
            for asset in info["assets"]]})
        archive.writestr("assets.csv", _csv(
            ["asset_id", "chip_id", "asset_class", "asset_value_rub", "vulnerability_coef",
             "longitude", "latitude"],
            [[asset["id"], scene_id, asset["class"], asset["value_rub"], asset["vulnerability"],
              asset["longitude"], asset["latitude"]] for asset in info["assets"]]))
        archive.writestr("asset_loss.csv", _csv(
            ["asset_id", "chip_id", "p_flood", "expected_loss_rub", "uncertainty", "rank", "in_region", "status",
             "p_window3", "permanent_water_at_point", "flood_expected_loss_rub", "ensemble_spread",
             "exposure_rub", "map_doubtful"],
            [[asset["id"], scene_id, _blank(asset["p_flood"]), _blank(asset["expected_loss_rub"]),
              _blank(asset["map_error_probability"]), region_rank.get(asset["id"], ""),
              str(asset["id"] in region_ids).lower(), asset["loss_status"], _blank(asset.get("p_window3")),
              _blank(asset.get("permanent_water_at_point")), _blank(asset["flood_expected_loss_rub"]),
              _blank(asset["uncertainty"]),
              asset["exposure_rub"], _blank(asset["map_doubtful"])]
             for asset in all_assets]))
        asset_rows = result["sensitivity"]["asset"]
        replans = asset_plan_sensitivity(ctx.space, asset_rows, inputs["budget_rub"], inputs["priority"],
                                         inputs["deadline_hours"], inputs["must_cover"])
        sensitivity_rows = []
        for row, selected in zip(asset_rows, replans):
            changed_inputs = {"asset_id": row["asset_id"], "input": row["input"],
                              "factor": row["factor"], "input_value": row["input_value"]}
            sensitivity_rows.append([
                f"asset_{row['asset_id']}_{row['input']}_{row['factor']}", "C",
                json.dumps(changed_inputs, ensure_ascii=False), selected["quote"]["total_rub"],
                selected["covered_expected_loss_rub"],
                "feasible" if selected["feasible"] else "infeasible",
                f"EL {row['expected_loss_rub']} RUB; rank {row['baseline_rank']}→{row['rank']} among region assets; fixed p-map",
                row["expected_loss_rub"], row["rank"], "|".join(selected["zone_ids"]),
                _blank(selected["covered_doubt_share"]),
            ])
        for row in result["sensitivity"]["plan"]:
            changed_inputs = {"input": row["input"], "factor": row["factor"],
                              "budget_rub": row["budget_rub"],
                              "tariff_multiplier": row["tariff_multiplier"]}
            sensitivity_rows.append([
                f"plan_{row['input']}_{row['factor']}", "C",
                json.dumps(changed_inputs, ensure_ascii=False), row["data_cost_rub"],
                row["covered_expected_loss_rub"],
                "feasible" if row["feasible"] else "infeasible",
                "Reoptimized basket for one changed input; covered EL is not avoided loss",
                "", "", "|".join(row["zone_ids"]), _blank(row["covered_doubt_share"]),
            ])
        archive.writestr("sensitivity.csv", _csv(
            ["scenario_id", "strategy", "changed_inputs_json", "decision_cost_rub",
             "covered_expected_loss_rub", "result_status", "interpretation",
             "asset_expected_loss_rub", "rank", "zone_ids", "covered_doubt_share"], sensitivity_rows))
        json_file("candidate_orders.geojson", {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": zone["geometry"],
             "properties": {**{("candidate_id" if key == "id" else key): value
                               for key, value in zone.items() if key not in {"geometry", "bounds", "name"}},
                            "c_selected": decisions[zone["id"]]["selected"],
                            "c_reason": decisions[zone["id"]]["reason"]}}
            for zone in zones]})
        archive.writestr("zone_decisions.csv", _csv(
            ["candidate_id", "kind", "area_km2", "selected_in_c", "reason", "reason_text", "price_alone_rub",
             "basket_price_with_zone_rub", "asset_ids", "expected_loss_rub", "doubt_share",
             "gain_loss_rub", "gain_doubt_share"],
            [[row["zone_id"], row["kind"], row["area_km2"], str(row["selected"]).lower(), row["reason"],
              REASONS[row["reason"]], row["price_alone_rub"], _blank(row.get("price_with_rub")),
              "|".join(row["asset_ids"]), row["expected_loss_rub"], _blank(row["doubt_share"]),
              _blank(row.get("gain_loss_rub")), _blank(row.get("gain_doubt_share"))]
             for row in result["zone_decisions"]]))
        plan_rows = []
        for strategy in ("B", "C"):
            for line in strategies[strategy]["quote"]["lines"]:
                zone = zone_by_id[line["candidate_id"]]
                plan_rows.append([
                    strategy, zone["id"], zone["area_km2"], line["base_rate_rub_km2"],
                    line["base_rate_status"], zone["processing_level"], zone["usage_type"],
                    str(zone["guaranteed_purchase"]).lower(), line["processing_coef"],
                    line["usage_coef"], line["freshness_coef"], line["discount_coef"],
                    line["discount_group_id"], line["group_area_km2"], line["unit_price_rub_km2"],
                    line["cost_rub"], line["formula"], line["legal_edition"],
                ])
        archive.writestr("procurement_plan.csv", _csv(
            ["strategy", "candidate_id", "area_km2", "base_rate_rub_km2",
             "base_rate_status", "processing_level", "usage_type", "guaranteed_purchase",
             "processing_coef", "usage_coef", "freshness_coef", "discount_coef",
             "discount_group_id", "group_area_km2", "unit_price_rub_km2", "cost_rub",
             "formula", "legal_edition"], plan_rows))
        json_file("strategy_plans.json", {key: strategies[key]["zone_ids"] for key in ("A", "B", "C")})
        archive.writestr("strategy_comparison.csv", _csv(
            ["strategy", "strategy_status", "data_cost_rub", "other_cost_rub", "other_cost_status", "decision_cost_rub",
             "budget_rub", "budget_feasible", "covered_expected_loss_rub", "coverage_share", "coverage_status",
             "covered_asset_count", "covered_doubt_share",
             "residual_uncertainty", "uncertainty_status", "uncertainty_basis"],
            [[key, _strategy_status(key, item), item["data_cost_rub"], "", "not_estimated", "",
              inputs["budget_rub"], str(item["budget_feasible"]).lower(), item["covered_expected_loss_rub"],
              _blank(item["coverage_share"]), _blank(item.get("coverage_status")),
              len(item.get("covered_asset_ids", [])), _blank(item.get("covered_doubt_share")),
              _blank(item["residual_uncertainty"]), item["uncertainty_status"],
              "mean_map_error_probability_of_region" if key == "A" else "no_new_observation"]
             for key, item in strategies.items()]))
        quality = result["quality"]
        metrics_rows = []
        for part in ("test_core", "bolivia"):
            for method in ("baseline", "model"):
                scored = quality["parts"][part][method]
                metrics_rows.append([part, "all_water", method, scored["iou"], scored["f1"],
                                     scored["precision"], scored["recall"], scored["n_pixels"]])
        for part, methods in (quality.get("flood_only") or {}).items():
            for method in ("baseline", "model"):
                scored = methods[method]
                metrics_rows.append([part, "without_jrc_permanent_water", method, scored["iou"], scored["f1"],
                                     scored["precision"], scored["recall"], scored["n_pixels"]])
        for method, key, water in (("baseline", "baseline_quality", "all_water"), ("model", "model_quality", "all_water"),
                                   ("baseline", "baseline_quality_flood", "without_jrc_permanent_water"),
                                   ("model", "model_quality_flood", "without_jrc_permanent_water")):
            scored = result["region"][key]
            if scored:
                metrics_rows.append(["selected_region", water, method, scored["iou"], scored["f1"],
                                     scored["precision"], scored["recall"], scored["valid_pixels"]])
        archive.writestr("metrics.csv", _csv(
            ["part", "water", "method", "iou", "f1", "precision", "recall", "n_pixels"], metrics_rows))
        run_signature = json.dumps({"chip": scene_id, "inputs": inputs,
                                    "region": result["region"]["bounds"]}, sort_keys=True)
        json_file("run_metadata.json", {
            "run_id": hashlib.sha256(run_signature.encode()).hexdigest()[:16],
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "chip_id": scene_id, "event_id": info["event"],
            "s1_source_files": [item["name"] for item in info["source_manifest"]
                                if live or item.get("layer") == "S1Hand" or "S1Hand" in item["name"]],
            "portfolio_seed": info["portfolio_seed"],
            "portfolio_rule": info.get("portfolio_rule", "one of each case asset type; seeded coordinates independent of p and label"),
            "model_threshold": info["model_threshold"],
            "baseline_threshold_db": info["baseline_threshold_db"],
            "probability_rule": info.get("probability_rule", PROBABILITY_RULE),
            "permanent_water": {"status": result["region"]["permanent_status"],
                                "rule": info.get("permanent_rule"), "period_note": info.get("permanent_period_note"),
                                "file": "permanent_water.tif" if info.get("permanent_rule") else None},
            "doubt_rule": DOUBT_RULE, "uncertainty_zone_rule": ZONE_RULE,
            "model": "5 member S1-only U-Net ensemble, Platt calibrated on validation",
            "model_weights": "ml/artifacts/model/model_card.json",
            "baseline": "VH threshold -23.6 dB fixed on validation",
            "repository": "https://github.com/Mojarung/kosmos_finals",
            "gitverse_commit": None,
            "gitverse_status": "pending_team_publication",
            "split": "ml/artifacts/split.json: authors' v1.1 lists; test chips touching train chips moved to test_adjacent",
            "split_part": info.get("split_part", "test_core"),
            "metrics_independent": info.get("metrics_independent", True),
            "scene_origin": info.get("origin", "demo"),
            "budget_rub": inputs["budget_rub"],
            "deadline_hours": inputs["deadline_hours"],
            "priority": inputs["priority"],
            "expected_loss_model": ("p_flood * asset_value_rub * vulnerability_coef for every estimated asset; "
                                    "flood_expected_loss_rub is 0 on JRC permanent water as an additional view"),
            "expected_loss_units": "RUB; scenario asset values and vulnerabilities",
            "covered_loss_rule": "sum of unique asset expected_loss_rub covered by selected zones",
            "coverage_share_rule": "covered / estimated EL of region assets; empty with coverage_status when the denominator is 0",
            "covered_doubt_rule": "doubtful area inside the union of selected zones / doubtful area of the analysed region",
            "other_cost_rule": ("processing runs on a CPU in seconds and analyst review or field checks are not priced, "
                                "so other_cost_rub and decision_cost_rub stay empty with other_cost_status=not_estimated; "
                                "strategies are compared by data_cost_rub"),
            "residual_uncertainty_rule": ("A: mean map error chance over valid pixels of the analysed region (share 0-1, "
                                          "status baseline); B and C: empty with status not_estimated, because no new "
                                          "image has been received"),
            "priority_rule": ("rank by expected_loss_rub; map_doubtful=true flags assets whose map error chance at the "
                              "point is >= doubt_threshold, they need a look whatever their rank; no_data assets are "
                              "listed with their exposure as unestimated risk"),
            "counterfactual_rule": "B above the budget has strategy_status=counterfactual_over_budget and is shown for comparison only",
            "unestimated_exposure_rub": result["metrics"]["unestimated_exposure_rub"],
            "uncertainty_rule": ("asset_loss.uncertainty is the map error chance at the asset pixel: 1 - p where "
                                 "flood_mask = 1, p where flood_mask = 0 (calibrated p, share 0-1); "
                                 "ensemble_spread and flood_uncertainty.tif keep the std of the five members "
                                 "as a diagnostic"),
            "candidate_rule": "1.12 km square around each asset, uncertainty rectangles from the doubt map, and the broad selected-region rectangle; min 1 km²",
            "zone_area_crs": "EPSG:6933",
            "proxy_check": result["asset_proxy_check"],
            "pricing": {"base_rate_status": "scenario", "base_rate_rub_km2": float(BASE_RATE),
                        "legal_edition": LEGAL_EDITION,
                        "legal_checked_at": "2026-09-25",
                        "legal_source_url": "https://government.ru/docs/all/160697/",
                        "legal_scope": "case_edition_verified; current procurement requires edition and provider check",
                        "base_rate_source": "case_materials_control_example",
                        "discount_group_id": strategies["C"]["quote"]["discount_group_id"],
                        "discount_group_rule": strategies["C"]["quote"]["discount_group_rule"],
                        "previous_group_area_km2": 0,
                        "availability_status": "scenario_6_hours",
                        "suitability_rule": SUITABILITY_RULE},
            "parameters": [{**row, "value": inputs["budget_rub"]} if row["name"] == "budget_rub" else row
                           for row in PARAMETERS],
            "sensitivity_method": result["sensitivity"]["method"],
            "decision_evidence": result["decision_evidence"],
            "interpretation": "Covered expected loss is observation priority, not avoided physical loss.",
            "commands": ["git lfs pull", "uv sync --project ml", "uv sync --project backend --extra demo",
                         "uv run --project ml floodvalue infer --s1 data/source/CHIP_S1Hand.tif --out data/inference/CHIP",
                         "uv run --project backend --extra demo python scripts/prepare_demo.py",
                         "uv run --project backend --extra demo python scripts/validate_package.py floodvalue-CHIP.zip"],
            "inference_manifest": info.get("inference_manifest", {"stac_item_id": info.get("source_item_id"),
                                                                  "processed_at": info.get("processed_at")}),
        })
        names = ["flood_probability.tif", "flood_mask.tif", "flood_uncertainty.tif", "baseline_mask.tif"]
        if (scene_dir(scene_id) / "permanent_water.tif").is_file():
            names.append("permanent_water.tif")
        for name in names:
            archive.write(scene_dir(scene_id) / name, arcname=name)
        archive.writestr("README.txt",
                         "FloodValue: один согласованный комплект для выбранного чипа и бюджета.\n"
                         "Вероятности и маски получены замороженной моделью по Sentinel-1.\n"
                         "permanent_water.tif (если есть): постоянная вода JRC. EL объекта на ней = p·V·q по правилу кейса,\n"
                         "а flood_expected_loss_rub = 0: вода там была и до паводка.\n"
                         "Точки и V/q учебные. Ставка и срок платной съёмки сценарные.\n"
                         "Ручная метка использована только для проверки качества.\n"
                         "zone_decisions.csv: почему каждая зона вошла или не вошла в заказ C.\n"
                         "sensitivity.csv: каждый вход меняется на ±20% по одному при фиксированной карте и портфеле.\n"
                         "Проверка перед реальным заказом: актуальная редакция ПП 840 и котировка поставщика.\n")
    output.seek(0)
    return output
