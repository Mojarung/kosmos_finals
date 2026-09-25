"""Region lens and A/B/C comparison for the demo chip."""

from __future__ import annotations

from math import ceil, floor

from .catalog import metadata, water_mask
from .planning import candidates, contains, enrich_assets, intersects, plan, quote, rectangle_area_km2


def region_summary(bounds: list[float] | None) -> dict:
    info = metadata()
    chip = info["bbox"]
    selected = bounds or chip
    area = rectangle_area_km2(selected)
    overlap = [max(selected[0], chip[0]), max(selected[1], chip[1]),
               min(selected[2], chip[2]), min(selected[3], chip[3])]
    if not intersects(selected, chip):
        return {"bounds": selected, "area_km2": round(area, 3), "observed_area_km2": 0,
                "water_area_km2": None, "water_share": None, "valid_pixels": 0,
                "water_pixels": 0, "data_status": "outside_demo_coverage"}
    w, s, e, n = chip
    width, height = info["width"], info["height"]
    col0 = max(0, floor((overlap[0] - w) / (e - w) * width))
    col1 = min(width, ceil((overlap[2] - w) / (e - w) * width))
    row0 = max(0, floor((n - overlap[3]) / (n - s) * height))
    row1 = min(height, ceil((n - overlap[1]) / (n - s) * height))
    mask = water_mask()
    water = valid = 0
    for row in range(row0, row1):
        line = mask[row * width + col0: row * width + col1]
        water += line.count(1)
        valid += line.count(1) + line.count(0)
    pixel_area = info["area_km2"] / (width * height)
    return {"bounds": selected, "area_km2": round(area, 3),
            "observed_area_km2": round(rectangle_area_km2(overlap), 3),
            "water_area_km2": round(water * pixel_area, 3),
            "water_share": round(water / valid, 4) if valid else None,
            "valid_pixels": valid, "water_pixels": water,
            "data_status": "covered" if valid else "no_valid_pixels"}


def analyze(bounds: list[float] | None, budget_rub: float, priority: str,
            deadline_hours: int, must_cover: str | None) -> dict:
    info = metadata()
    region = region_summary(bounds)
    all_assets = enrich_assets(info["assets"])
    assets = [asset for asset in all_assets if bounds is None or contains(bounds, asset)]
    broad_bounds = info["bbox"]
    if bounds and region["data_status"] == "covered":
        chip = info["bbox"]
        overlap = [max(bounds[0], chip[0]), max(bounds[1], chip[1]),
                   min(bounds[2], chip[2]), min(bounds[3], chip[3])]
        if rectangle_area_km2(overlap) >= 1:
            broad_bounds = overlap
        else:
            center = {"longitude": (overlap[0] + overlap[2]) / 2,
                      "latitude": (overlap[1] + overlap[3]) / 2}
            from .planning import square_around
            broad_bounds = square_around(center, chip)
    all_zones = candidates(all_assets, info["bbox"], broad_bounds)
    zones = [zone for zone in all_zones if zone["id"] == "ZALL" or
             any(contains(zone["bounds"], asset) for asset in assets)] if assets else []
    c = plan(assets, zones, budget_rub, priority, deadline_hours, must_cover)
    broad = [zone for zone in zones if zone["id"] == "ZALL"] if assets else []
    b_quote = quote(broad)
    exposure = round(sum(asset["expected_loss_rub"] for asset in assets), 2)
    b_covered = exposure if broad and deadline_hours >= 6 else 0
    return {
        "chip": {key: info[key] for key in ["chip_id", "event", "observed_at", "bbox", "area_km2", "valid_pixels", "water_pixels", "water_area_km2", "portfolio_seed"]},
        "region": region, "assets": assets, "zones": zones,
        "inputs": {"budget_rub": budget_rub, "priority": priority,
                   "deadline_hours": deadline_hours, "must_cover": must_cover},
        "metrics": {"portfolio_count": len(assets), "scenario_exposure_rub": exposure,
                    "observed_water_area_km2": region["water_area_km2"],
                    "planning_status": "scenario_without_ml"},
        "strategies": {
            "A": {"label": "Открытые данные", "zone_ids": [], "data_cost_rub": 0,
                  "covered_expected_loss_rub": 0, "coverage_share": 0,
                  "budget_feasible": True, "timely": True},
            "B": {"label": "Широкая съёмка", "zone_ids": [z["id"] for z in broad],
                  "data_cost_rub": b_quote["total_rub"], "quote": b_quote,
                  "covered_expected_loss_rub": b_covered,
                  "coverage_share": round(b_covered / exposure, 4) if exposure else 0,
                  "budget_feasible": b_quote["total_rub"] <= budget_rub,
                  "timely": deadline_hours >= 6},
            "C": {"label": "Приоритетная съёмка", **c,
                  "data_cost_rub": c["quote"]["total_rub"],
                  "coverage_share": round(c["covered_expected_loss_rub"] / exposure, 4) if exposure else 0,
                  "budget_feasible": c["feasible"], "timely": True},
        },
        "comparison": {"data_savings_vs_broad_rub": round(b_quote["total_rub"] - c["quote"]["total_rub"], 2),
                       "coverage_gain_vs_open_rub": c["covered_expected_loss_rub"],
                       "status": "scenario_coverage_not_prevented_loss"},
        "limitations": [
            "Вода: ручная эталонная метка Sen1Floods11, не прогноз модели.",
            "Вероятности объектов: независимый сценарный набор для проверки интерфейса; ML ещё не подключён.",
            "Цена: расчёт по формуле кейса со сценарной ставкой 788,64 ₽/км²; не котировка поставщика.",
            "Охваченный ожидаемый ущерб означает приоритет наблюдения, а не предотвращённый ущерб.",
        ],
    }
