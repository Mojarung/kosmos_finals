import csv
import io
import zipfile
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.costs import ANALYST_RATE_RUB_H, COMPUTE_RUB, MAP_REVIEW_H, line_hours
from app.main import app

validator = pytest.importorskip("test_planning").validator
client = TestClient(app)


def _analysis(deadline=8, budget=6500):
    return client.get("/api/analysis", params={"scene_id": "Nigeria_812045", "budget": budget,
                                               "deadline": deadline}).json()


def _expected_other(strategy: dict) -> float:
    hours = MAP_REVIEW_H + sum((line_hours(line) for line in strategy.get("quote", {}).get("lines", [])),
                               Decimal(0))
    return float((hours * ANALYST_RATE_RUB_H + COMPUTE_RUB).quantize(Decimal("0.01")))


@pytest.mark.parametrize("deadline", [8, 4])
def test_decision_cost_is_data_plus_disclosed_other_costs(deadline):
    strategies = _analysis(deadline)["strategies"]
    for key in "ABC":
        item = strategies[key]
        assert item["other_cost_rub"] == _expected_other(item)
        assert item["decision_cost_rub"] == round(item["data_cost_rub"] + item["other_cost_rub"], 2)
        assert item["other_costs"]["status"] == "scenario"
        assert item["other_costs"]["field_rub"] is None and item["other_costs"]["field_status"] == "not_estimated"
    assert strategies["A"]["other_cost_rub"] > 0


def test_radar_l1_orders_add_processing_time():
    at_8, at_4 = _analysis(8)["strategies"]["C"], _analysis(4)["strategies"]["C"]
    assert at_4["other_costs"]["processing_rub"] > 0 == at_8["other_costs"]["processing_rub"]


def test_residual_uncertainty_of_b_and_c_is_a_labelled_scenario():
    strategies = _analysis(8)["strategies"]
    baseline = strategies["A"]["residual_uncertainty"]
    assert strategies["A"]["uncertainty_status"] == "baseline"
    for key in "BC":
        item = strategies[key]
        assert item["uncertainty_status"] == "scenario"
        assert "сумма шансов внутри" in item["uncertainty_basis"]
        assert 0 <= item["residual_uncertainty"] < baseline
        assert item["residual_uncertainty"] == round(baseline * (1 - item["covered_chance_share"]), 5)


def test_export_discloses_other_costs_and_the_validator_checks_the_sum(tmp_path):
    response = client.get("/api/export", params={"scene_id": "Nigeria_812045", "budget": 6500})
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    rows = list(csv.DictReader(io.StringIO(files["strategy_comparison.csv"].decode())))
    for row in rows:
        assert float(row["decision_cost_rub"]) == pytest.approx(
            float(row["data_cost_rub"]) + float(row["other_cost_rub"]), abs=0.011)
        assert row["other_cost_status"] == "scenario"
    assert {row["strategy"]: row["uncertainty_status"] for row in rows} == {
        "A": "baseline", "B": "scenario", "C": "scenario"}
    path = tmp_path / "ok.zip"
    path.write_bytes(response.content)
    assert validator.validate(path)["status"] == "ok"

    rows[2]["decision_cost_rub"] = str(float(rows[2]["decision_cost_rub"]) + 100)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    files["strategy_comparison.csv"] = output.getvalue().encode()
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(tampered, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    with pytest.raises(ValueError, match="decision cost"):
        validator.validate(tampered)
