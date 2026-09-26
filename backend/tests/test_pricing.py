"""PP RF 840 price engine: coefficient tables, both discount formulas and discount groups per basket."""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest

from app import planning

CALC = date(2026, 9, 26)
OPTICAL = {"sensor_type": "optical", "resolution_m": 1, "processing_level": "L2", "usage_type": "internal"}
SAR = {"sensor_type": "sar", "resolution_m": 3, "processing_level": "L1", "usage_type": "internal"}
NEW = {"acquisition_type": "new", "guaranteed_purchase": True, "observation_at": None}
NINE = Decimal("0.000000001")
KOPECK = Decimal("0.01")


def existing(days: int, kind: str) -> dict:
    """Imagery that already exists, acquired `days` calendar days before the calculation date."""
    return {"acquisition_type": kind, "guaranteed_purchase": False,
            "observation_at": (CALC - timedelta(days=days)).isoformat()}


def order(candidate_id: str, area: float, product: dict, acquisition: dict) -> dict:
    return {"id": candidate_id, "area_km2": area, **product, **acquisition}


def price(orders: list[dict], **kwargs) -> dict:
    return planning.quote(orders, calculation_date=CALC, **kwargs)


def test_case_control_arithmetic():
    """Б 788,64; К 1,000; optics 1 m; S 1,000; L2; internal: new 1 703,46, operational 946,37, archive 567,82."""
    new = price([order("N", 1.0, OPTICAL, NEW)])
    line = new["lines"][0]
    assert new["total_rub"] == line["cost_rub"] == line["unit_price_rub_km2"] == 1703.46
    assert (line["base_rate_rub_km2"], line["processing_coef"], line["usage_coef"],
            line["freshness_coef"], line["discount_coef"]) == (788.64, 1.2, 1.0, 1.8, 1.0)
    assert line["discount_group_id"] == "C_optical_1m_2026" and line["group_area_km2"] == 1.0
    assert line["base_rate_status"] == "scenario" and line["formula"] == "B*K*O*P*T*R"
    assert line["legal_edition"] == planning.LEGAL_EDITION and "0.058" in line["discount_formula"]
    assert price([order("O", 1.0, OPTICAL, existing(30, "operational"))])["total_rub"] == 946.37
    assert price([order("A", 1.0, OPTICAL, existing(400, "archive"))])["total_rub"] == 567.82


def test_ninety_days_is_still_operational_and_ninety_one_is_archive():
    assert planning.freshness_coef(existing(90, "operational"), CALC) == Decimal("1")
    assert planning.freshness_coef(existing(91, "archive"), CALC) == Decimal("0.6")
    day_90 = price([order("D90", 1.0, OPTICAL, existing(90, "operational"))])["lines"][0]
    day_91 = price([order("D91", 1.0, OPTICAL, existing(91, "archive"))])["lines"][0]
    assert (day_90["age_days"], day_90["freshness_coef"], day_90["cost_rub"]) == (90, 1.0, 946.37)
    assert (day_91["age_days"], day_91["freshness_coef"], day_91["cost_rub"]) == (91, 0.6, 567.82)


@pytest.mark.parametrize("acquisition", [
    existing(91, "operational"),                               # the label contradicts the age
    existing(90, "archive"),
    existing(-1, "operational"),                               # acquired after the calculation date
    {**existing(30, "operational"), "guaranteed_purchase": True},  # existing imagery is never guaranteed
    {"acquisition_type": "operational", "guaranteed_purchase": False, "observation_at": None},
    {"acquisition_type": "new", "guaranteed_purchase": False, "observation_at": None},
    {"acquisition_type": "tasked", "guaranteed_purchase": True, "observation_at": None},
])
def test_freshness_rejects_inconsistent_orders(acquisition):
    with pytest.raises(ValueError):
        planning.freshness_coef(acquisition, CALC)


def test_radar_uses_its_own_discount_curve_and_resolution():
    ratio = Decimal(100) / Decimal(3) ** 2
    expected = (Decimal("-0.020") * ratio.ln() + Decimal("1.031")).quantize(NINE, ROUND_HALF_UP)
    assert Decimal("0.7") < expected < 1
    assert planning.discount_coef("sar", Decimal(100), 3) == expected
    quoted = price([order("S", 100.0, SAR, NEW)])
    line = quoted["lines"][0]
    unit = (Decimal("788.64") * Decimal(1) * Decimal(1) * Decimal("1.8") * expected).quantize(KOPECK, ROUND_HALF_UP)
    assert line["discount_coef"] == float(expected)
    assert line["processing_coef"] == 1.0 and line["unit_price_rub_km2"] == float(unit)
    assert quoted["total_rub"] == float((unit * 100).quantize(KOPECK, ROUND_HALF_UP))
    assert line["discount_group_id"] == "C_sar_3m_2026" and "0.020" in line["discount_formula"]
    optical = (Decimal("-0.058") * Decimal(100).ln() + Decimal("1.037")).quantize(NINE, ROUND_HALF_UP)
    assert planning.discount_coef("optical", Decimal(100), 1) == optical == Decimal("0.769900129")
    assert planning.discount_coef("sar", Decimal(1), 3) == 1
    assert planning.discount_coef("sar", Decimal(10) ** 9, 1) == Decimal("0.7")
    assert planning.discount_coef("optical", Decimal(10) ** 9, 1) == Decimal("0.2")


