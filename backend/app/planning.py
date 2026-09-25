"""Procurement math for frozen model outputs and a disclosed scenario tariff."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from itertools import combinations
from math import cos, log, pi

from pyproj import Transformer


BASE_RATE = Decimal("788.64")  # Scenario from the case materials, not an official quote.
MONEY = Decimal("0.01")
AREA = Decimal("0.001")
DISCOUNT = Decimal("0.000000001")
TO_EQUAL_AREA = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True)


def rounded(value: float | Decimal, unit: Decimal) -> Decimal:
    return Decimal(str(value)).quantize(unit, rounding=ROUND_HALF_UP)


def rectangle_area_km2(bounds: list[float]) -> float:
    west, south, east, north = bounds
    x0, y0 = TO_EQUAL_AREA.transform(west, south)
    x1, y1 = TO_EQUAL_AREA.transform(east, north)
    return abs((x1 - x0) * (y1 - y0)) / 1_000_000


def intersects(a: list[float], b: list[float]) -> bool:
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def contains(bounds: list[float], asset: dict) -> bool:
    return bounds[0] <= asset["longitude"] <= bounds[2] and bounds[1] <= asset["latitude"] <= bounds[3]


def as_polygon(bounds: list[float]) -> dict:
    w, s, e, n = bounds
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def square_around(asset: dict, chip_bounds: list[float]) -> list[float]:
    # A 1.12 km side leaves a margin above the case's 1 km² minimum.
    half_lat = 0.56 / 111.32
    half_lon = 0.56 / (111.32 * cos(asset["latitude"] * pi / 180))
    lon = min(max(asset["longitude"], chip_bounds[0] + half_lon), chip_bounds[2] - half_lon)
    lat = min(max(asset["latitude"], chip_bounds[1] + half_lat), chip_bounds[3] - half_lat)
    bounds = [lon - half_lon, lat - half_lat, lon + half_lon, lat + half_lat]
    if rectangle_area_km2(bounds) < 1:
        raise ValueError("Candidate zone is below 1 km²")
    return bounds


def candidates(assets: list[dict], chip_bounds: list[float], broad_bounds: list[float] | None = None) -> list[dict]:
    zones = []
    for asset in assets:
        bounds = square_around(asset, chip_bounds)
        zones.append({
            "id": f"Z{asset['id'][1:]}", "name": f"Зона · {asset['name'].lower()}",
            "bounds": bounds, "geometry": as_polygon(bounds),
            "area_km2": float(rounded(rectangle_area_km2(bounds), AREA)),
            "resolution_m": 1, "sensor_type": "optical", "delivery_hours": 6,
            "data_role": "event_observation", "acquisition_type": "new",
            "processing_level": "L2", "usage_type": "internal", "guaranteed_purchase": True,
            "base_rate_status": "scenario", "base_rate_source": "case_materials_control_example",
            "base_rate_rub_km2": float(BASE_RATE), "observation_at": None,
            "available_at": None, "legal_edition": "PP_RF_840_2025-08-27",
        })
    broad_bounds = broad_bounds or chip_bounds
    zones.append({
        "id": "ZALL", "name": "Широкая съёмка · область анализа", "bounds": broad_bounds,
        "geometry": as_polygon(broad_bounds),
        "area_km2": float(rounded(rectangle_area_km2(broad_bounds), AREA)),
        "resolution_m": 1, "sensor_type": "optical", "delivery_hours": 6,
        "data_role": "event_observation", "acquisition_type": "new",
        "processing_level": "L2", "usage_type": "internal", "guaranteed_purchase": True,
        "base_rate_status": "scenario", "base_rate_source": "case_materials_control_example",
        "base_rate_rub_km2": float(BASE_RATE), "observation_at": None,
        "available_at": None, "legal_edition": "PP_RF_840_2025-08-27",
    })
    return zones


def quote(zones: list[dict], tariff_multiplier: float = 1.0) -> dict:
    """Recalculate volume discount for the entire basket, then round each line."""
    if not zones:
        return {"total_rub": 0.0, "group_area_km2": 0, "discount_coef": 1.0, "lines": []}
    group_area = sum(Decimal(str(z["area_km2"])) for z in zones)
    # The demo catalog is one optical / 1 m / calendar-year group.
    raw_discount = -0.058 * log(float(group_area)) + 1.037
    discount = rounded(min(1.0, max(0.2, raw_discount)), DISCOUNT)
    scenario_rate = BASE_RATE * Decimal(str(tariff_multiplier))
    unit_price = rounded(scenario_rate * Decimal("1.2") * Decimal("1.8") * discount, MONEY)
    lines = [{
        "candidate_id": zone["id"], "area_km2": zone["area_km2"],
        "unit_price_rub_km2": float(unit_price),
        "cost_rub": float(rounded(unit_price * Decimal(str(zone["area_km2"])), MONEY)),
        "discount_coef": float(discount), "group_area_km2": float(group_area),
        "base_rate_rub_km2": float(scenario_rate), "base_rate_status": "scenario",
        "processing_coef": 1.2, "usage_coef": 1.0, "freshness_coef": 1.8,
        "formula": "B*K*O*P*T*R", "legal_edition": "PP_RF_840_2025-08-27",
    } for zone in zones]
    total = sum(Decimal(str(line["cost_rub"])) for line in lines)
    return {"total_rub": float(total), "group_area_km2": float(group_area),
            "discount_coef": float(discount), "lines": lines}


def enrich_assets(assets: list[dict]) -> list[dict]:
    result = []
    for asset in assets:
        p = asset["p_flood"]
        uncertainty = asset["uncertainty"]
        result.append({
            **asset,
            "expected_loss_rub": round(p * asset["value_rub"] * asset["vulnerability"], 2) if p is not None else None,
            "attention_score": round(min(1, uncertainty / 0.15), 3) if uncertainty is not None else None,
            "probability_status": "frozen_ensemble",
        })
    return sorted(result, key=lambda asset: (-(asset["expected_loss_rub"] or 0), asset["id"]))


def covered_assets(zones: list[dict], assets: list[dict]) -> list[dict]:
    return [asset for asset in assets if any(contains(zone["bounds"], asset) for zone in zones)]


def plan(assets: list[dict], zones: list[dict], budget_rub: float, priority: str,
         deadline_hours: int, must_cover: str | None,
         tariff_multiplier: float = 1.0) -> dict:
    timely = [zone for zone in zones if zone["delivery_hours"] <= deadline_hours]
    best: tuple[float, float, list[dict], dict] | None = None
    for size in range(len(timely) + 1):
        for group in combinations(timely, size):
            selected = list(group)
            price = quote(selected, tariff_multiplier)
            if price["total_rub"] > budget_rub:
                continue
            covered = covered_assets(selected, assets)
            if must_cover and not any(asset["id"] == must_cover for asset in covered):
                continue
            utility = sum((asset["expected_loss_rub"] or 0) *
                          (0.25 + 0.75 * (asset["attention_score"] or 0) if priority == "information" else 1)
                          for asset in covered)
            score = (utility, -price["total_rub"])
            if best is None or score > best[:2]:
                best = (score[0], score[1], selected, price)
    if best is None:
        return {"zone_ids": [], "quote": quote([]), "covered_asset_ids": [],
                "covered_expected_loss_rub": 0, "feasible": False,
                "reason": "Обязательный объект не успевает или не помещается в бюджет"}
    _, _, selected, price = best
    covered = covered_assets(selected, assets)
    return {"zone_ids": [zone["id"] for zone in selected], "quote": price,
            "covered_asset_ids": [asset["id"] for asset in covered],
            "covered_expected_loss_rub": round(sum(a["expected_loss_rub"] or 0 for a in covered), 2),
            "feasible": True, "reason": None}
