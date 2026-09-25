"""Build a single, internally consistent case package for the selected chip."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile

from .catalog import DATA_DIR, metadata
from .planning import as_polygon, enrich_assets, plan
from .sensitivity import asset_sensitivity


def _csv(headers: list[str], rows: list[list[object]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return output.getvalue()


def package(result: dict) -> io.BytesIO:
    scene_id = result["chip"]["chip_id"]
    info = metadata(scene_id)
    assets = enrich_assets(info["assets"])
    selected_ids = {asset["id"] for asset in result["assets"]}
    zones = result["zones"]
    strategies = result["strategies"]
    zone_by_id = {zone["id"]: zone for zone in zones}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        def json_file(name: str, value: object) -> None:
            archive.writestr(name, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))

        json_file("analysis.json", result)
        json_file("source_manifest.json", [{
            **item, "version": "Sen1Floods11_v1.1", "observed_at": info["observed_at"],
            "published_at": None, "available_at": None,
            "license": "not_confirmed_by_dataset_publisher",
            "role": "model_input" if "S1Hand" in item["name"] else "diagnostic_label_only",
            "limitation": "Historical 512x512 chip; not a live flood observation",
        } for item in info["source_manifest"]])
        json_file("selected_region.geojson", {"type": "FeatureCollection", "features": [{
            "type": "Feature", "geometry": as_polygon(result["region"]["bounds"]),
            "properties": {"area_km2": result["region"]["area_km2"],
                           "data_status": result["region"]["data_status"]},
        }]})
        json_file("assets.geojson", {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point",
              "coordinates": [asset["longitude"], asset["latitude"]]},
             "properties": {"asset_id": asset["id"], "chip_id": scene_id,
                            "asset_class": asset["class"], "asset_value_rub": asset["value_rub"],
                            "vulnerability_coef": asset["vulnerability"]}}
            for asset in info["assets"]]})
        archive.writestr("assets.csv", _csv(
            ["asset_id", "chip_id", "asset_class", "asset_value_rub", "vulnerability_coef",
             "longitude", "latitude"],
            [[asset["id"], scene_id, asset["class"], asset["value_rub"], asset["vulnerability"],
              asset["longitude"], asset["latitude"]] for asset in info["assets"]]))
        archive.writestr("asset_loss.csv", _csv(
            ["asset_id", "chip_id", "p_flood", "expected_loss_rub", "uncertainty", "rank", "in_region", "status"],
            [[asset["id"], scene_id, asset["p_flood"], asset["expected_loss_rub"],
              asset["uncertainty"], rank if asset["p_flood"] is not None else "",
              str(asset["id"] in selected_ids).lower(),
              "ok" if asset["p_flood"] is not None else "no_data"]
             for rank, asset in enumerate(assets, 1)]))
        sensitivity_rows = []
        for row in asset_sensitivity(assets):
            changed = []
            for asset in result["assets"]:
                if asset["id"] == row["asset_id"]:
                    changed.append({**asset, "expected_loss_rub": row["expected_loss_rub"]})
                else:
                    changed.append(asset)
            selected = plan(changed, zones, result["inputs"]["budget_rub"],
                            result["inputs"]["priority"], result["inputs"]["deadline_hours"],
                            result["inputs"]["must_cover"])
            changed_inputs = {"asset_id": row["asset_id"], "input": row["input"],
                              "factor": row["factor"], "input_value": row["input_value"]}
            sensitivity_rows.append([
                f"asset_{row['asset_id']}_{row['input']}_{row['factor']}", "C",
                json.dumps(changed_inputs, ensure_ascii=False), selected["quote"]["total_rub"],
                selected["covered_expected_loss_rub"],
                "feasible" if selected["feasible"] else "infeasible",
                f"EL {row['expected_loss_rub']} RUB; rank {row['baseline_rank']}→{row['rank']}; fixed p-map and portfolio",
                row["expected_loss_rub"], row["rank"], "|".join(selected["zone_ids"]),
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
                "", "", "|".join(row["zone_ids"]),
            ])
        archive.writestr("sensitivity.csv", _csv(
            ["scenario_id", "strategy", "changed_inputs_json", "decision_cost_rub",
             "covered_expected_loss_rub", "result_status", "interpretation",
             "asset_expected_loss_rub", "rank", "zone_ids"], sensitivity_rows))
        json_file("candidate_orders.geojson", {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": zone["geometry"],
             "properties": {("candidate_id" if key == "id" else key): value
                            for key, value in zone.items() if key not in {"geometry", "bounds", "name"}}}
            for zone in zones]})
        plan_rows = []
        for strategy in ("B", "C"):
            for line in strategies[strategy]["quote"]["lines"]:
                zone = zone_by_id[line["candidate_id"]]
                plan_rows.append([
                    strategy, zone["id"], zone["area_km2"], line["base_rate_rub_km2"],
                    line["base_rate_status"], zone["processing_level"], zone["usage_type"],
                    str(zone["guaranteed_purchase"]).lower(), line["processing_coef"],
                    line["usage_coef"], line["freshness_coef"], line["discount_coef"],
                    "optical_1m_2026", line["group_area_km2"], line["unit_price_rub_km2"],
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
            ["strategy", "data_cost_rub", "other_cost_rub", "decision_cost_rub",
             "budget_rub", "budget_feasible", "covered_expected_loss_rub", "coverage_share",
             "residual_uncertainty", "uncertainty_status", "uncertainty_basis"],
            [[key, item["data_cost_rub"], 0, item["data_cost_rub"], result["inputs"]["budget_rub"],
              str(item["budget_feasible"]).lower(), item["covered_expected_loss_rub"],
              item["coverage_share"], item["residual_uncertainty"], item["uncertainty_status"],
              "weighted_member_std_by_expected_loss" if key == "A" else "no_new_observation"]
             for key, item in strategies.items()]))
        quality = result["quality"]
        metrics_rows = []
        for part in ("test_core", "bolivia"):
            for method in ("baseline", "model"):
                scored = quality["parts"][part][method]
                metrics_rows.append([part, method, scored["iou"], scored["f1"],
                                     scored["precision"], scored["recall"], scored["n_pixels"]])
        for method, key in (("baseline", "baseline_quality"), ("model", "model_quality")):
            scored = result["region"][key]
            if scored:
                metrics_rows.append(["selected_region", method, scored["iou"], scored["f1"],
                                     scored["precision"], scored["recall"], scored["valid_pixels"]])
        archive.writestr("metrics.csv", _csv(
            ["part", "method", "iou", "f1", "precision", "recall", "n_pixels"], metrics_rows))
        run_signature = json.dumps({"chip": scene_id, "inputs": result["inputs"],
                                    "region": result["region"]["bounds"]}, sort_keys=True)
        json_file("run_metadata.json", {
            "run_id": hashlib.sha256(run_signature.encode()).hexdigest()[:16],
            "chip_id": scene_id, "event_id": info["event"],
            "s1_source_files": [item["name"] for item in info["source_manifest"] if "S1Hand" in item["name"]],
            "portfolio_seed": info["portfolio_seed"],
            "portfolio_rule": "one of each case asset type; seeded coordinates independent of p and label",
            "model_threshold": info["model_threshold"],
            "baseline_threshold_db": info["baseline_threshold_db"],
            "probability_rule": "mean of valid 3x3 flood_probability pixels around asset point",
            "model": "5 member S1-only U-Net ensemble, Platt calibrated on validation",
            "model_weights": "ml/artifacts/model/model_card.json",
            "baseline": "VH threshold -23.6 dB fixed on validation",
            "repository": "https://github.com/Mojarung/kosmos_finals",
            "gitverse_commit": None,
            "gitverse_status": "pending_team_publication",
            "split": "test_core, independent of train adjacency",
            "budget_rub": result["inputs"]["budget_rub"],
            "deadline_hours": result["inputs"]["deadline_hours"],
            "expected_loss_model": "p_flood * asset_value_rub * vulnerability_coef",
            "expected_loss_units": "RUB; scenario asset values and vulnerabilities",
            "covered_loss_rule": "sum of unique asset expected_loss_rub covered by selected zones",
            "uncertainty_rule": "ensemble member standard deviation; information priority heuristic",
            "candidate_rule": "1.12 km square around each asset plus broad selected-region rectangle; min 1 km²",
            "zone_area_crs": "EPSG:6933",
            "proxy_check": result["asset_proxy_check"],
            "pricing": {"base_rate_status": "scenario", "base_rate_rub_km2": 788.64,
                        "legal_edition": "PP_RF_840_2025-08-27",
                        "legal_checked_at": "2026-09-25",
                        "legal_source_url": "https://government.ru/docs/all/160697/",
                        "legal_scope": "case_edition_verified; current procurement requires edition and provider check",
                        "base_rate_source": "case_materials_control_example",
                        "availability_status": "scenario_6_hours"},
            "sensitivity_method": result["sensitivity"]["method"],
            "decision_evidence": result["decision_evidence"],
            "interpretation": "Covered expected loss is observation priority, not avoided physical loss.",
            "commands": ["git lfs pull", "uv sync --project ml",
                         "ml/.venv/bin/floodvalue infer --s1 data/source/CHIP_S1Hand.tif --out data/inference/CHIP",
                         "backend/.venv/bin/python scripts/prepare_demo.py",
                         "backend/.venv/bin/python scripts/validate_package.py floodvalue-CHIP.zip"],
            "inference_manifest": info["inference_manifest"],
        })
        for name in ("flood_probability.tif", "flood_mask.tif", "flood_uncertainty.tif", "baseline_mask.tif"):
            archive.write(DATA_DIR / scene_id / name, arcname=name)
        archive.writestr("README.txt",
                         "FloodValue — один согласованный комплект для выбранного чипа и бюджета.\n"
                         "Вероятности и маски получены замороженной моделью по Sentinel-1.\n"
                         "Точки и V/q учебные. Ставка и срок платной съемки сценарные.\n"
                         "Ручная метка использована только для проверки качества.\n"
                         "sensitivity.csv: каждый вход меняется на ±20% по одному при фиксированной карте и портфеле.\n"
                         "Проверка перед реальным заказом: актуальная редакция ПП 840 и котировка поставщика.\n")
    output.seek(0)
    return output
