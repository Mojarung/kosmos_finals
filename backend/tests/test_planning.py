import csv
import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.analysis import analyze
from app.catalog import metadata
from app.main import app
from app.planning import candidates, covered_assets, quote, rectangle_area_km2
from app.scene_builder import PROBABILITY_RULE

validator_spec = importlib.util.spec_from_file_location(
    "validate_package", Path(__file__).resolve().parents[2] / "scripts" / "validate_package.py")
validator = importlib.util.module_from_spec(validator_spec)
validator_spec.loader.exec_module(validator)


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
    assert all(abs(a["expected_loss_rub"] - a["p_flood"] * a["value_rub"] * a["vulnerability"]) < 0.02
               for a in result["assets"])


def test_arbitrary_region_without_demo_data_is_not_misreported():
    client = TestClient(app)
    response = client.get("/api/analysis", params={"bbox": "30,50,31,51"})
    assert response.status_code == 200
    result = response.json()
    assert result["region"]["data_status"] == "outside_coverage"
    assert result["region"]["model_water_area_km2"] is None
    assert result["assets"] == []
    assert result["strategies"]["C"]["data_cost_rub"] == 0

    inside = client.get("/api/analysis", params={"bbox": "5.801,9.067,5.83,9.096"}).json()
    broad = next(zone for zone in inside["zones"] if zone["id"] == "ZALL")
    assert abs(broad["area_km2"] - inside["region"]["area_km2"]) < 0.001


def test_frozen_model_metrics_and_case_export():
    client = TestClient(app)
    result = client.get("/api/analysis").json()
    assert result["chip"]["chip_id"] == "Nigeria_812045"
    assert result["region"]["model_water_area_km2"] == 14.796
    assert result["region"]["model_quality"]["iou"] > result["region"]["baseline_quality"]["iou"]
    assert result["quality"]["parts"]["test_core"]["model"]["iou"] > result["quality"]["parts"]["test_core"]["baseline"]["iou"]
    assert result["quality"]["parts"]["test_core"]["chips"] == 55
    exported = client.get("/api/export")
    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        region = json.loads(archive.read("selected_region.geojson"))
        names = set(archive.namelist())
        assets = archive.read("asset_loss.csv").decode()
    assert region["features"][0]["properties"]["data_status"] == "covered"
    assert {"flood_probability.tif", "flood_mask.tif", "baseline_mask.tif",
            "assets.geojson", "asset_loss.csv", "procurement_plan.csv",
            "strategy_comparison.csv", "run_metadata.json"} <= names
    assert len(assets.splitlines()) == 11

    clipped = client.get("/api/export", params={"bbox": "5.801,9.067,5.83,9.096"})
    with zipfile.ZipFile(io.BytesIO(clipped.content)) as archive:
        rows = list(csv.DictReader(io.StringIO(archive.read("asset_loss.csv").decode())))
    assert len(rows) == 10
    assert any(row["in_region"] == "false" for row in rows)

    impossible = client.get("/api/analysis", params={"budget": 2000, "must_cover": "A10"}).json()
    assert impossible["strategies"]["C"]["budget_feasible"] is True
    assert impossible["strategies"]["C"]["constraints_feasible"] is False
    assert impossible["metrics"]["data_savings_vs_broad_rub"] is None


def test_sensitivity_pixel_and_submission_validation(tmp_path):
    client = TestClient(app)
    result = client.get("/api/analysis").json()
    assert len(result["sensitivity"]["asset"]) == 60
    assert len(result["sensitivity"]["plan"]) == 6
    assert result["decision_evidence"]["priority_changes_order"] is True
    assert result["asset_proxy_check"]["n_assets"] > 0
    pixel = client.get("/api/pixel", params={"scene_id": "Nigeria_812045",
                                            "longitude": 5.81, "latitude": 9.08}).json()
    assert pixel["status"] == "ok"
    assert pixel["model_water"] == (pixel["p_flood"] >= pixel["model_threshold"])
    assert client.get("/api/pixel", params={"longitude": 31, "latitude": 52}).json()["status"] == "outside_coverage"

    zip_path = tmp_path / "case.zip"
    zip_path.write_bytes(client.get("/api/export").content)
    report = validator.validate(zip_path)
    assert report["status"] == "ok"
    assert report["assets"] == 10


def test_export_documents_pixel_probability_rule():
    client = TestClient(app)
    with zipfile.ZipFile(io.BytesIO(client.get("/api/export").content)) as archive:
        meta = json.loads(archive.read("run_metadata.json"))
        header = archive.read("asset_loss.csv").decode().splitlines()[0].split(",")
    assert meta["probability_rule"] == PROBABILITY_RULE
    assert "p_window3" in header


def test_validator_rejects_p_not_read_at_asset_pixel(tmp_path):
    client = TestClient(app)
    with zipfile.ZipFile(io.BytesIO(client.get("/api/export").content)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    rows = list(csv.DictReader(io.StringIO(files["asset_loss.csv"].decode())))
    assets = {row["asset_id"]: row for row in csv.DictReader(io.StringIO(files["assets.csv"].decode()))}
    target = next(row for row in rows if row["status"] == "ok")
    tampered = 0.5 if float(target["p_flood"]) < 0.4 else 0.1
    asset = assets[target["asset_id"]]
    target["p_flood"] = str(tampered)
    target["expected_loss_rub"] = str(round(
        tampered * float(asset["asset_value_rub"]) * float(asset["vulnerability_coef"]), 2))
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    files["asset_loss.csv"] = output.getvalue().encode()
    path = tmp_path / "tampered.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    with pytest.raises(ValueError, match="asset pixel"):
        validator.validate(path)
