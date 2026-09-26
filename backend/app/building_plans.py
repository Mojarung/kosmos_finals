"""Imagery baskets over building footprints and the same editable damage assumptions."""
from __future__ import annotations

import io
import hashlib
import json
import math
import zipfile
from functools import lru_cache
from typing import Literal

import numpy as np
from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field
from shapely.geometry import shape

from . import building_sources, buildings, catalog, planning, procurement, project_imagery
from .costs import decision_costs
from .voi import asset_information_value

router = APIRouter(prefix="/api/building-plans", tags=["buildings"])


class Scope(buildings.Evaluation):
    model_config = ConfigDict(extra="forbid")
    target_ids: list[str] | None = Field(default=None, max_length=building_sources.MAX_OSM_BUILDINGS)


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    buildings: Scope
    budget_rub: float = Field(default=6500, ge=0, le=1e9, allow_inf_nan=False)
    deadline_hours: int = Field(default=8, ge=1, le=48)
    method: Literal["budget", "value"] = "budget"
    visit_cost_rub: float = Field(default=10000, ge=0, le=10000000, allow_inf_nan=False)
    strategy: Literal["A", "B", "C"] = "C"


class ImageryRequest(project_imagery.Input):
    buildings: Scope


def padded_bounds(bounds):
    """A footprint bounding rectangle with a margin above the scenario tariff's 1 km² floor."""
    w, s, e, n = bounds
    lon, lat = (w + e) / 2, (s + n) / 2
    dx = max((e - w) / 2 + 1e-7, .56 / (111.32 * math.cos(math.radians(lat))))
    dy = max((n - s) / 2 + 1e-7, .56 / 111.32)
    for _ in range(8):
        result = [max(-180, lon - dx), max(-85, lat - dy), min(180, lon + dx), min(85, lat + dy)]
        if planning.rectangle_area_km2(result) >= 1.001:
            return result
        dx *= 1.25
        dy *= 1.25
    raise ValueError("Не удалось построить зону съёмки у границы карты")


def zone(identifier, name, bounds, observed_at, kind="object"):
    result = {"id": identifier, "name": name, "kind": kind, "bounds": bounds,
              "geometry": planning.as_polygon(bounds), "area_km2": float(planning.rounded(planning.rectangle_area_km2(bounds), planning.AREA))}
    result["products"] = planning.products(result, planning.as_date(observed_at))
    return result


@lru_cache(maxsize=2)
def prepared(scope_json: str, on: str, permanent_sha: str):
    scope = Scope.model_validate_json(scope_json)
    result = buildings.evaluate(scope)
    all_ids = {f["id"] for f in result["features"]}
    targets = all_ids if scope.target_ids is None else set(scope.target_ids)
    if not targets:
        raise ValueError("Добавьте здания из карточек или выберите все здания")
    if targets - all_ids or scope.target_ids is not None and len(targets) != len(scope.target_ids):
        raise ValueError("Неизвестное или повторное здание в подборке")
    features = [f for f in result["features"] if f["id"] in targets]
    assets = []
    for feature in features:
        p = feature["properties"]
        bounds = list(shape(feature["geometry"]).bounds)
        assets.append({"id": feature["id"], "name": p["tags"].get("name") or p["type_label"] + " · " + feature["id"],
                       "geometry": feature["geometry"], "footprint_bounds": bounds,
                       "longitude": (bounds[0] + bounds[2]) / 2, "latitude": (bounds[1] + bounds[3]) / 2,
                       "p_flood": p["water_probability_proxy"] if p["status"] == "ok" else None,
                       "value_rub": p["exposure_rub"], "exposure_rub": p["conditional_damage_rub"],
                       "expected_loss_rub": p["expected_loss_rub"], "synthetic_cost": p["synthetic_cost"]})
    # Seed spatial groups with high-loss buildings, then uncertainty. All buildings
    # covered by each rectangle participate; the shortlist is not restricted to the top ten.
    remaining = sorted(assets, key=lambda a: (-(a["expected_loss_rub"] or 0), -min(a["p_flood"] or 0, 1 - (a["p_flood"] or 0)), a["id"]))
    zones = []
    while remaining and len(zones) < 10:
        bounds = padded_bounds(remaining[0]["footprint_bounds"])
        zones.append(zone(f"BG{len(zones) + 1}", f"Группа зданий {len(zones) + 1}", bounds, result["observed_at"]))
        remaining = [a for a in remaining if not planning.contains(bounds, a)]
    bounds = padded_bounds([min(a["footprint_bounds"][0] for a in assets), min(a["footprint_bounds"][1] for a in assets),
                            max(a["footprint_bounds"][2] for a in assets), max(a["footprint_bounds"][3] for a in assets)])
    zones.append(zone("BGALL", "Все выбранные здания", bounds, result["observed_at"], "broad"))
    return result, features, assets, planning.BasketSpace(zones, assets, compact=True)


