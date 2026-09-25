"""Reproducible one-factor scenarios over a fixed chip and asset portfolio."""

from __future__ import annotations

from .planning import plan


def asset_sensitivity(assets: list[dict]) -> list[dict]:
    """Vary one input at a time; all other assets and inputs stay fixed."""
    rows = []
    eligible = [asset for asset in assets if asset["p_flood"] is not None]
    baseline = {asset["id"]: index for index, asset in enumerate(
        sorted(eligible, key=lambda item: (-item["expected_loss_rub"], item["id"])), 1)}
    for asset in assets:
        if asset["p_flood"] is None:
            continue
        for field, key in (("p", "p_flood"), ("V", "value_rub"), ("q", "vulnerability")):
            for factor in (0.8, 1.2):
                value = asset[key] * factor
                if field in {"p", "q"}:
                    value = min(1, value)
                values = {"p": asset["p_flood"], "V": asset["value_rub"],
                          "q": asset["vulnerability"]}
                values[field] = value
                loss = round(values["p"] * values["V"] * values["q"], 2)
                losses = {item["id"]: item["expected_loss_rub"] for item in eligible}
                losses[asset["id"]] = loss
                rank = sorted(losses, key=lambda item_id: (-losses[item_id], item_id)).index(asset["id"]) + 1
                rows.append({"asset_id": asset["id"], "input": field,
                             "factor": factor, "input_value": round(value, 6),
                             "expected_loss_rub": loss, "rank": rank,
                             "baseline_rank": baseline[asset["id"]],
                             "rank_change": baseline[asset["id"]] - rank})
    return rows


def plan_sensitivity(assets: list[dict], zones: list[dict], budget_rub: float,
                     priority: str, deadline_hours: int, must_cover: str | None) -> list[dict]:
    rows = []
    for input_name in ("budget", "tariff"):
        for factor in (0.8, 1.0, 1.2):
            budget = round(budget_rub * factor, 2) if input_name == "budget" else budget_rub
            multiplier = factor if input_name == "tariff" else 1.0
            selected = plan(assets, zones, budget, priority, deadline_hours,
                            must_cover, tariff_multiplier=multiplier)
            rows.append({"input": input_name, "factor": factor,
                         "budget_rub": budget, "tariff_multiplier": multiplier,
                         "zone_ids": selected["zone_ids"],
                         "data_cost_rub": selected["quote"]["total_rub"],
                         "covered_expected_loss_rub": selected["covered_expected_loss_rub"],
                         "covered_asset_ids": selected["covered_asset_ids"],
                         "feasible": selected["feasible"]})
    return rows
