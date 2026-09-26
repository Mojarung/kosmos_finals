"""Regional observation and procurement analysis over real model outputs."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from .catalog import default_scene_id, float_map, mask, metadata, on_refresh, permanent_mask, quality, scene_catalog
from .costs import decision_costs
from .evidence import asset_proxy_check, reference_proxy_check
from .planning import (CANDIDATE_RULE, PRODUCT_RULE, BasketSpace, as_date, candidates, chosen_product, contains,
                       enrich_assets, intersects, map_error_probability, pricing_rules, rectangle_area_km2, today)
from .reliability import BIAS
from .reliability import LIMITATION as CALIBRATION_LIMITATION
from .scene_builder import current_rule
from .sensitivity import asset_sensitivity, plan_sensitivity
from .temporal import scene_timing
from .zoning import (DOUBT_EVIDENCE, DOUBT_RULE, DOUBT_THRESHOLD, ZONE_RULE, doubt_atoms, pixel_window,
                     uncertainty_zones, union_share)

LIVE = {"live_sentinel1_rtc", "uploaded_sentinel1_geotiff"}
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
    p = float(float_map(scene_id, "probability")[index])
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
    if info.get("source_kind") in LIVE:
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
    scene_id: str = ""
    window: tuple[int, int, int, int] = (0, 0, 0, 0)


def raster_assets(scene_id: str, info: dict) -> list[dict]:
    """p of each asset straight from the probability raster, so EL = p·V·q matches the GeoTIFF.

    Scene metadata keeps p rounded to 5 decimals; on a 7.5 million rouble exposure that alone moves EL by rubles.
    """
    prob = float_map(scene_id, "probability")
    width = info["width"]
    return [{**asset, "p_flood": float(prob[asset["row"] * width + asset["col"]])}
            if asset["p_flood"] is not None and "row" in asset else asset for asset in info["assets"]]


@lru_cache(maxsize=64)
def _context(scene_id: str, bounds_key: tuple[float, ...] | None) -> Context:
    info = metadata(scene_id)
    bounds = list(bounds_key) if bounds_key else None
    region = _region(scene_id, bounds_key)
    all_assets = enrich_assets(raster_assets(scene_id, info), info["model_threshold"])
    if info.get("model", {}).get("id") in {"next_optical", "context"}:
        for asset in all_assets:
            asset["p_check"] = None  # Legacy Platt reliability bins of the v1 ensemble do not describe these models.
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
        zones = candidates(all_assets, chip, broad_bounds, doubtful, info.get("observed_at"))
    masks, masses, total = doubt_atoms(scene_id, window, zones) if zones else (None, None, 0.0)
    space = BasketSpace(zones, assets, masks, masses, total)
    exposure = round(sum(asset["expected_loss_rub"] or 0 for asset in assets), 2)
    unestimated = [asset for asset in assets if asset["loss_status"] == "no_data"]
    case_exposure = round(sum(asset["case_expected_loss_rub"] or 0 for asset in assets), 2)
    return Context(info, region, assets, zones, space, exposure, unestimated, case_exposure, scene_id, window)


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


def _broad(ctx: Context) -> tuple[dict | None, int | None]:
    """The broad footprint of strategy B and its basket index."""
    broad = next((zone for zone in ctx.zones if zone["id"] == "ZALL"), None)
    return broad, ctx.space.index(["ZALL"]) if broad is not None else None


def _zone_view(zone: dict, deadline_hours: int) -> dict:
    """One footprint as the screen knows it, carrying the product the rule takes at this deadline."""
    product = chosen_product(zone, deadline_hours)
    return {**{key: value for key, value in zone.items() if key != "products"},
            **{key: value for key, value in product.items() if key != "footprint_id"},
            "timely": product["delivery_hours"] <= deadline_hours}


RESIDUAL_BASIS = ("сценарий: средний шанс ошибки карты в области после того, как своевременное новое наблюдение "
                  "уточнит карту внутри заказанных зон = A x (1 - сумма шансов внутри заказанных зон / сумма шансов "
                  "области); не измерено, нового наблюдения нет")


def _residual(ctx: Context, baseline: float | None, footprint_ids: list[str]) -> dict:
    """Residual uncertainty of a paid strategy as an explicit scenario, never an automatic reduction."""
    if baseline is None:
        return {"residual_uncertainty": None, "uncertainty_status": "not_estimated",
                "uncertainty_basis": "no map error chance in the region", "covered_chance_share": None}
    bounds = [zone["bounds"] for zone in ctx.zones if zone["id"] in set(footprint_ids)]
    share = union_share(ctx.scene_id, "chance", ctx.window, bounds) if bounds else 0.0
    if not bounds or share is None:
        return {"residual_uncertainty": baseline, "uncertainty_status": "baseline",
                "uncertainty_basis": "no timely event observation ordered", "covered_chance_share": 0.0}
    share = round(share, 4)
    return {"residual_uncertainty": round(baseline * (1 - share), 5), "uncertainty_status": "scenario",
            "uncertainty_basis": RESIDUAL_BASIS, "covered_chance_share": share}


def _baseline_uncertainty(ctx: Context) -> float | None:
    """Strategy A: the mean map error chance of the region, before any new observation."""
    return round(ctx.region["doubt_share"], 5) if ctx.region.get("doubt_share") is not None else None


def residual_for(ctx: Context, footprint_ids: list[str]) -> dict:
    """Residual uncertainty of a timely C basket by the same rule as the A/B/C table (sensitivity variants)."""
    return _residual(ctx, _baseline_uncertainty(ctx), footprint_ids)


def _strategies(ctx: Context, c: dict, budget_rub: float, deadline_hours: int, on) -> dict:
    space, exposure = ctx.space, ctx.exposure
    broad, broad_index = _broad(ctx)
    b = space.summary(broad_index, deadline_hours, strategy="B", on=on)
    # An empty B (no broad footprint outside the data) orders nothing, so nothing is late.
    b_timely = broad is None or chosen_product(broad, deadline_hours)["delivery_hours"] <= deadline_hours
    b_covered_ids = b["covered_asset_ids"] if b_timely else []
    b_covered = b["covered_expected_loss_rub"] if b_timely else 0
    b_doubt = (b["covered_doubt_share"] if b_timely else 0) if space.doubt_total else None
    no_exposure = None if exposure else "no_estimated_exposure_in_portfolio"
    baseline = _baseline_uncertainty(ctx)
    strategies = {
        "A": {"label": "Открытые данные", "zone_ids": [], "data_cost_rub": 0, "covered_asset_ids": [],
              "covered_expected_loss_rub": 0, "coverage_share": 0 if exposure else None,
              "coverage_status": no_exposure, **_case_coverage(ctx, []),
              "covered_doubt_share": 0 if space.doubt_total else None,
              "budget_feasible": True, "timely": True,
              "residual_uncertainty": baseline, "uncertainty_status": "baseline",
              "uncertainty_basis": "mean_map_error_probability_of_region"},
        "B": {"label": "Широкая съёмка", "zone_ids": b["zone_ids"], "candidate_ids": b["candidate_ids"],
              "data_cost_rub": b["quote"]["total_rub"], "quote": b["quote"],
              "covered_asset_ids": b_covered_ids, "covered_expected_loss_rub": b_covered,
              "coverage_share": _share(b_covered, exposure), "coverage_status": no_exposure,
              **_case_coverage(ctx, b_covered_ids),
              "covered_doubt_share": b_doubt,
              "budget_feasible": b["quote"]["total_rub"] <= budget_rub,
              "timely": b_timely, **_residual(ctx, baseline, b["zone_ids"] if b_timely else [])},
        "C": {"label": "Приоритетная съёмка", **c,
              "data_cost_rub": c["quote"]["total_rub"],
              "coverage_share": _share(c["covered_expected_loss_rub"], exposure), "coverage_status": no_exposure,
              **_case_coverage(ctx, c["covered_asset_ids"]),
              "budget_feasible": c["quote"]["total_rub"] <= budget_rub,
              "constraints_feasible": c["feasible"], "timely": True,
              **_residual(ctx, baseline, c["zone_ids"])},
    }
    for item in strategies.values():
        item.update(decision_costs(item["data_cost_rub"], item.get("quote", {}).get("lines", [])))
    return strategies


def _all_uncertainty(space: BasketSpace, broad: dict, deadline_hours: int) -> dict | None:
    """Buy every uncertainty zone: the doubt a broad purchase covers there, at its own price."""
    ids = [zone["id"] for zone in space.zones if zone["kind"] == "uncertainty"]
    if not ids:
        return None
    plan = space.summary(space.index(ids), deadline_hours)
    cost = plan["quote"]["total_rub"]
    return {"zone_ids": ids, "candidate_ids": plan["candidate_ids"], "data_cost_rub": cost,
            "covered_doubt_share": plan["covered_doubt_share"],
            "covered_expected_loss_rub": plan["covered_expected_loss_rub"],
            "savings_share_vs_broad": round(1 - cost / broad["data_cost_rub"], 4) if broad["data_cost_rub"] else None}


def analyze(bounds: list[float] | None, budget_rub: float, priority: str,
            deadline_hours: int, must_cover: str | None, scene_id: str | None = None,
            calculation_date: object = None) -> dict:
    """calculation_date is the pricing date (today in UTC by default): it fixes the order year and image ages."""
    scene_id = scene_id or default_scene_id()
    on = as_date(calculation_date) or today()
    ctx = context(scene_id, bounds)
    info, region, assets, space, exposure = ctx.info, ctx.region, ctx.assets, ctx.space, ctx.exposure
    c_index = space.best(budget_rub, priority, deadline_hours, must_cover)
    c = space.summary(c_index, deadline_hours, on=on)
    strategies = _strategies(ctx, c, budget_rub, deadline_hours, on)
    decisions = space.decisions(c_index, budget_rub, priority, deadline_hours, must_cover, on=on)
    b_cost = strategies["B"]["data_cost_rub"]
    c_useful = c["feasible"] and (c["covered_expected_loss_rub"] > 0 or (c["covered_doubt_share"] or 0) > 0)
    savings = round(b_cost - c["quote"]["total_rub"], 2) if c_useful and b_cost else None
    other = "information" if priority == "risk" else "risk"
    alternate = space.plan(budget_rub, other, deadline_hours, must_cover)
    pixel_area = info["area_km2"] / (info["width"] * info["height"])
    live = info.get("source_kind") in LIVE
    return {
        "chip": {key: info[key] for key in (
            "chip_id", "event", "observed_at", "bbox", "area_km2", "valid_pixels",
            "model_water_area_km2", "water_area_km2", "portfolio_seed",
            "model_threshold", "baseline_threshold_db")}
            | {key: info[key] for key in ("source_kind", "source_item_id", "source_item_ids",
                                            "source_filename", "source_sha256", "source_radiometry", "pixel_size_m_approx",
                                            "portfolio_status", "portfolio_rule", "processed_at", "split_part",
                                            "metrics_independent", "permanent_rule", "model", "evaluation_role") if key in info}
            | {key: current_rule(info[key]) for key in ("portfolio_rule", "permanent_rule") if key in info}
            | {"permanent_status": region["permanent_status"], "timing": scene_timing(info)},
        "scenes": scene_catalog()["scenes"],
        "region": region, "assets": assets,
        "zones": [_zone_view(zone, deadline_hours) for zone in ctx.zones],
        "zone_decisions": decisions,
        "catalog": space.catalog(c_index, _broad(ctx)[1], deadline_hours, decisions, on=on),
        "pricing": {**pricing_rules(), "calculation_date": on.isoformat(), "order_year": on.year,
                    "candidate_rule": CANDIDATE_RULE, "product_rule": PRODUCT_RULE},
        "sensitivity": {
            "asset": asset_sensitivity(assets),
            "plan": [{**row, **residual_for(ctx, row["zone_ids"])}
                     for row in plan_sensitivity(space, budget_rub, priority, deadline_hours, must_cover)],
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
            "meaning": "Сомнение - это калиброванный шанс, что карта ошибается; там новый снимок вероятнее всего "
                       "изменит карту. Как это влияет на ущерб, не измерено.",
        },
        "doubt": {"rule": DOUBT_RULE, "zone_rule": ZONE_RULE, "threshold": DOUBT_THRESHOLD,
                  "doubtful_area_km2": round(space.doubt_total * pixel_area, 3) if space.doubt_total else None,
                  "frontier": space.frontier(deadline_hours, must_cover),
                  "all_uncertainty_zones": _all_uncertainty(space, strategies["B"], deadline_hours),
                  "evidence": DOUBT_EVIDENCE,
                  "calibration_checked_here": not live and not info.get("model")},
        "strategies": strategies,
        "comparison": {"data_savings_vs_broad_rub": savings,
                       "coverage_gain_vs_open_rub": c["covered_expected_loss_rub"],
                       "status": "modeled_risk_coverage_not_prevented_loss"},
        "quality": quality(),
        "calibration_bias": ({"status": "not_evaluated_for_current_model", "model_id": info["model"].get("id")}
                             if info.get("model") else BIAS),
        "asset_proxy_check": reference_proxy_check() if live else asset_proxy_check(scene_id),
        "limitations": _limitations(info, region),
    }


def _limitations(info: dict, region: dict) -> list[str]:
    live = info.get("source_kind") in LIVE
    water = {
        "jrc_waterhand": "Постоянная вода JRC вычтена из карты паводка.",
        "jrc_gsw": "Постоянная вода JRC Global Surface Water (вода и в 1984, и в 2021 году) вычтена из карты паводка.",
    }.get(region["permanent_status"], "Постоянная вода в этой области не отделена: карта показывает всю воду.")
    rows = [
        water,
        "Обязательный EL = p × V × q считается для всех объектов, в том числе на постоянной воде. "
        "Ущерб только от паводка (0 на постоянной воде) идёт отдельной оценкой; корзина C использует обязательный EL.",
        "V и q объектов заданы кейсом; сами точки учебные, а не реальный реестр.",
        "Базовая ставка 788,64 ₽/км² и доступность новой съёмки (оптика через 6 часов, радар через 4 часа) сценарные.",
        "Архивный оптический кадр уточняет состояние до паводка и в покрытие текущей воды не входит.",
        "Охват ожидаемого ущерба показывает приоритет проверки, а не предотвращённый ущерб.",
    ]
    if info.get("model", {}).get("id") == "context":
        model = info["model"]
        optics = (f"с безоблачной оптикой Sentinel‑2 L2A ({model['optical_used_pixels']} пикселей)"
                  if model.get("track") == "optical" else "без оптики: чистого снимка Sentinel‑2 за 0–2 дня до радара нет")
        rows += [f"Модель с контекстом {optics}: радар того же трека за год до события, HAND, рельеф, JRC и WorldCover. "
                 "На тесте Sen1Floods11 IoU 0,689 без оптики и 0,821 с оптикой; для этой рамки ручной метки нет.",
                 "Порог сомнений сохранён от прежней модели как правило планирования; для модели с контекстом "
                 "свой порог по OOF 0,147 записан в ml/artifacts/context_model/evaluation."]
    elif info.get("model"):
        rows += ["Новая модель next_optical: исторический backtest и таблицы качества прежней S1-модели не оценивают эту версию.",
                 "Порог сомнений сохранён от прежней модели как правило планирования; точность вероятностей и этот порог для новой версии в сервисе ещё не проверены.",
                 "Оптика S2 связана с источником ручных меток. Тестовые события уже изучались; это не новая слепая проверка."]
    elif CALIBRATION_LIMITATION:
        rows.append(CALIBRATION_LIMITATION)
    if live:
        rows += ["Для нового региона нет ручной метки: локальные IoU/F1 не вычисляются.",
                 "Модель обучена на Sen1Floods11; перенос на RTC другого времени и места требует полевой проверки."]
    return rows
