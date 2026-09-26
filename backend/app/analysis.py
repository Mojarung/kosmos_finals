"""Regional observation and procurement analysis over real model outputs."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from .catalog import default_scene_id, float_map, mask, metadata, on_refresh, permanent_mask, quality, scene_catalog
from .evidence import asset_proxy_check, reference_proxy_check
from .planning import (BasketSpace, candidates, contains, enrich_assets, intersects, map_error_probability,
                       rectangle_area_km2)
from .sensitivity import asset_sensitivity, plan_sensitivity
from .zoning import DOUBT_EVIDENCE, DOUBT_RULE, DOUBT_THRESHOLD, ZONE_RULE, doubt_atoms, pixel_window, uncertainty_zones

LIVE = "live_sentinel1_rtc"
WITH_DATA = {"covered", "partial_coverage"}


def _grid(scene_id: str, name: str) -> np.ndarray:
    info = metadata(scene_id)
    return np.frombuffer(mask(scene_id, name), dtype=np.uint8).reshape(info["height"], info["width"])


def _permanent(scene_id: str) -> np.ndarray | None:
    values = permanent_mask(scene_id)
    if values is None:
        return None
    info = metadata(scene_id)
    return np.frombuffer(values, dtype=np.uint8).reshape(info["height"], info["width"]) == 1


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
    permanent = permanent_mask(scene_id)
    p = round(float_map(scene_id, "probability")[index], 4)
    return {**result, "status": "ok", "row": row, "col": col,
            "p_flood": p,
            "uncertainty": round(float_map(scene_id, "uncertainty")[index], 4),
            "map_error_probability": map_error_probability(p, info["model_threshold"]),
            "model_water": model == 1,
            "baseline_water": mask(scene_id, "baseline")[index] == 1,
            "reference_water": None if reference == 255 else reference == 1,
            "permanent_water": None if permanent is None else permanent[index] == 1,
            "reference_role": "diagnostic_only", "model_threshold": info["model_threshold"]}


def _scores(prediction: np.ndarray, truth: np.ndarray) -> dict | None:
    if not prediction.size:
        return None
    tp = int((prediction & truth).sum())
    fp = int((prediction & ~truth).sum())
    fn = int((~prediction & truth).sum())
    tn = int((~prediction & ~truth).sum())
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "iou": round(tp / (tp + fp + fn), 4) if tp + fp + fn else None,
        "f1": round(2 * tp / (2 * tp + fp + fn), 4) if 2 * tp + fp + fn else None,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "valid_pixels": tp + fp + fn + tn,
    }


def _overlap(selected: list[float], chip: list[float]) -> list[float]:
    return [max(selected[0], chip[0]), max(selected[1], chip[1]),
            min(selected[2], chip[2]), min(selected[3], chip[3])]


@lru_cache(maxsize=256)
def _region(scene_id: str, bounds_key: tuple[float, ...] | None) -> dict:
    info = metadata(scene_id)
    chip = info["bbox"]
    selected = list(bounds_key) if bounds_key else chip
    area = rectangle_area_km2(selected)
    declared = info.get("permanent_status")
    if permanent_mask(scene_id) is None:
        permanent_status = "not_separated"
    else:
        permanent_status = declared if declared not in (None, "not_separated") else "jrc_waterhand"
    common = {
        "bounds": selected, "area_km2": round(area, 3),
        "observed_area_km2": 0.0, "data_status": "outside_coverage",
        "model_water_area_km2": None, "flood_water_area_km2": None, "permanent_water_area_km2": None,
        "permanent_status": permanent_status,
        "baseline_water_area_km2": None, "reference_water_area_km2": None,
        "mean_probability": None, "mean_uncertainty": None,
        "doubt_area_km2": None, "doubt_share": None, "doubtful_area_km2": None, "doubtful_share": None,
        "model_quality": None, "baseline_quality": None,
        "model_quality_flood": None, "baseline_quality_flood": None,
        "valid_pixels": 0, "reference_pixels": 0,
    }
    if not intersects(selected, chip):
        return common
    overlap = _overlap(selected, chip)
    r0, r1, c0, c1 = pixel_window(info, overlap)
    predicted = _grid(scene_id, "model")[r0:r1, c0:c1]
    valid = predicted != 255
    valid_pixels = int(valid.sum())
    observed = rectangle_area_km2(overlap)
    if info.get("source_kind") == LIVE:
        observed *= valid_pixels / max(1, predicted.size)
    if not valid_pixels:
        return {**common, "observed_area_km2": round(observed, 3), "data_status": "no_valid_pixels"}
    width, height = info["width"], info["height"]
    pixel_area = info["area_km2"] / (width * height)
    probability = np.frombuffer(float_map(scene_id, "probability"), dtype=np.float32).reshape(height, width)[r0:r1, c0:c1]
    spread = np.frombuffer(float_map(scene_id, "uncertainty"), dtype=np.float32).reshape(height, width)[r0:r1, c0:c1]
    water = (predicted == 1) & valid
    baseline = (_grid(scene_id, "baseline")[r0:r1, c0:c1] == 1) & valid
    reference = _grid(scene_id, "water")[r0:r1, c0:c1]
    labeled = valid & (reference != 255)
    truth = reference == 1
    doubt = np.where(water, 1.0 - probability, probability)[valid].astype(np.float64).clip(0, 1)
    permanent = _permanent(scene_id)
    permanent_window = permanent[r0:r1, c0:c1] & valid if permanent is not None else None
    flood = water & ~permanent_window if permanent_window is not None else None
    result = {
        **common,
        "observed_area_km2": round(observed, 3),
        "data_status": "covered" if observed / max(area, 0.000001) >= 0.98 else "partial_coverage",
        "model_water_area_km2": round(int(water.sum()) * pixel_area, 3),
        "flood_water_area_km2": round(int(flood.sum()) * pixel_area, 3) if flood is not None else None,
        "permanent_water_area_km2": round(int((water & permanent_window).sum()) * pixel_area, 3) if permanent_window is not None else None,
        "baseline_water_area_km2": round(int(baseline.sum()) * pixel_area, 3),
        "reference_water_area_km2": round(int((truth & labeled).sum()) * pixel_area, 3) if labeled.any() else None,
        "mean_probability": round(float(probability[valid].mean()), 4),
        "mean_uncertainty": round(float(spread[valid].mean()), 4),
        "doubt_area_km2": round(float(doubt.sum()) * pixel_area, 3),
        "doubt_share": round(float(doubt.mean()), 4),
        "doubtful_area_km2": round(int((doubt >= DOUBT_THRESHOLD).sum()) * pixel_area, 3),
        "doubtful_share": round(float((doubt >= DOUBT_THRESHOLD).mean()), 4),
        "valid_pixels": valid_pixels,
        "reference_pixels": int(labeled.sum()),
        "model_water_pixels": int(water.sum()),
        "baseline_water_pixels": int(baseline.sum()),
        "reference_water_pixels": int((truth & labeled).sum()),
    }
    if labeled.any():
        result["model_quality"] = _scores(water[labeled], truth[labeled])
        result["baseline_quality"] = _scores(baseline[labeled], truth[labeled])
        if permanent_window is not None:
            outside = labeled & ~permanent_window
            result["model_quality_flood"] = _scores(water[outside], truth[outside])
            result["baseline_quality_flood"] = _scores(baseline[outside], truth[outside])
    return result


def region_summary(bounds: list[float] | None, scene_id: str | None = None) -> dict:
    return dict(_region(scene_id or default_scene_id(), tuple(bounds) if bounds else None))


@dataclass(frozen=True)
class Context:
    """Everything about a scene and region that does not depend on budget or goal."""
    info: dict
    region: dict
    assets: list[dict]
    zones: list[dict]
    space: BasketSpace
    exposure: float
    unestimated: list[dict]
    case_exposure: float


@lru_cache(maxsize=64)
def _context(scene_id: str, bounds_key: tuple[float, ...] | None) -> Context:
    info = metadata(scene_id)
    bounds = list(bounds_key) if bounds_key else None
    region = _region(scene_id, bounds_key)
    all_assets = enrich_assets(info["assets"], info["model_threshold"])
    # A/B/C is a fixed-chip experiment. The ROI changes pixel statistics and
    # doubt sampling, never its ten seeded assets or their p, V and q.
    assets = all_assets
    chip = info["bbox"]
    broad_bounds = chip
    window = pixel_window(info, chip)
    if bounds and region["data_status"] in WITH_DATA:
        overlap = _overlap(bounds, chip)
        window = pixel_window(info, overlap)
    zones: list[dict] = []
    if region["data_status"] in WITH_DATA:
        doubtful = uncertainty_zones(scene_id, window, rectangle_area_km2)
        zones = candidates(all_assets, chip, broad_bounds, doubtful)
    masks, masses, total = doubt_atoms(scene_id, window, zones) if zones else (None, None, 0.0)
    space = BasketSpace(zones, assets, masks, masses, total)
    exposure = round(sum(asset["expected_loss_rub"] or 0 for asset in assets), 2)
    unestimated = [asset for asset in assets if asset["loss_status"] == "no_data"]
    case_exposure = round(sum(asset["case_expected_loss_rub"] or 0 for asset in assets), 2)
    return Context(info, region, assets, zones, space, exposure, unestimated, case_exposure)


on_refresh(_region.cache_clear)
on_refresh(_context.cache_clear)


def context(scene_id: str, bounds: list[float] | None) -> Context:
    return _context(scene_id, tuple(bounds) if bounds else None)


def _share(value: float, total: float) -> float | None:
    return round(value / total, 4) if total else None


def _case_coverage(ctx: Context, covered_ids: list[str]) -> dict:
    """Mandatory case EL (p·V·q) of the covered assets, next to the flood-only numbers the plan optimises."""
    losses = {asset["id"]: asset["case_expected_loss_rub"] or 0 for asset in ctx.assets}
    covered = round(sum(losses[asset_id] for asset_id in covered_ids), 2)
    return {"case_covered_expected_loss_rub": covered,
            "case_coverage_share": _share(covered, ctx.case_exposure),
            "case_coverage_status": None if ctx.case_exposure else "no_estimated_exposure_in_portfolio"}


def _strategies(ctx: Context, c: dict, c_index: int | None, budget_rub: float, deadline_hours: int) -> dict:
    space, exposure = ctx.space, ctx.exposure
    zone_ids = [zone["id"] for zone in ctx.zones]
    broad_index = space.index(["ZALL"]) if "ZALL" in zone_ids else None
    b = space.summary(broad_index) if broad_index is not None else space.summary(None)
    b_timely = deadline_hours >= 6
    b_covered_ids = b["covered_asset_ids"] if b_timely else []
    b_covered = b["covered_expected_loss_rub"] if b_timely else 0
    b_doubt = (b["covered_doubt_share"] if b_timely else 0) if space.doubt_total else None
    no_exposure = None if exposure else "no_estimated_exposure_in_portfolio"
    return {
        "A": {"label": "Открытые данные", "zone_ids": [], "data_cost_rub": 0, "covered_asset_ids": [],
              "covered_expected_loss_rub": 0, "coverage_share": 0 if exposure else None,
              "coverage_status": no_exposure, **_case_coverage(ctx, []),
              "covered_doubt_share": 0 if space.doubt_total else None,
              "budget_feasible": True, "timely": True,
              "residual_uncertainty": round(ctx.region["doubt_share"], 5) if ctx.region.get("doubt_share") is not None else None,
              "uncertainty_status": "baseline"},
        "B": {"label": "Широкая съёмка", "zone_ids": [z["id"] for z in space.selected(broad_index)] if broad_index is not None else [],
              "data_cost_rub": b["quote"]["total_rub"], "quote": b["quote"],
              "covered_asset_ids": b_covered_ids, "covered_expected_loss_rub": b_covered,
              "coverage_share": _share(b_covered, exposure), "coverage_status": no_exposure,
              **_case_coverage(ctx, b_covered_ids),
              "covered_doubt_share": b_doubt,
              "budget_feasible": b["quote"]["total_rub"] <= budget_rub,
              "timely": b_timely, "residual_uncertainty": None,
              "uncertainty_status": "not_estimated"},
        "C": {"label": "Приоритетная съёмка", **c,
              "data_cost_rub": c["quote"]["total_rub"],
              "coverage_share": _share(c["covered_expected_loss_rub"], exposure), "coverage_status": no_exposure,
              **_case_coverage(ctx, c["covered_asset_ids"]),
              "budget_feasible": c["quote"]["total_rub"] <= budget_rub,
              "constraints_feasible": c["feasible"], "timely": True,
              "residual_uncertainty": None, "uncertainty_status": "not_estimated"},
    }


def _all_uncertainty(space: BasketSpace, broad: dict) -> dict | None:
    """Buy every uncertainty zone: the doubt a broad purchase covers there, at its own price."""
    ids = [zone["id"] for zone in space.zones if zone["kind"] == "uncertainty"]
    if not ids:
        return None
    plan = space.summary(space.index(ids))
    cost = plan["quote"]["total_rub"]
    return {"zone_ids": ids, "data_cost_rub": cost, "covered_doubt_share": plan["covered_doubt_share"],
            "covered_expected_loss_rub": plan["covered_expected_loss_rub"],
            "savings_share_vs_broad": round(1 - cost / broad["data_cost_rub"], 4) if broad["data_cost_rub"] else None}


def analyze(bounds: list[float] | None, budget_rub: float, priority: str,
            deadline_hours: int, must_cover: str | None, scene_id: str | None = None) -> dict:
    scene_id = scene_id or default_scene_id()
    ctx = context(scene_id, bounds)
    info, region, assets, space, exposure = ctx.info, ctx.region, ctx.assets, ctx.space, ctx.exposure
    c_index = space.best(budget_rub, priority, deadline_hours, must_cover)
    c = space.summary(c_index)
    strategies = _strategies(ctx, c, c_index, budget_rub, deadline_hours)
    b_cost = strategies["B"]["data_cost_rub"]
    c_useful = c["feasible"] and (c["covered_expected_loss_rub"] > 0 or (c["covered_doubt_share"] or 0) > 0)
    savings = round(b_cost - c["quote"]["total_rub"], 2) if c_useful and b_cost else None
    other = "information" if priority == "risk" else "risk"
    alternate = space.plan(budget_rub, other, deadline_hours, must_cover)
    pixel_area = info["area_km2"] / (info["width"] * info["height"])
    live = info.get("source_kind") == LIVE
    return {
        "chip": {key: info[key] for key in (
            "chip_id", "event", "observed_at", "bbox", "area_km2", "valid_pixels",
            "model_water_area_km2", "water_area_km2", "portfolio_seed",
            "model_threshold", "baseline_threshold_db")}
            | {key: info[key] for key in ("source_kind", "source_item_id", "source_item_ids", "pixel_size_m_approx",
                                            "portfolio_status", "portfolio_rule", "processed_at", "split_part",
                                            "metrics_independent", "permanent_rule") if key in info}
            | {"permanent_status": region["permanent_status"]},
        "scenes": scene_catalog()["scenes"],
        "region": region, "assets": assets, "zones": ctx.zones,
        "zone_decisions": space.decisions(c_index, budget_rub, priority, deadline_hours, must_cover),
        "sensitivity": {
            "asset": asset_sensitivity(assets),
            "plan": plan_sensitivity(space, budget_rub, priority, deadline_hours, must_cover),
            "method": "one_input_at_a_time_20_percent_fixed_probability_map_and_portfolio",
        },
        "inputs": {"budget_rub": budget_rub, "priority": priority, "bbox": bounds,
                   "deadline_hours": deadline_hours, "must_cover": must_cover},
        "metrics": {"portfolio_count": len(assets), "expected_loss_rub": exposure,
                    "case_expected_loss_rub": ctx.case_exposure,
                    "high_probability_assets": sum((a["p_flood"] or 0) >= info["model_threshold"] for a in assets),
                    "permanent_water_assets": sum(a["flood_loss_status"] == "permanent_water" for a in assets),
                    "assets_in_region": sum(contains(region["bounds"], a) for a in assets),
                    "region_expected_loss_rub": round(sum(a["expected_loss_rub"] or 0 for a in assets
                                                          if contains(region["bounds"], a)), 2),
                    "flood_expected_loss_rub": round(sum(a["flood_expected_loss_rub"] or 0 for a in assets), 2),
                    "unestimated_assets": len(ctx.unestimated),
                    "unestimated_exposure_rub": round(sum(a["exposure_rub"] for a in ctx.unestimated), 2),
                    "model_water_area_km2": region["model_water_area_km2"],
                    "flood_water_area_km2": region["flood_water_area_km2"],
                    "doubt_area_km2": region["doubt_area_km2"],
                    "doubtful_area_km2": region["doubtful_area_km2"],
                    "prioritized_assets": len(c["covered_asset_ids"]),
                    "data_savings_vs_broad_rub": savings,
                    "savings_share_vs_broad": round(savings / b_cost, 4) if savings is not None and b_cost else None,
                    "c_price_share_of_broad": round(c["quote"]["total_rub"] / b_cost, 4) if c_useful and b_cost else None,
                    "cost_per_million_covered_rub": round(c["quote"]["total_rub"] / (c["covered_expected_loss_rub"] / 1_000_000), 2) if c["covered_expected_loss_rub"] else None,
                    "status": "measured_model_probability_scenario_assets_and_tariff"},
        "decision_evidence": {
            "selected_priority": priority,
            "alternate_priority": other,
            "selected_zone_ids": c["zone_ids"],
            "alternate_zone_ids": alternate["zone_ids"],
            "priority_changes_order": set(c["zone_ids"]) != set(alternate["zone_ids"]),
            "alternate_covered_expected_loss_rub": alternate["covered_expected_loss_rub"],
            "alternate_covered_doubt_share": alternate["covered_doubt_share"],
            "alternate_cost_rub": alternate["quote"]["total_rub"],
            "meaning": "Doubt is the calibrated chance that the map is wrong; covering it is where a new image "
                       "is most likely to change the map. Its effect on loss is not measured.",
        },
        "doubt": {"rule": DOUBT_RULE, "zone_rule": ZONE_RULE, "threshold": DOUBT_THRESHOLD,
                  "doubtful_area_km2": round(space.doubt_total * pixel_area, 3) if space.doubt_total else None,
                  "frontier": space.frontier(deadline_hours, must_cover),
                  "all_uncertainty_zones": _all_uncertainty(space, strategies["B"]),
                  "evidence": DOUBT_EVIDENCE,
                  "calibration_checked_here": not live},
        "strategies": strategies,
        "comparison": {"data_savings_vs_broad_rub": savings,
                       "coverage_gain_vs_open_rub": c["covered_expected_loss_rub"],
                       "status": "modeled_risk_coverage_not_prevented_loss"},
        "quality": quality(),
        "asset_proxy_check": reference_proxy_check() if live else asset_proxy_check(scene_id),
        "limitations": _limitations(info, region),
    }


def _limitations(info: dict, region: dict) -> list[str]:
    live = info.get("source_kind") == LIVE
    water = {
        "jrc_waterhand": "Постоянная вода JRC вычтена из карты паводка.",
        "jrc_gsw": "Постоянная вода JRC Global Surface Water (вода и в 1984, и в 2021 году) вычтена из карты паводка.",
    }.get(region["permanent_status"], "Постоянная вода в этой области не отделена: карта показывает всю воду.")
    rows = [
        water,
        "Обязательный EL = p × V × q считается для всех объектов, в том числе на постоянной воде. "
        "Ущерб только от паводка (0 на постоянной воде) идёт отдельной оценкой; корзина C использует обязательный EL.",
        "V и q объектов заданы кейсом; сами точки учебные, а не реальный реестр.",
        "Базовая ставка 788,64 ₽/км² и доступность новой съёмки через 6 часов сценарные.",
        "Охват ожидаемого ущерба показывает приоритет проверки, а не предотвращённый ущерб.",
    ]
    if live:
        rows += ["Для нового региона нет ручной метки: локальные IoU/F1 не вычисляются.",
                 "Модель обучена на Sen1Floods11; перенос на RTC другого времени и места требует полевой проверки."]
    return rows
