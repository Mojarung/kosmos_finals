"""Editable, separately labelled economic scenarios over one frozen probability map.

The required ten case assets and the canonical A/B/C export never change. This
module lets a jury change assumptions, then reprices and re-optimises every basket.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

from .analysis import context, residual_for
from .catalog import known_scene_ids
from .costs import decision_costs
from .planning import BASE_RATE, BasketSpace, chosen_product, today


class AssetChange(BaseModel):
    asset_id: str
    p_flood: float | None = Field(default=None, ge=0, le=1)
    value_rub: float | None = Field(default=None, ge=0, le=1_000_000_000)
    vulnerability: float | None = Field(default=None, ge=0, le=1)


class ScenarioRequest(BaseModel):
    target_ids: list[str] | None = None
    scene_id: str
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    budget_rub: float = Field(default=6500, ge=0, le=1_000_000)
    deadline_hours: int = Field(default=8, ge=1, le=48)
    priority: Literal["risk", "information"] = "risk"
    must_cover: str | None = None
    base_rate_rub_km2: float = Field(default=float(BASE_RATE), ge=0, le=1_000_000)
    processing_level: Literal["default", "L0", "L1", "L2"] = "default"
    usage_type: Literal["internal", "limited", "unrestricted"] = "internal"
    asset_changes: list[AssetChange] = Field(default_factory=list, max_length=10)


def _money(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _space(ctx, processing_level: str, usage_type: str) -> BasketSpace:
    if processing_level == "default" and usage_type == "internal":
        return ctx.space
    zones = []
    for zone in ctx.zones:
        products = []
        for product in zone["products"]:
            products.append({**product,
                             "processing_level": product["processing_level"] if processing_level == "default" else processing_level,
                             "usage_type": usage_type})
        zones.append({**zone, "products": products})
    return BasketSpace(zones, ctx.assets, ctx.space.doubt_masks, ctx.space.doubt_mass, ctx.space.doubt_total)


def _assets(ctx, changes: list[AssetChange]) -> tuple[list[dict], np.ndarray]:
    by_id = {item.asset_id: item for item in changes}
    if len(by_id) != len(changes):
        raise ValueError("Один объект указан несколько раз.")
    known = {asset["id"] for asset in ctx.assets}
    unknown = set(by_id) - known
    if unknown:
        raise ValueError("Неизвестный объект: " + ", ".join(sorted(unknown)))
    rows = []
    for asset in ctx.assets:
        change = by_id.get(asset["id"])
        p = change.p_flood if change and change.p_flood is not None else asset["p_flood"]
        value = change.value_rub if change and change.value_rub is not None else asset["value_rub"]
        vulnerability = change.vulnerability if change and change.vulnerability is not None else asset["vulnerability"]
        el = _money(p * value * vulnerability) if p is not None else None
        rows.append({"asset_id": asset["id"], "name": asset["name"], "p_flood": p,
                     "value_rub": value, "vulnerability": vulnerability,
                     "expected_loss_rub": el, "original_expected_loss_rub": asset["case_expected_loss_rub"],
                     "status": "scenario_override" if change else "case_value",
                     "inference_p_unchanged": asset["p_flood"]})
    ranked = sorted((row for row in rows if row["expected_loss_rub"] is not None),
                    key=lambda row: (-row["expected_loss_rub"], row["asset_id"]))
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
    return rows, np.array([row["expected_loss_rub"] or 0 for row in rows], dtype=float)


def _strategy(space: BasketSpace, ctx, index: int | None, strategy: str, deadline: int,
              budget: float, tariff: float, losses: np.ndarray, exposure: float) -> dict:
    item = space.summary(index, deadline, tariff, losses, strategy=strategy, on=today())
    timely = strategy != "B" or not item["zone_ids"] or all(
        chosen_product(zone, deadline)["delivery_hours"] <= deadline
        for zone in space.zones if zone["id"] in item["zone_ids"])
    covered_ids = item["covered_asset_ids"] if timely else []
    covered = item["covered_expected_loss_rub"] if timely else 0
    cost = item["quote"]["total_rub"]
    other = decision_costs(cost, item["quote"]["lines"])
    return {"strategy": strategy, "zone_ids": item["zone_ids"], "candidate_ids": item["candidate_ids"],
            "covered_asset_ids": covered_ids, "covered_expected_loss_rub": covered,
            "coverage_share": round(covered / exposure, 4) if exposure else None,
            "covered_doubt_share": item["covered_doubt_share"] if timely else 0,
            "data_cost_rub": cost, **other, "quote": item["quote"],
            "budget_feasible": cost <= budget, "constraints_feasible": item["feasible"],
            "reason": item["reason"], "timely": timely,
            **residual_for(ctx, item["zone_ids"] if timely else [])}


def _plan(ctx, space: BasketSpace, budget: float, deadline: int, priority: str,
          must_cover: str | None, tariff: float, losses: np.ndarray) -> dict:
    exposure = _money(float(losses.sum()))
    c_index = space.best(budget, priority, deadline, must_cover, tariff, losses)
    broad_index = space.index(["ZALL"]) if any(zone["id"] == "ZALL" for zone in space.zones) else None
    strategies = {
        "A": _strategy(space, ctx, 0, "A", deadline, budget, tariff, losses, exposure),
        "B": _strategy(space, ctx, broad_index, "B", deadline, budget, tariff, losses, exposure),
        "C": _strategy(space, ctx, c_index, "C", deadline, budget, tariff, losses, exposure),
    }
    decisions = space.decisions(c_index, budget, priority, deadline, must_cover, tariff, on=today(), losses=losses)
    return {"expected_loss_rub": exposure, "strategies": strategies, "zone_decisions": decisions,
            "selected_zone_ids": strategies["C"]["zone_ids"]}


def calculate(request: ScenarioRequest) -> dict:
    if request.scene_id not in known_scene_ids():
        raise ValueError("Снимок не найден.")
    ctx = context(request.scene_id, request.bbox, request.target_ids)
    if request.must_cover and request.must_cover not in {item["id"] for item in ctx.assets}:
        raise ValueError("Обязательный объект не найден.")
    assets, losses = _assets(ctx, request.asset_changes)
    baseline = _plan(ctx, ctx.space, request.budget_rub, request.deadline_hours,
                     request.priority, request.must_cover, 1.0, ctx.space.losses)
    scenario_space = _space(ctx, request.processing_level, request.usage_type)
    tariff = request.base_rate_rub_km2 / float(BASE_RATE)
    scenario = _plan(ctx, scenario_space, request.budget_rub, request.deadline_hours,
                     request.priority, request.must_cover, tariff, losses)
    before, after = baseline["strategies"]["C"], scenario["strategies"]["C"]
    return {"scene_id": request.scene_id, "source_date": ctx.info.get("observed_at"),
            "region_bbox": ctx.region.get("bounds"),
            "status": "editable_scenario_not_case_submission", "calculated_at": today().isoformat(),
            "inputs": request.model_dump(), "case": baseline, "scenario": scenario, "assets": assets,
            "changes": {"c_data_cost_rub": _money(after["data_cost_rub"] - before["data_cost_rub"]),
                        "c_covered_expected_loss_rub": _money(after["covered_expected_loss_rub"] - before["covered_expected_loss_rub"]),
                        "basket_changed": before["zone_ids"] != after["zone_ids"]},
            "explanation": [
                "This is a separate economic scenario. The required case assets, probability raster and case ZIP remain unchanged.",
                "p override changes the economic assumption at one object, not the model raster or its calibration.",
                "The entered base rate is a scenario, not an official tariff or supplier quote. O/P/T/R follow the selected product and published case formulas.",
                "B may exceed the budget and is then counterfactual; C is re-optimised with the entered assumptions.",
                "Covered expected loss is an observation priority, not prevented physical damage.",
            ]}


def package(result: dict) -> bytes:
    """A separate, reviewable economic scenario; never masquerades as the case ZIP."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("economic_scenario.json", json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        for name, headers, rows in (
            ("asset_scenario.csv", ["asset_id", "name", "p_flood", "value_rub", "vulnerability", "expected_loss_rub", "rank", "status"], result["assets"]),
            ("strategy_scenario.csv", ["strategy", "data_cost_rub", "other_cost_rub", "decision_cost_rub", "budget_feasible", "covered_expected_loss_rub", "coverage_share", "zone_ids"],
             list(result["scenario"]["strategies"].values())),
            ("zone_decisions.csv", ["zone_id", "selected", "reason", "price_alone_rub", "expected_loss_rub", "doubt_share"],
             result["scenario"]["zone_decisions"]),
        ):
            text = io.StringIO()
            writer = csv.writer(text)
            writer.writerow(headers)
            for row in rows:
                writer.writerow([";".join(value) if isinstance(value, list) else row.get(key) for key in headers
                                 for value in [row.get(key)]])
            archive.writestr(name, text.getvalue())
        archive.writestr("README.txt", "Отдельный сценарный расчёт. Обязательные десять объектов и ZIP кейса не меняются.\n"
                         "Параметры, формула и все строки корзин B/C записаны в economic_scenario.json.\n"
                         "Покрытый ожидаемый ущерб не означает предотвращённый ущерб.\n")
    return output.getvalue()
