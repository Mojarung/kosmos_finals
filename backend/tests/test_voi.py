import io
import json
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.analysis import context
from app.main import app
from app.planning import quote
from app.voi import asset_information_value, calculate


@pytest.mark.parametrize("p,exposure,cost,expected", [
    (0, 1_000_000, 10_000, 0),
    (1, 1_000_000, 10_000, 0),
    (.2, 100_000, 10_000, 8_000),
    (.8, 100_000, 10_000, 2_000),
    (.5, 1_000, 10_000, 0),
])
def test_value_of_information_is_avoidable_decision_cost(p, exposure, cost, expected):
    assert asset_information_value(p, exposure, cost)["voi_rub"] == expected


def test_exact_global_basket_and_overlap_accounting():
    result = calculate("Nigeria_812045", None, 10_000, 8, None)
    space = context("Nigeria_812045", None).space
    values = np.array([next(row["voi_rub"] or 0 for row in result["assets"] if row["asset_id"] == asset["id"])
                       for asset in space.assets])
    benefit = np.round(space.asset_covered @ values, 2)
    prices = space.prices(1, 8)
    for subset in space.subsets:
        assert prices[subset] == pytest.approx(quote(space.orders(int(subset), 8))["total_rub"], abs=.005)
    timely = (space.subsets & space.late(8)) == 0
    best_net = max(0, float(np.max((benefit - prices)[timely])))
    assert result["plan"]["net_after_data_rub"] == pytest.approx(best_net, abs=.01)
    index = space.index(result["plan"]["zone_ids"])
    assert result["plan"]["voi_rub"] == pytest.approx(benefit[index])
    assert result["plan"]["derived_budget_rub"] == quote(space.orders(index, 8))["total_rub"]
    assert result["plan"]["voi_rub"] <= sum(values)
    assert result["plan"]["derived_budget_rub"] != 6500  # budget is derived from positive net benefit
    assert all(row["voi_alone_rub"] is not None and row["price_alone_rub"] >= 0 for row in result["zones"])


def test_no_positive_purchase_and_unreachable_required_asset():
    free = calculate("Nigeria_812045", None, 0, 8, None)
    assert free["plan"]["zone_ids"] == []
    assert free["plan"]["derived_budget_rub"] == 0
    late = calculate("Nigeria_812045", None, 10_000, 1, "A01")
    assert late["plan"]["zone_ids"] == []
    assert late["plan"]["constraints_feasible"] is False


def test_api_and_download_include_scenario_and_reproducible_zone_values():
    client = TestClient(app)
    response = client.get("/api/voi", params={"scene_id": "Nigeria_812045", "visit_cost": 10000})
    assert response.status_code == 200
    data = response.json()
    assert data["scenario"]["visit_cost_status"] == "operator_scenario"
    assert data["plan"]["other_costs"]["status"] == "scenario"
    zipped = client.get("/api/voi/export", params={"scene_id": "Nigeria_812045", "visit_cost": 10000})
    assert zipped.status_code == 200
    with zipfile.ZipFile(io.BytesIO(zipped.content)) as archive:
        exported = json.loads(archive.read("value_of_information.json"))
        assert exported["plan"]["zone_ids"] == data["plan"]["zone_ids"]
        csv_text = archive.read("zone_value.csv").decode()
        assert "voi_alone_rub" in csv_text and "ZALL" in csv_text
