"""Scenario value of an ideal timely observation for an asset inspection decision."""
from __future__ import annotations

import csv
import io
import json
import math
import zipfile

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from .analysis import context
from .catalog import default_scene_id, known_scene_ids, metadata
from .costs import decision_costs
from .planning import quote, today

router = APIRouter(prefix="/api/voi", tags=["procurement"])
DEFAULT_VISIT_COST_RUB = 10_000.0
METHOD = ("Для каждого оценённого объекта L=V*q, а выезд стоит C. До нового снимка выбираем min(C,p*L); "
          "после идеального своевременного наблюдения платим p*min(C,L). VOI - их разность. Польза корзины - "
          "сумма VOI по объединению покрытых объектов без двойного счёта. Максимизируем VOI корзины минус точную "
          "сценарную цену данных по всем своевременным подмножествам; покупаем только при положительной чистой пользе.")
LIMITATION = ("Только сценарий: p берётся из модели текущей сцены; калибровка вероятности и экономия на решениях "
              "требуют независимой проверки; V/q и стоимость выезда являются допущениями; новый снимок считается "
              "идеальным и своевременным. Выезды на место, фактическое снижение ущерба, облачность и цена "
              "поставщика не измерялись. Затраты на проверку и обработку показаны отдельно и не входят в эту "
              "оптимизацию только по данным.")


def asset_information_value(p: float | None, exposure_rub: float, visit_cost_rub: float) -> dict:
    """The visit-or-skip Bayes action and its expected avoidable decision cost."""
    if p is None or not math.isfinite(p) or not 0 <= p <= 1:
        return {"status": "no_probability", "voi_rub": None, "before_rub": None, "after_rub": None,
                "action_before": None}
    if not math.isfinite(exposure_rub) or exposure_rub < 0 or not math.isfinite(visit_cost_rub) or visit_cost_rub < 0:
        raise ValueError("Exposure and visit cost must be finite and non-negative")
    missed = p * exposure_rub
    visit = visit_cost_rub
    before = min(visit, missed)
    after = p * min(visit, exposure_rub)
    return {"status": "scenario", "voi_rub": round(max(0.0, before - after), 2),
            "before_rub": round(before, 2), "after_rub": round(after, 2),
            "action_before": "visit" if visit <= missed else "skip"}


def _bounds(value: str | None) -> list[float] | None:
    if not value:
        return None
    try:
        coords = [float(piece) for piece in value.split(",")]
    except ValueError as error:
        raise HTTPException(422, "bbox: четыре координаты west,south,east,north") from error
    if (len(coords) != 4 or not all(math.isfinite(x) for x in coords)
            or not (-180 <= coords[0] < coords[2] <= 180)
            or not (-85 <= coords[1] < coords[3] <= 85)):
        raise HTTPException(422, "bbox: некорректные границы")
    return coords


