"""Regional observation and procurement analysis over real model outputs."""

from __future__ import annotations

from math import ceil, floor

from .catalog import default_scene_id, float_map, mask, metadata, quality, scene_catalog
from .planning import candidates, contains, enrich_assets, intersects, plan, quote, rectangle_area_km2, square_around
from .sensitivity import asset_sensitivity, plan_sensitivity
from .evidence import asset_proxy_check


def inspect_pixel(longitude: float, latitude: float, scene_id: str | None = None) -> dict:
    """Read the same frozen grids shown on the map at a clicked coordinate."""
    scene_id = scene_id or default_scene_id()
    info = metadata(scene_id)
    west, south, east, north = info["bbox"]
    result = {"chip_id": scene_id, "longitude": longitude, "latitude": latitude,
              "status": "outside_coverage"}
    if not (west <= longitude < east and south < latitude <= north):
        return result
    col = min(info["width"] - 1, int((longitude - west) / (east - west) * info["width"]))
    row = min(info["height"] - 1, int((north - latitude) / (north - south) * info["height"]))
    index = row * info["width"] + col
    model = mask(scene_id, "model")[index]
    if model == 255:
        return {**result, "status": "no_valid_pixel", "row": row, "col": col}
    reference = mask(scene_id, "water")[index]
    return {**result, "status": "ok", "row": row, "col": col,
            "p_flood": round(float_map(scene_id, "probability")[index], 4),
            "uncertainty": round(float_map(scene_id, "uncertainty")[index], 4),
            "model_water": model == 1,
            "baseline_water": mask(scene_id, "baseline")[index] == 1,
            "reference_water": None if reference == 255 else reference == 1,
            "reference_role": "diagnostic_only", "model_threshold": info["model_threshold"]}


def _scores(tp: int, fp: int, fn: int, tn: int) -> dict:
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "iou": round(tp / (tp + fp + fn), 4) if tp + fp + fn else None,
        "f1": round(2 * tp / (2 * tp + fp + fn), 4) if 2 * tp + fp + fn else None,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "valid_pixels": tp + fp + fn + tn,
    }


def region_summary(bounds: list[float] | None, scene_id: str | None = None) -> dict:
    scene_id = scene_id or default_scene_id()
    info = metadata(scene_id)
    chip = info["bbox"]
    selected = bounds or chip
    area = rectangle_area_km2(selected)
    common = {
        "bounds": selected, "area_km2": round(area, 3),
        "observed_area_km2": 0.0, "data_status": "outside_coverage",
        "model_water_area_km2": None, "baseline_water_area_km2": None,
        "reference_water_area_km2": None, "mean_probability": None,
        "mean_uncertainty": None, "uncertain_share": None,
        "model_quality": None, "baseline_quality": None,
        "valid_pixels": 0, "reference_pixels": 0,
    }
    if not intersects(selected, chip):
        return common
    overlap = [max(selected[0], chip[0]), max(selected[1], chip[1]),
               min(selected[2], chip[2]), min(selected[3], chip[3])]
    w, s, e, n = chip
    width, height = info["width"], info["height"]
    col0 = max(0, floor((overlap[0] - w) / (e - w) * width))
    col1 = min(width, ceil((overlap[2] - w) / (e - w) * width))
    row0 = max(0, floor((n - overlap[3]) / (n - s) * height))
    row1 = min(height, ceil((n - overlap[1]) / (n - s) * height))
    predicted = mask(scene_id, "model")
    baseline = mask(scene_id, "baseline")
    reference = mask(scene_id, "water")
    probabilities = float_map(scene_id, "probability")
    uncertainty = float_map(scene_id, "uncertainty")
    model_pixels = baseline_pixels = ref_pixels = valid_pixels = uncertain_pixels = 0
    sum_p = sum_u = 0.0
    main_tp = main_fp = main_fn = main_tn = 0
    base_tp = base_fp = base_fn = base_tn = 0
    for row in range(row0, row1):
        for col in range(col0, col1):
            i = row * width + col
            if predicted[i] == 255:
                continue
            valid_pixels += 1
            p = probabilities[i]
            sum_p += p
            sum_u += uncertainty[i]
            uncertain_pixels += 0.3 <= p <= 0.7
            model_pixels += predicted[i] == 1
            baseline_pixels += baseline[i] == 1
            if reference[i] == 255:
                continue
            ref_pixels += reference[i] == 1
            truth = reference[i] == 1
            main = predicted[i] == 1
            base = baseline[i] == 1
            main_tp += main and truth
            main_fp += main and not truth
            main_fn += not main and truth
            main_tn += not main and not truth
            base_tp += base and truth
            base_fp += base and not truth
            base_fn += not base and truth
            base_tn += not base and not truth
    pixel_area = info["area_km2"] / (width * height)
    observed = rectangle_area_km2(overlap)
    return {
        **common,
        "observed_area_km2": round(observed, 3),
        "data_status": ("covered" if abs(observed - area) < 0.001 else "partial_coverage") if valid_pixels else "no_valid_pixels",
        "model_water_area_km2": round(model_pixels * pixel_area, 3),
        "baseline_water_area_km2": round(baseline_pixels * pixel_area, 3),
        "reference_water_area_km2": round(ref_pixels * pixel_area, 3) if main_tp + main_fp + main_fn + main_tn else None,
        "mean_probability": round(sum_p / valid_pixels, 4) if valid_pixels else None,
        "mean_uncertainty": round(sum_u / valid_pixels, 4) if valid_pixels else None,
        "uncertain_share": round(uncertain_pixels / valid_pixels, 4) if valid_pixels else None,
        "model_quality": _scores(main_tp, main_fp, main_fn, main_tn) if main_tp + main_fp + main_fn + main_tn else None,
        "baseline_quality": _scores(base_tp, base_fp, base_fn, base_tn) if base_tp + base_fp + base_fn + base_tn else None,
        "valid_pixels": valid_pixels,
        "reference_pixels": main_tp + main_fp + main_fn + main_tn,
        "model_water_pixels": model_pixels,
        "baseline_water_pixels": baseline_pixels,
        "reference_water_pixels": ref_pixels,
    }


