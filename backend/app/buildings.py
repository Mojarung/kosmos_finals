"""Building footprints intersected with the actual scene grid, plus reproducible scenario costs."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import zipfile
from datetime import datetime, timezone

import numpy as np
import shapely
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from shapely.affinity import affine_transform
from shapely.geometry import shape

from . import building_rates as assumptions, building_sources as sources, catalog

router = APIRouter(prefix="/api/buildings", tags=["buildings"])
METHOD = {
    "version": "footprint-fractional-v1", "currency": "RUB", "cost_basis": "Площадь контура / первого этажа",
    "formula": "loss_rub = footprint_m2 × cost_rub_m2 × damage_ratio × water_probability_proxy",
    "probability": "Среднее p воды по площади пересечения контура с пикселями; не калиброванная вероятность затопления здания",
    "permanent_water": "При наличии маски постоянной воды p и маска воды в таких пикселях обнуляются",
    "coverage": "При неполном покрытии радаром ущерб не экстраполируется: сумма отсутствует",
    "default_valuation": "Для неизвестного типа: базовая ставка 45 000 ₽/м² × стабильный коэффициент 0,85–1,15 по ID. Это демонстрационная стоимость, не определение типа или рыночная оценка.",
    "limitations": [
        "Оценка сценарная. Глубина воды, конструкция, имущество, простой и реальный счёт за ремонт неизвестны.",
        "Площадь верхних этажей не включается; ставка относится к подверженному воздействию первому этажу.",
        "Sentinel-1 не подтверждает повреждение отдельного дома; в плотной застройке возможны пропуски воды.",
        "Низкий сигнал воды на позднем снимке не доказывает отсутствия ущерба во время пика паводка.",
        "Неизвестному типу назначается условная стоимость с вариацией по ID; назначение здания остаётся неизвестным.",
        "Без полного покрытия радаром в карточке показаны условные потери при затоплении; в сумму ущерба по снимку они не входят.",
    ],
}


class Rate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cost_rub_m2: float = Field(ge=0, le=10_000_000, allow_inf_nan=False)
    damage_ratio: float = Field(ge=0, le=1, allow_inf_nan=False)


class Evaluation(BaseModel):
    scene_id: str = Field(max_length=150)
    catalog_id: str = Field(max_length=24)
    rate_overrides: dict[str, Rate] = Field(default_factory=dict, max_length=30)
    type_overrides: dict[str, str] = Field(default_factory=dict, max_length=5000)


class LoadRequest(BaseModel):
    scene_id: str = Field(max_length=150)
    source: str = "auto"
    rate_overrides: dict[str, Rate] = Field(default_factory=dict, max_length=30)


class ImportRequest(BaseModel):
    scene_id: str = Field(max_length=150)
    content: str = Field(max_length=sources.MAX_BYTES)
    rate_overrides: dict[str, Rate] = Field(default_factory=dict, max_length=30)


def raster_stats(geometry, info: dict, probability, model, permanent) -> dict:
    west, south, east, north = info["bbox"]
    width, height = info["width"], info["height"]
    sx, sy = width / (east - west), height / (north - south)
    pixel_geometry = affine_transform(geometry, [sx, 0, 0, -sy, -west * sx, north * sy])
    x0, y0, x1, y1 = pixel_geometry.bounds
    xs = np.arange(max(0, math.floor(x0)), min(width, math.ceil(x1)))
    ys = np.arange(max(0, math.floor(y0)), min(height, math.ceil(y1)))
    if len(xs) * len(ys) > 200000:
        raise ValueError("Слишком большой контур здания для расчёта")
    columns, rows = np.meshgrid(xs, ys)
    weights = shapely.area(shapely.intersection(pixel_geometry, shapely.box(columns, rows, columns + 1, rows + 1))).ravel()
    indexes = (rows * width + columns).ravel()
    valid = (model[indexes] != 255) & np.isfinite(probability[indexes]) & (probability[indexes] >= 0) & (probability[indexes] <= 1)
    observed = float(weights[valid].sum())
    total = pixel_geometry.area
    coverage = min(1.0, observed / total)
    p, water = probability[indexes].copy(), (model[indexes] == 1)
    if permanent is not None:
        fixed = permanent[indexes] == 1
        p[fixed], water[fixed] = 0, False
    return {
        "radar_coverage": coverage,
        "water_probability_proxy": float(np.dot(weights[valid], p[valid]) / observed) if observed else None,
        "water_fraction": float(weights[valid & water].sum() / observed) if observed else None,
        "water_area_m2": sources.area_m2(geometry) * float(weights[valid & water].sum()) / total,
        "observed_pixels": int(np.count_nonzero(valid & (weights > 0))),
    }


def evaluate(request: Evaluation) -> dict:
    info = catalog.metadata(request.scene_id)
    dataset = sources.read(request.catalog_id)
    rates = {key: dict(value) for key, value in assumptions.RATES.items()}
    for key, value in request.rate_overrides.items():
        if key not in rates:
            raise ValueError(f"Неизвестный тип для ставки: {key}")
        rates[key].update(value.model_dump())
    features = dataset["features"]
    ids = {f["id"] for f in features}
    if set(request.type_overrides) - ids or any(t not in {*rates, "unknown"} for t in request.type_overrides.values()):
        raise ValueError("Неизвестное здание или тип в ручных уточнениях")
    probability = np.asarray(catalog.float_map(request.scene_id, "probability"), dtype=float)
    model = np.frombuffer(catalog.mask(request.scene_id, "model"), dtype=np.uint8)
    permanent_raw = catalog.permanent_mask(request.scene_id)
    permanent = np.frombuffer(permanent_raw, dtype=np.uint8) if permanent_raw is not None else None
    geometries = [shape(f["geometry"]) for f in features]
    # Ambiguous overlapping footprints must not silently inflate the total.
    overlaps = set()
    tree = shapely.STRtree(geometries)
    for index, geometry in enumerate(geometries):
        for other in tree.query(geometry, predicate="intersects"):
            if other > index and geometry.intersection(geometries[other]).area > min(geometry.area, geometries[other].area) * .02:
                overlaps.update((index, int(other)))
    rows = []
    for index, (feature, geometry) in enumerate(zip(features, geometries)):
        props = feature["properties"]
        kind, type_source = assumptions.resolve_type(props)
        if feature["id"] in request.type_overrides:
            kind, type_source = request.type_overrides[feature["id"]], "Уточнено пользователем"
        area = sources.area_m2(geometry)
        stats = raster_stats(geometry, info, probability, model, permanent)
        status = "ok"
        if stats["radar_coverage"] < 0.99999:
            status = "no_radar" if stats["radar_coverage"] <= 1e-8 else "partial_radar"
        if index in overlaps:
            status = "overlap"
        rate = rates.get(kind)
        if rate is None:
            status = "unknown_type"
        # Stable demo prices: reopening/repricing never draws a new random amount.
        cost_factor = 1 + (int(hashlib.sha256(feature["id"].encode()).hexdigest()[:8], 16) % 31 - 15) / 100 if kind == "unknown" else 1
        unit_cost = rate["cost_rub_m2"] * cost_factor if rate else None
        exposure = area * unit_cost if rate else None
        conditional = exposure * rate["damage_ratio"] if rate else None
        loss = conditional * stats["water_probability_proxy"] if status == "ok" else None
        rows.append({**feature, "properties": {**props, **stats, **assumptions.display_height(props["tags"]),
            "building_type": kind, "type_label": rate["label"] if rate else "Тип не определён",
            "type_source": type_source, "area_m2": area, "levels": props["tags"].get("building:levels"),
            "cost_rub_m2": unit_cost, "base_cost_rub_m2": rate["cost_rub_m2"] if rate else None,
            "cost_factor": cost_factor, "synthetic_cost": kind == "unknown", "damage_ratio": rate["damage_ratio"] if rate else None,
            "cost_source": ("Условная ставка: базовая цена × коэффициент по ID здания" if kind == "unknown" else "Ставка пользователя" if rate["cost_rub_m2"] != assumptions.RATES[kind]["cost_rub_m2"] else "Сценарная ставка команды") if rate else None,
            "damage_source": ("Доля повреждения пользователя" if rate["damage_ratio"] != assumptions.RATES[kind]["damage_ratio"] else "Допущение команды: 30%") if rate else None,
            "exposure_rub": exposure, "conditional_damage_rub": conditional,
            "expected_loss_rub": loss, "status": status, "overlapping_footprint": index in overlaps,
        }})
    ranked = sorted((f for f in rows if f["properties"]["water_probability_proxy"] is not None),
                    key=lambda f: (-(f["properties"]["water_probability_proxy"]),
                                   -(f["properties"]["expected_loss_rub"] or 0), f["id"]))
    ranks = {f["id"]: i + 1 for i, f in enumerate(ranked)}
    for feature in rows:
        feature["properties"]["risk_rank"] = ranks.get(feature["id"])
    estimated = [f["properties"]["expected_loss_rub"] for f in rows if f["properties"]["expected_loss_rub"] is not None]
    total = sum(estimated) if estimated else None
    return {"type": "FeatureCollection", "features": rows, "scene_id": request.scene_id, "catalog_id": request.catalog_id,
        "source": dataset["source"], "observed_at": info["observed_at"], "bbox": info["bbox"],
        "model": info.get("model"), "permanent_separated": permanent is not None,
        "summary": {"count": len(rows), "estimated": len(estimated), "unestimated": len(rows) - len(estimated),
                    "unknown_type": sum(f["properties"]["building_type"] == "unknown" for f in rows),
                    "with_water": sum((f["properties"]["water_fraction"] or 0) > 0 for f in rows),
                    "expected_loss_rub": total,
                    "conditional_damage_rub": sum(f["properties"]["conditional_damage_rub"] or 0 for f in rows if not f["properties"]["overlapping_footprint"]),
                    "synthetic_cost": sum(f["properties"]["synthetic_cost"] for f in rows)},
        "rates": rates, "rate_version": assumptions.VERSION, "rate_source": assumptions.SOURCE,
        "rate_overrides": {key: value.model_dump() for key, value in request.rate_overrides.items()},
        "type_overrides": request.type_overrides, "method": METHOD,
        "top_building_ids": [f["id"] for f in ranked[:10]],
        "ranking_rule": "По убыванию среднего p воды внутри контура; при равном p — по сценарию ущерба. Без данных радара место не присваивается.",
        "source_sha256": hashlib.sha256(json.dumps(dataset, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        "raster_sha256": hashlib.sha256(probability.astype('<f4').tobytes() + model.tobytes() + (permanent_raw or b'')).hexdigest(),
    }


def checked(action):
    try:
        return action()
    except KeyError as error:
        raise HTTPException(404, "Снимок не найден") from error
    except (ValueError, TypeError) as error:
        raise HTTPException(422, str(error)) from error


@router.get("/rates")
def rate_catalog():
    return {"rates": assumptions.RATES, "source": assumptions.SOURCE, "version": assumptions.VERSION, "method": METHOD}


@router.get("/demo")
def demo():
    path = sources.ROOT / "data/buildings/demo.json"
    return json.loads(path.read_text()) if path.exists() else {"scene_id": None}


@router.post("/load")
def load(request: LoadRequest):
    def action():
        info = catalog.metadata(request.scene_id)
        if request.source == "paiporta" or request.source == "auto" and request.scene_id == "Paiporta_20241101":
            data = sources.normalize(json.loads((sources.ROOT / "data/buildings/paiporta.geojson").read_text()))
        elif request.source in {"auto", "osm"}:
            data = sources.load_osm(info["bbox"])
        else:
            raise ValueError("Неизвестный источник контуров")
        return evaluate(Evaluation(scene_id=request.scene_id, catalog_id=sources.save(data), rate_overrides=request.rate_overrides))
    return checked(action)


@router.post("/import")
def import_buildings(request: ImportRequest):
    def action():
        catalog.metadata(request.scene_id)
        data = json.loads(request.content)
        data["source"] = {"name": "GeoJSON пользователя", "retrieved_at": datetime.now(timezone.utc).isoformat(),
                          "temporal_scope": "Дата и точность контуров требуют проверки пользователем"}
        normalized = sources.normalize(data)
        return evaluate(Evaluation(scene_id=request.scene_id, catalog_id=sources.save(normalized), rate_overrides=request.rate_overrides))
    return checked(action)


@router.post("/evaluate")
def evaluate_buildings(request: Evaluation):
    return checked(lambda: evaluate(request))


def package(result: dict) -> bytes:
    target, table = io.BytesIO(), io.StringIO()
    columns = ["id", "name", "building_type", "type_source", "risk_rank", "height_m", "height_source", "area_m2", "radar_coverage", "water_probability_proxy", "water_fraction",
               "cost_rub_m2", "base_cost_rub_m2", "cost_factor", "synthetic_cost", "cost_source", "damage_ratio", "damage_source", "exposure_rub", "conditional_damage_rub", "expected_loss_rub", "status", "source_url"]
    writer = csv.DictWriter(table, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for feature in result["features"]:
        # Spreadsheet formulas in imported names must remain text.
        row = {k: ("'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v) for k, v in feature["properties"].items()}
        writer.writerow(row)
    manifest = {k: v for k, v in result.items() if k not in {"features", "type"}}
    manifest["exported_at"] = datetime.now(timezone.utc).isoformat()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("buildings.geojson", json.dumps(result, ensure_ascii=False, allow_nan=False))
        archive.writestr("buildings.csv", "\ufeff" + table.getvalue())
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False))
        archive.writestr("rates.json", json.dumps(result["rates"], ensure_ascii=False, indent=2))
        archive.writestr("README.txt", "Сценарная оценка ущерба зданиям, RUB.\n" + METHOD["formula"] + "\n" + "\n".join(METHOD["limitations"]) + "\nИсточник начальных ставок: " + assumptions.SOURCE + "\nПрименённые ставки — rates.json; пользовательские настройки — rate_overrides в manifest.json.\nПроисхождение ставки и доли повреждения каждого здания — cost_source и damage_source в CSV/GeoJSON.")
    return target.getvalue()


@router.post("/export")
def export_buildings(request: Evaluation):
    content = checked(lambda: package(evaluate(request)))
    return Response(content, media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="building-damage.zip"'})