def calculate(scene_id: str, bounds: list[float] | None, visit_cost_rub: float,
              deadline_hours: int, must_cover: str | None, target_ids: list[str] | None = None) -> dict:
    if not math.isfinite(visit_cost_rub) or not 0 <= visit_cost_rub <= 10_000_000:
        raise ValueError("Стоимость выезда должна быть от 0 до 10 млн ₽")
    if not 1 <= deadline_hours <= 48:
        raise ValueError("Срок решения должен быть от 1 до 48 ч")
    ctx = context(scene_id, bounds, target_ids)
    space = ctx.space
    if must_cover and must_cover not in {a["id"] for a in ctx.assets}:
        raise ValueError("Неизвестный обязательный объект")
    on = today()
    asset_rows = []
    for asset in ctx.assets:
        value = asset_information_value(asset["p_flood"], asset["exposure_rub"], visit_cost_rub)
        asset_rows.append({"asset_id": asset["id"], "name": asset["name"], "p_water": asset["p_flood"],
                           "exposure_rub": asset["exposure_rub"], **value})
    values = np.array([row["voi_rub"] or 0 for row in asset_rows])
    benefit = np.round(space.asset_covered @ values, 2)
    prices = np.round(space.prices(1.0, deadline_hours), 2)
    net = np.round(benefit - prices, 2)
    timely = (space.subsets & space.late(deadline_hours)) == 0
    eligible = timely.copy()
    if must_cover:
        position = next(k for k, asset in enumerate(ctx.assets) if asset["id"] == must_cover)
        eligible &= space.asset_covered[:, position]
    options = np.flatnonzero(eligible & (net > 0))
    if len(options):
        order = np.lexsort((space.count[options], prices[options], -net[options]))
        index = int(options[order[0]])
    else:
        index = 0
    # The vectorised quote uses the same round-half-up rule as quote(). Verify the
    # winning basket at the exact Decimal boundary before returning its cost.
    orders = space.orders(index, deadline_hours)
    priced = quote(orders, strategy="C", calculation_date=on)
    if abs(priced["total_rub"] - prices[index]) > .005:
        raise ValueError("Векторная цена корзины разошлась с точной сметой")
    chosen_zones = space.selected(index)
    chosen = {zone["id"] for zone in chosen_zones}
    chosen_assets = [asset["id"] for asset, covered in zip(ctx.assets, space.asset_covered[index]) if covered]
    zones = []
    for k, zone in enumerate(space.zones):
        bit = 1 << k
        product = space.chosen(deadline_hours)[k]
        alone = quote([product], strategy="C", calculation_date=on)["total_rub"]
        alternative = index ^ bit if zone["id"] in chosen else index | bit
        alternative_quote = quote(space.orders(alternative, deadline_hours), strategy="C", calculation_date=on)
        if abs(alternative_quote["total_rub"] - prices[alternative]) > .005:
            raise ValueError("Векторная цена альтернативы разошлась с точной сметой")
        selected = zone["id"] in chosen
        marginal_benefit = benefit[index] - benefit[alternative] if selected else benefit[alternative] - benefit[index]
        marginal_price = prices[index] - prices[alternative] if selected else prices[alternative] - prices[index]
        timely_zone = product["delivery_hours"] <= deadline_hours
        asset_ids = [asset["id"] for asset, covered in zip(ctx.assets, space.asset_covered[bit]) if covered]
        zones.append({"zone_id": zone["id"], "name": zone["name"], "kind": zone["kind"], "asset_ids": asset_ids,
                      "selected": selected, "timely": timely_zone, "candidate_id": product["candidate_id"],
                      "delivery_hours": product["delivery_hours"], "sensor_type": product["sensor_type"],
                      "area_km2": zone["area_km2"], "voi_alone_rub": round(float(benefit[bit]), 2) if timely_zone else 0,
                      "price_alone_rub": alone, "net_alone_rub": round(float(benefit[bit]) - alone, 2) if timely_zone else -alone,
                      "marginal_voi_rub": round(float(marginal_benefit), 2) if timely_zone else 0,
                      "marginal_data_cost_rub": round(float(marginal_price), 2),
                      "reason": "selected" if selected else "late" if not timely_zone else
                                "no_new_information" if marginal_benefit <= 0 else "not_economic_in_optimal_basket"})
    baseline_cost = round(sum(row["before_rub"] or 0 for row in asset_rows), 2)
    after_cost = round(baseline_cost - float(benefit[index]), 2)
    return {"scene_id": scene_id, "model": metadata(scene_id).get("model", {"id": "service_s1"}), "inputs": {"visit_cost_rub": visit_cost_rub,
            "deadline_hours": deadline_hours, "must_cover": must_cover, "bbox": bounds, "target_ids": target_ids},
            "status": "positive_net_basket" if index else "no_positive_net_basket",
            "method": METHOD, "limitation": LIMITATION,
            "scenario": {"visit_cost_status": "operator_scenario", "price_status": "scenario_not_supplier_quote",
                         "observation_status": "ideal_timely_scenario", "calculation_date": on.isoformat(),
                         "scope": "data_price_only; other_cost_rub показан отдельно"},
            "assets": asset_rows, "zones": zones,
            "plan": {"zone_ids": [zone["id"] for zone in chosen_zones],
                     "candidate_ids": [order["candidate_id"] for order in orders],
                     "covered_asset_ids": chosen_assets, "derived_budget_rub": priced["total_rub"],
                     "quote": priced, "voi_rub": round(float(benefit[index]), 2),
                     "net_after_data_rub": round(float(benefit[index]) - priced["total_rub"], 2),
                     "action_cost_before_rub": baseline_cost, "action_cost_after_observation_rub": after_cost,
                     "positive_basket_exists": bool(options.size),
                     "constraints_feasible": not must_cover or bool(options.size),
                     **decision_costs(priced["total_rub"], priced["lines"])}}


def _request(scene_id: str | None, bbox: str | None, visit_cost: float, deadline: int, must_cover: str | None, target_ids: str | None = None) -> dict:
    scene = scene_id or default_scene_id()
    if scene not in known_scene_ids():
        raise HTTPException(404, "Неизвестная сцена")
    try:
        return calculate(scene, _bounds(bbox), visit_cost, deadline, must_cover, target_ids.split(",") if target_ids is not None else None)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get("")
def get_voi(scene_id: str | None = None, bbox: str | None = None,
            visit_cost: float = Query(DEFAULT_VISIT_COST_RUB, ge=0, le=10_000_000),
            deadline: int = Query(8, ge=1, le=48), must_cover: str | None = None, target_ids: str | None = None) -> dict:
    return _request(scene_id, bbox, visit_cost, deadline, must_cover, target_ids)


@router.get("/export")
def export_voi(scene_id: str | None = None, bbox: str | None = None,
               visit_cost: float = Query(DEFAULT_VISIT_COST_RUB, ge=0, le=10_000_000),
               deadline: int = Query(8, ge=1, le=48), must_cover: str | None = None, target_ids: str | None = None):
    result = _request(scene_id, bbox, visit_cost, deadline, must_cover, target_ids)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("value_of_information.json", json.dumps(result, ensure_ascii=False, indent=2))
        csv_stream = io.StringIO()
        columns = ["zone_id", "name", "kind", "asset_ids", "selected", "timely", "area_km2",
                   "voi_alone_rub", "price_alone_rub", "net_alone_rub", "marginal_voi_rub",
                   "marginal_data_cost_rub", "reason"]
        writer = csv.DictWriter(csv_stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows({key: ",".join(row[key]) if key == "asset_ids" else row[key] for key in columns}
                         for row in result["zones"])
        archive.writestr("zone_value.csv", csv_stream.getvalue())
    stream.seek(0)
    return StreamingResponse(stream, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="floodvalue-voi-{result["scene_id"]}.zip"'})