def analyze(bounds: list[float] | None, budget_rub: float, priority: str,
            deadline_hours: int, must_cover: str | None, scene_id: str | None = None) -> dict:
    scene_id = scene_id or default_scene_id()
    info = metadata(scene_id)
    region = region_summary(bounds, scene_id)
    all_assets = enrich_assets(info["assets"])
    assets = [asset for asset in all_assets if bounds is None or contains(bounds, asset)]
    broad_bounds = info["bbox"]
    if bounds and region["data_status"] in {"covered", "partial_coverage"}:
        chip = info["bbox"]
        overlap = [max(bounds[0], chip[0]), max(bounds[1], chip[1]),
                   min(bounds[2], chip[2]), min(bounds[3], chip[3])]
        broad_bounds = overlap if rectangle_area_km2(overlap) >= 1 else square_around(
            {"longitude": (overlap[0] + overlap[2]) / 2,
             "latitude": (overlap[1] + overlap[3]) / 2}, chip)
    all_zones = candidates(all_assets, info["bbox"], broad_bounds)
    zones = [zone for zone in all_zones if zone["id"] == "ZALL" or
             any(contains(zone["bounds"], asset) for asset in assets)] if assets else []
    c = plan(assets, zones, budget_rub, priority, deadline_hours, must_cover)
    broad = [zone for zone in zones if zone["id"] == "ZALL"] if assets else []
    b_quote = quote(broad)
    exposure = round(sum(asset["expected_loss_rub"] or 0 for asset in assets), 2)
    b_covered = exposure if broad and deadline_hours >= 6 else 0
    initial_uncertainty = (sum((asset["uncertainty"] or 0) * (asset["expected_loss_rub"] or 0)
                               for asset in assets) / exposure) if exposure else None
    strategies = {
        "A": {"label": "Открытые данные", "zone_ids": [], "data_cost_rub": 0,
              "covered_expected_loss_rub": 0, "coverage_share": 0,
              "budget_feasible": True, "timely": True,
              "residual_uncertainty": round(initial_uncertainty, 5) if initial_uncertainty is not None else None,
              "uncertainty_status": "baseline"},
        "B": {"label": "Широкая съёмка", "zone_ids": [z["id"] for z in broad],
              "data_cost_rub": b_quote["total_rub"], "quote": b_quote,
              "covered_expected_loss_rub": b_covered,
              "coverage_share": round(b_covered / exposure, 4) if exposure else 0,
              "budget_feasible": b_quote["total_rub"] <= budget_rub,
              "timely": deadline_hours >= 6, "residual_uncertainty": None,
              "uncertainty_status": "not_estimated"},
        "C": {"label": "Приоритетная съёмка", **c,
              "data_cost_rub": c["quote"]["total_rub"],
              "coverage_share": round(c["covered_expected_loss_rub"] / exposure, 4) if exposure else 0,
              "budget_feasible": c["quote"]["total_rub"] <= budget_rub,
              "constraints_feasible": c["feasible"], "timely": True,
              "residual_uncertainty": None, "uncertainty_status": "not_estimated"},
    }
    selected_count = len(strategies["C"]["covered_asset_ids"])
    savings = round(b_quote["total_rub"] - c["quote"]["total_rub"], 2) if c["feasible"] and c["covered_expected_loss_rub"] > 0 else None
    sensitivity = {
        "asset": asset_sensitivity(assets),
        "plan": plan_sensitivity(assets, zones, budget_rub, priority, deadline_hours, must_cover),
        "method": "one_input_at_a_time_20_percent_fixed_probability_map_and_portfolio",
    }
    alternate = plan(assets, zones, budget_rub,
                     "information" if priority == "risk" else "risk", deadline_hours, must_cover)
    return {
        "chip": {key: info[key] for key in (
            "chip_id", "event", "observed_at", "bbox", "area_km2", "valid_pixels",
            "model_water_area_km2", "water_area_km2", "portfolio_seed",
            "model_threshold", "baseline_threshold_db")},
        "scenes": scene_catalog()["scenes"],
        "region": region, "assets": assets, "zones": zones,
        "sensitivity": sensitivity,
        "inputs": {"budget_rub": budget_rub, "priority": priority,
                   "deadline_hours": deadline_hours, "must_cover": must_cover},
        "metrics": {"portfolio_count": len(assets), "expected_loss_rub": exposure,
                    "high_probability_assets": sum((a["p_flood"] or 0) >= info["model_threshold"] for a in assets),
                    "model_water_area_km2": region["model_water_area_km2"],
                    "prioritized_assets": selected_count,
                    "data_savings_vs_broad_rub": savings,
                    "savings_share_vs_broad": round(savings / b_quote["total_rub"], 4) if savings is not None and b_quote["total_rub"] else None,
                    "cost_per_million_covered_rub": round(c["quote"]["total_rub"] / (c["covered_expected_loss_rub"] / 1_000_000), 2) if c["covered_expected_loss_rub"] else None,
                    "status": "measured_model_probability_scenario_assets_and_tariff"},
        "decision_evidence": {
            "selected_priority": priority,
            "alternate_priority": "information" if priority == "risk" else "risk",
            "selected_zone_ids": c["zone_ids"],
            "alternate_zone_ids": alternate["zone_ids"],
            "priority_changes_order": set(c["zone_ids"]) != set(alternate["zone_ids"]),
            "alternate_covered_expected_loss_rub": alternate["covered_expected_loss_rub"],
            "meaning": "Information priority is a heuristic based on ensemble spread, not measured value of a new image.",
        },
        "strategies": strategies,
        "comparison": {"data_savings_vs_broad_rub": savings,
                       "coverage_gain_vs_open_rub": c["covered_expected_loss_rub"],
                       "status": "modeled_risk_coverage_not_prevented_loss"},
        "quality": quality(),
        "asset_proxy_check": asset_proxy_check(scene_id),
        "limitations": [
            "Модель измеряет воду, но не отделяет постоянную воду от паводковой.",
            "V и q объектов заданы кейсом; сами точки учебные, а не реальный реестр.",
            "Базовая ставка 788,64 ₽/км² и доступность новой съёмки через 6 часов сценарные.",
            "Охват ожидаемого ущерба — приоритет проверки, а не предотвращённый ущерб.",
        ],
    }