def inventory(scope: Scope):
    # Rates/types/scope and permanent-water updates invalidate the expensive footprint calculation.
    permanent = catalog.permanent_mask(scope.scene_id) or b""
    return prepared(scope.model_dump_json(), planning.today().isoformat(), hashlib.sha256(permanent).hexdigest())


catalog.on_refresh(prepared.cache_clear)


def calculate(request: PlanRequest):
    dataset, features, assets, space = inventory(request.buildings)
    values = [asset_information_value(a["p_flood"], a["exposure_rub"], request.visit_cost_rub) for a in assets]
    benefit = space.losses_for(np.array([v["voi_rub"] or 0 for v in values]))
    prices = space.prices(1, request.deadline_hours)
    eligible = space.feasible(request.budget_rub, request.deadline_hours, None, 1)
    indexes = np.flatnonzero(eligible)
    losses = space.losses_for(None)
    score = losses if request.method == "budget" else benefit - prices
    if request.method == "value":
        indexes = indexes[score[indexes] > .005]
    order = np.lexsort((space.count[indexes], prices[indexes], -score[indexes]))
    chosen = 0
    for position in order:
        candidate = int(indexes[position])
        exact = planning.quote(space.orders(candidate, request.deadline_hours))["total_rub"]
        if exact <= request.budget_rub and (request.method == "budget" or benefit[candidate] > exact):
            chosen = candidate
            break
    total_loss = sum(a["expected_loss_rub"] or 0 for a in assets)
    before = sum(v["before_rub"] or 0 for v in values)

    def summary(index, strategy):
        quoted = planning.quote(space.orders(index, request.deadline_hours), strategy=strategy)
        timely = (index & space.late(request.deadline_hours)) == 0
        effective = index if timely else 0
        ids = [a["id"] for a, covered in zip(assets, space.covered_assets(effective)) if covered]
        return {"zone_ids": [z["id"] for z in space.selected(index)], "covered_building_ids": ids,
                "data_cost_rub": quoted["total_rub"], "quote": quoted, "timely": timely,
                "budget_feasible": quoted["total_rub"] <= request.budget_rub,
                "covered_expected_loss_rub": round(float(losses[effective]), 2),
                "coverage_share": min(1.0, float(losses[effective]) / total_loss) if total_loss else 0,
                "voi_rub": round(float(benefit[effective]), 2),
                "net_after_data_rub": round(float(benefit[effective]) - quoted["total_rub"], 2),
                "action_cost_before_rub": round(before, 2), "action_cost_after_rub": round(before - float(benefit[effective]), 2),
                **decision_costs(quoted["total_rub"], quoted["lines"])}

    strategies = {"A": summary(0, "A"), "B": summary(1 << (len(space.zones) - 1), "B"), "C": summary(chosen, "C")}
    zones = []
    for k, z in enumerate(space.zones):
        product = planning.chosen_product(z, request.deadline_hours)
        ids = [a["id"] for a, covered in zip(assets, space.covered_assets(1 << k)) if covered]
        zones.append({**{key: value for key, value in z.items() if key != "products"},
                      "building_ids": ids, "building_count": len(ids), "product": product,
                      "expected_loss_rub": round(float(losses[1 << k]), 2),
                      "price_alone_rub": planning.quote([product])["total_rub"]})
    return {"scene_id": request.buildings.scene_id, "inputs": request.model_dump(), "strategies": strategies, "zones": zones,
            "scope": {"count": len(assets), "estimated": sum(a["p_flood"] is not None for a in assets),
                      "synthetic_cost": sum(a["synthetic_cost"] for a in assets), "total_expected_loss_rub": round(total_loss, 2),
                      "building_ids": [a["id"] for a in assets], "bbox": space.zones[-1]["bounds"]},
            "catalog_id": dataset["catalog_id"], "raster_sha256": dataset["raster_sha256"],
            "source_sha256": dataset["source_sha256"], "rates": dataset["rates"], "source": dataset["source"],
            "method": {"coverage": "Полный контур внутри хотя бы одной зоны; каждое здание учитывается один раз.",
                       "search": "Точный перебор корзин из не более 10 пространственных групп зданий и одной общей зоны; не глобальный поиск всех возможных контуров съёмки.",
                       "price": "Сценарный тариф в RUB, минимум 1 км² на зону. Это не предложение поставщика.",
                       "value": "Сценарная экономия на выездах и пропусках при идеальном новом снимке; p — сигнал модели, не проверенная вероятность затопления дома.",
                       "damage": "Охваченный ущерб — приоритет проверки, не предотвращённые потери. Стоимость обработки показывается отдельно."}}


