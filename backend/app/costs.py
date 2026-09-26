"""Other costs of a decision, disclosed next to the data price.

The case writes the full cost as C_decision = C_data + C_compute + C_review + C_field +
C_action_errors and asks to keep measured articles and scenarios apart from the data price,
never folding labour into the tariff. Everything here is a scenario with its basis written
out; field visits and the cost of wrong actions have no model and stay not_estimated.
The data budget still limits data_cost_rub only.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

MONEY = Decimal("0.01")
ANALYST_RATE_RUB_H = Decimal("1500")         # scenario: GIS analyst hour with overheads, not a quote
MAP_REVIEW_H = Decimal("1")                   # reading the flood map and the ten objects, every strategy
IMAGE_REVIEW_H_PER_KM2 = Decimal("0.2")       # interpreting one delivered image of an ordered zone...
IMAGE_REVIEW_MIN_H = Decimal("0.25")          # ...but never less than this per order line
SAR_PROCESSING_H_PER_KM2 = Decimal("0.1")     # calibration and terrain correction of an L0/L1 radar order...
SAR_PROCESSING_MIN_H = Decimal("0.25")        # ...per line, at least
CPU_SECONDS_PER_SCENE = Decimal("20")         # measured 25.09.2026: frozen ensemble on one chip, laptop CPU
CPU_RATE_RUB_H = Decimal("10")                # scenario: one cloud CPU hour
COMPUTE_RUB = (CPU_SECONDS_PER_SCENE / Decimal(3600) * CPU_RATE_RUB_H).quantize(MONEY, rounding=ROUND_HALF_UP)

BASIS = ("scenario: compute = measured 20 s CPU per scene x 10 RUB/h; review = (1 h map review + per order line "
         "max(0.25 h, 0.2 h/km2)) x 1500 RUB/h; processing = per L0/L1 radar line max(0.25 h, 0.1 h/km2) x 1500 RUB/h; "
         "field visits and wrong-action costs not estimated")
RULE = {
    "formula": "decision_cost_rub = data_cost_rub + other_cost_rub; other = compute + review + processing",
    "status": "scenario", "budget_limits": "data_cost_rub only",
    "analyst_rate_rub_h": float(ANALYST_RATE_RUB_H), "map_review_h": float(MAP_REVIEW_H),
    "image_review_h_per_km2": float(IMAGE_REVIEW_H_PER_KM2), "image_review_min_h": float(IMAGE_REVIEW_MIN_H),
    "sar_processing_h_per_km2": float(SAR_PROCESSING_H_PER_KM2), "sar_processing_min_h": float(SAR_PROCESSING_MIN_H),
    "cpu_seconds_per_scene": float(CPU_SECONDS_PER_SCENE), "cpu_seconds_status": "measured 2026-09-25, laptop CPU",
    "cpu_rate_rub_h": float(CPU_RATE_RUB_H),
    "not_estimated": ["field visits", "cost of wrong actions"],
}


def review_hours(line: dict) -> Decimal:
    return max(IMAGE_REVIEW_MIN_H, IMAGE_REVIEW_H_PER_KM2 * Decimal(str(line["area_km2"])))


def processing_hours(line: dict) -> Decimal:
    if line.get("sensor_type") != "sar" or line.get("processing_level") not in {"L0", "L1"}:
        return Decimal(0)
    return max(SAR_PROCESSING_MIN_H, SAR_PROCESSING_H_PER_KM2 * Decimal(str(line["area_km2"])))


def line_hours(line: dict) -> Decimal:
    return review_hours(line) + processing_hours(line)


def decision_costs(data_cost_rub: float, lines: list[dict]) -> dict:
    """Other cost articles of one strategy and its full decision cost."""
    review_h = MAP_REVIEW_H + sum((review_hours(line) for line in lines), Decimal(0))
    processing_h = sum((processing_hours(line) for line in lines), Decimal(0))
    review = (review_h * ANALYST_RATE_RUB_H).quantize(MONEY, rounding=ROUND_HALF_UP)
    processing = (processing_h * ANALYST_RATE_RUB_H).quantize(MONEY, rounding=ROUND_HALF_UP)
    other = COMPUTE_RUB + review + processing
    decision = (Decimal(str(data_cost_rub)) + other).quantize(MONEY, rounding=ROUND_HALF_UP)
    return {
        "other_cost_rub": float(other), "decision_cost_rub": float(decision),
        "other_costs": {
            "compute_rub": float(COMPUTE_RUB), "review_rub": float(review), "processing_rub": float(processing),
            "review_hours": float(review_h), "processing_hours": float(processing_h),
            "field_rub": None, "field_status": "not_estimated",
            "action_errors_rub": None, "action_errors_status": "not_estimated",
            "status": "scenario", "basis": BASIS,
        },
    }