def test_adding_an_order_changes_the_discount_of_its_own_group_only():
    base = [order("O1", 1.5, OPTICAL, NEW), order("O2", 1.5, OPTICAL, NEW), order("S1", 60.0, SAR, NEW)]

    def groups(orders: list[dict]) -> dict[str, dict]:
        return {group["discount_group_id"]: group for group in price(orders)["groups"]}

    before = groups(base)
    assert set(before) == {"C_optical_1m_2026", "C_sar_3m_2026"}
    assert before["C_optical_1m_2026"]["group_area_km2"] == 3.0 and before["C_sar_3m_2026"]["group_area_km2"] == 60.0
    assert before["C_optical_1m_2026"]["discount_coef"] < 1 and before["C_sar_3m_2026"]["discount_coef"] < 1
    more_optics = groups(base + [order("O3", 2.0, OPTICAL, NEW)])
    assert more_optics["C_optical_1m_2026"]["discount_coef"] < before["C_optical_1m_2026"]["discount_coef"]
    assert more_optics["C_sar_3m_2026"] == before["C_sar_3m_2026"]
    more_radar = groups(base + [order("S2", 30.0, SAR, NEW)])
    assert more_radar["C_sar_3m_2026"]["discount_coef"] < before["C_sar_3m_2026"]["discount_coef"]
    assert more_radar["C_optical_1m_2026"] == before["C_optical_1m_2026"]
    # Archive optics of the same resolution and order year joins the optical group; 0.5 m optics is its own group.
    archive = groups(base + [order("A1", 2.0, OPTICAL, existing(200, "archive"))])
    assert archive["C_optical_1m_2026"]["group_area_km2"] == 5.0
    finer = groups(base + [order("F1", 2.0, {**OPTICAL, "resolution_m": 0.5}, NEW)])
    assert finer["C_optical_0.5m_2026"]["group_area_km2"] == 2.0 and finer["C_optical_1m_2026"] == before["C_optical_1m_2026"]

    quoted = price(base)
    for line in quoted["lines"]:
        group = before[line["discount_group_id"]]
        assert (line["discount_coef"], line["group_area_km2"]) == (group["discount_coef"], group["group_area_km2"])
    assert quoted["total_rub"] == float(sum(Decimal(str(line["cost_rub"])) for line in quoted["lines"]))


def test_group_id_names_the_basket_and_earlier_orders_join_s():
    one = [order("N", 1.0, OPTICAL, NEW)]
    assert price(one, strategy="B")["lines"][0]["discount_group_id"] == "B_optical_1m_2026"
    later = planning.quote(one, calculation_date=date(2027, 1, 2))
    assert later["lines"][0]["discount_group_id"] == "C_optical_1m_2027"
    earlier = price(one, previous_group_area_km2={"optical_1m_2026": 9})
    line = earlier["lines"][0]
    assert (line["previous_group_area_km2"], line["basket_group_area_km2"], line["group_area_km2"]) == (9.0, 1.0, 10.0)
    assert line["discount_coef"] == float(planning.discount_coef("optical", Decimal(10), 1)) < 1
    assert earlier["total_rub"] < price(one)["total_rub"]


@pytest.mark.parametrize("area, resolution", [(0, 1), (-1, 1), (1, 0), (1, -3)])
def test_non_positive_area_or_resolution_is_invalid(area, resolution):
    for sensor in ("optical", "sar"):
        with pytest.raises(ValueError):
            planning.discount_coef(sensor, Decimal(area), resolution)


@pytest.mark.parametrize("change", [{"sensor_type": "lidar"}, {"processing_level": "L3"},
                                    {"usage_type": "public"}, {"resolution_m": 0}, {"area_km2": 0.9994}])
def test_orders_outside_the_tables_are_rejected(change):
    with pytest.raises(ValueError):
        price([{**order("X", 1.0, OPTICAL, NEW), **change}])


def test_area_is_rounded_half_up_to_a_thousandth():
    assert price([order("R", 1.0005, OPTICAL, NEW)])["lines"][0]["area_km2"] == 1.001