@router.post("")
def plan(request: PlanRequest):
    return buildings.checked(lambda: calculate(request))


def export_bundle(scope, result, kind):
    dataset, features, _, _ = inventory(scope)
    estimated = [f["properties"]["expected_loss_rub"] for f in features if f["properties"]["expected_loss_rub"] is not None]
    subset = {**dataset, "features": features,
              "top_building_ids": [f["id"] for f in sorted(features, key=lambda f: f["properties"]["risk_rank"] or 10**9)[:10]],
              "summary": {"count": len(features), "estimated": len(estimated), "unestimated": len(features) - len(estimated),
                          "unknown_type": sum(f["properties"]["building_type"] == "unknown" for f in features),
                          "synthetic_cost": sum(f["properties"]["synthetic_cost"] for f in features),
                          "with_water": sum((f["properties"]["water_fraction"] or 0) > 0 for f in features),
                          "expected_loss_rub": sum(estimated) if estimated else None}}
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("plan.json", json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        with zipfile.ZipFile(io.BytesIO(buildings.package(subset))) as building_archive:
            for name in building_archive.namelist():
                archive.writestr("buildings/" + name, building_archive.read(name))
        if kind != "skyfi":
            selected = set(result["strategies"][result["inputs"]["strategy"]]["zone_ids"])
            archive.writestr("zones.geojson", json.dumps({"type": "FeatureCollection", "features": [
                {"type": "Feature", "geometry": z["geometry"], "properties": {k: v for k, v in {**z, "selected": z["id"] in selected}.items() if k != "geometry"}}
                for z in result["zones"]]}, ensure_ascii=False, allow_nan=False))
        archive.writestr("README.txt", "План съёмки реальных зданий. Полные контуры, без повторного учёта зданий.\nЦены зданий и ущерб сценарные; условные цены отмечены synthetic_cost. Заказ не отправлен.\n" + ("Цены SkyFi в USD; архив подтверждает только свою дату." if kind == "skyfi" else "Тариф снимков в RUB сценарный. Охваченный ущерб не является предотвращённым ущербом."))
    return Response(content.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="building-{kind}-plan.zip"'})


@router.post("/export")
def export(request: PlanRequest):
    return buildings.checked(lambda: export_bundle(request.buildings, calculate(request), "scenario"))


def imagery(request: ImageryRequest):
    dataset, features, assets, _ = inventory(request.buildings)
    response = project_imagery.preview_inventory({"id": None, "name": "Здания", "scene_id": request.buildings.scene_id}, assets,
                                                procurement.read(request.catalog_id), request)
    return {**response, "object_source": "buildings", "building_scope": request.buildings.model_dump(),
            "building_source_sha256": dataset["source_sha256"], "building_raster_sha256": dataset["raster_sha256"],
            "building_rates": dataset["rates"], "coverage_rule": "Полный контур здания внутри отдельного снимка; пересечения учитываются один раз."}


@router.post("/imagery/plan")
def imagery_plan(request: ImageryRequest):
    return buildings.checked(lambda: imagery(request))


@router.post("/imagery/export")
def imagery_export(request: ImageryRequest):
    return buildings.checked(lambda: export_bundle(request.buildings, imagery(request), "skyfi"))
