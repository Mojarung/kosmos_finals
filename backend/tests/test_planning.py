import io
import json
import zipfile

from fastapi.testclient import TestClient

from app.analysis import analyze
from app.catalog import metadata
from app.main import app
from app.planning import candidates, covered_assets, quote, rectangle_area_km2


def test_case_control_price_and_basket_discount():
    one_km = {"id": "control", "area_km2": 1.0}
    assert quote([one_km])["total_rub"] == 1703.46
    assert quote([one_km, one_km])["discount_coef"] < quote([one_km])["discount_coef"]


def test_zones_and_budget_plan_are_consistent():
    info = metadata()
    zones = candidates(info["assets"], info["bbox"])
    assert len(zones) == 11
    assert all(rectangle_area_km2(z["bounds"]) >= 1 for z in zones)
    result = analyze(None, 6500, "risk", 8, None)
    selected = [z for z in result["zones"] if z["id"] in result["strategies"]["C"]["zone_ids"]]
    covered = covered_assets(selected, result["assets"])
    assert result["strategies"]["C"]["data_cost_rub"] <= 6500
    assert result["strategies"]["C"]["covered_expected_loss_rub"] == round(sum(a["expected_loss_rub"] for a in covered), 2)
    assert result["strategies"]["B"]["data_cost_rub"] > result["strategies"]["C"]["data_cost_rub"]


def test_arbitrary_region_without_demo_data_is_not_misreported():
    client = TestClient(app)
    response = client.get("/api/analysis", params={"bbox": "30,50,31,51"})
    assert response.status_code == 200
    result = response.json()
    assert result["region"]["data_status"] == "outside_demo_coverage"
    assert result["region"]["water_area_km2"] is None
    assert result["assets"] == []
    assert result["strategies"]["C"]["data_cost_rub"] == 0

    inside = client.get("/api/analysis", params={"bbox": "-1.467,9.304,-1.439,9.326"}).json()
    broad = next(zone for zone in inside["zones"] if zone["id"] == "ZALL")
    assert abs(broad["area_km2"] - inside["region"]["area_km2"]) < 0.001


def test_manual_label_metrics_and_export_status():
    client = TestClient(app)
    result = client.get("/api/analysis").json()
    assert result["chip"]["water_pixels"] == 14933
    assert result["region"]["water_area_km2"] == 1.464
    exported = client.get("/api/export")
    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        region = json.loads(archive.read("selected_region.geojson"))
    assert region["features"][0]["properties"]["data_status"] == "covered"
