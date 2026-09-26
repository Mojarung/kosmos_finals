"""Reproducible one-factor scenarios over a fixed chip and asset portfolio."""

from __future__ import annotations

import numpy as np

from .planning import BasketSpace


def _loss(asset: dict, values: dict) -> float:
    return round(values["p"] * values["V"] * values["q"], 2)


def _rank(losses: dict[str, float], asset_id: str) -> int:
    """1 for the largest loss, ties by asset_id."""
    return sorted(losses, key=lambda item_id: (-losses[item_id], item_id)).index(asset_id) + 1


def asset_sensitivity(assets: list[dict]) -> list[dict]:
    """Vary one input at a time; all other assets and inputs stay fixed.

    Both rankings follow the mandatory EL = p·V·q used by the plan.
    """
    rows = []
    eligible = [asset for asset in assets if asset["p_flood"] is not None]
    baseline = {asset["id"]: index for index, asset in enumerate(
        sorted(eligible, key=lambda item: (-item["expected_loss_rub"], item["id"])), 1)}
    case_losses = {item["id"]: item["case_expected_loss_rub"] for item in eligible}
    for asset in eligible:
        for field, key in (("p", "p_flood"), ("V", "value_rub"), ("q", "vulnerability")):
            for factor in (0.8, 1.2):
                value = asset[key] * factor
                if field in {"p", "q"}:
                    value = min(1, value)
                values = {"p": asset["p_flood"], "V": asset["value_rub"], "q": asset["vulnerability"]}
                values[field] = value
                loss = _loss(asset, values)
                losses = {item["id"]: item["expected_loss_rub"] for item in eligible}
                losses[asset["id"]] = loss
                rank = _rank(losses, asset["id"])
                case_loss = round(values["p"] * values["V"] * values["q"], 2)
                rows.append({"asset_id": asset["id"], "input": field,
                             "factor": factor, "input_value": round(value, 6),
                             "expected_loss_rub": loss, "rank": rank,
                             "baseline_rank": baseline[asset["id"]],
                             "rank_change": baseline[asset["id"]] - rank,
                             "case_expected_loss_rub": case_loss,
                             "case_rank": _rank({**case_losses, asset["id"]: case_loss}, asset["id"]),
                             "case_baseline_rank": _rank(case_losses, asset["id"])})
    return rows


def asset_plan_sensitivity(space: BasketSpace, rows: list[dict], budget_rub: float, priority: str,
                           deadline_hours: int, must_cover: str | None) -> list[dict]:
    """Re-optimise plan C for every asset scenario of asset_sensitivity."""
    ids = [asset["id"] for asset in space.assets]
    result = []
    for row in rows:
        losses = space.losses.copy()
        losses[ids.index(row["asset_id"])] = row["expected_loss_rub"]
        result.append(space.plan(budget_rub, priority, deadline_hours, must_cover, losses=np.asarray(losses)))
    return result


def plan_sensitivity(space: BasketSpace, budget_rub: float, priority: str,
                     deadline_hours: int, must_cover: str | None) -> list[dict]:
    rows = []
    for input_name in ("budget", "tariff"):
        for factor in (0.8, 1.0, 1.2):
            budget = round(budget_rub * factor, 2) if input_name == "budget" else budget_rub
            multiplier = factor if input_name == "tariff" else 1.0
            selected = space.plan(budget, priority, deadline_hours, must_cover, tariff=multiplier)
            rows.append({"input": input_name, "factor": factor,
                         "budget_rub": budget, "tariff_multiplier": multiplier,
                         "zone_ids": selected["zone_ids"],
                         "data_cost_rub": selected["quote"]["total_rub"],
                         "covered_expected_loss_rub": selected["covered_expected_loss_rub"],
                         "covered_doubt_share": selected["covered_doubt_share"],
                         "covered_asset_ids": selected["covered_asset_ids"],
                         "feasible": selected["feasible"]})
    return rows
