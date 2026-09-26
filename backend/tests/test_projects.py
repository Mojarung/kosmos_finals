"""Operator imports remain separate from the fixed ten-asset case experiment."""

from fastapi.testclient import TestClient

from app import projects
from app.main import app
from app import live
import json


def test_import_keeps_sources_and_saved_decision_is_immutable(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "DB", tmp_path / "projects.sqlite3")
    client = TestClient(app)
    created = client.post("/api/projects", json={"name": "Оператор", "scene_id": "Nigeria_812045"})
    assert created.status_code == 201
    project_id = created.json()["id"]
    original = client.get("/api/analysis", params={"scene_id": "Nigeria_812045"}).json()
    asset = original["assets"][0]
    csv_text = ("name,longitude,latitude,value_rub,vulnerability,value_source,source_date\n"
                f"Реальный склад,{asset['longitude']},{asset['latitude']},2000000,0.3,Реестр оператора,2026-09-01\n")
    imported = client.post(f"/api/projects/{project_id}/assets/import", json={"format": "csv", "content": csv_text})
    assert imported.status_code == 200 and imported.json()["estimated"] == 1
    detail = client.get(f"/api/projects/{project_id}").json()
    assert detail["assets"][0]["expected_loss_rub"] == round(asset["p_flood"] * 2_000_000 * .3, 2)
    assert detail["assets"][0]["value_source"] == "Реестр оператора"
    snapshot = client.post(f"/api/projects/{project_id}/decisions", json={"budget": 6500})
    assert snapshot.status_code == 201 and snapshot.json()["asset_count"] == 1
    assert snapshot.json()["metrics"]["portfolio_count"] == 10
    # A later inventory replacement must not rewrite the archived decision.
    replacement = csv_text.replace("2000000", "4000000")
    assert client.post(f"/api/projects/{project_id}/assets/import", json={"format": "csv", "content": replacement}).status_code == 200
    archived = client.get(f"/api/projects/{project_id}").json()["decisions"][0]
    assert archived["asset_snapshot"][0]["value_rub"] == 2_000_000


def test_import_rejects_missing_cost_source_without_erasing_inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "DB", tmp_path / "projects.sqlite3")
    client = TestClient(app)
    project_id = client.post("/api/projects", json={"name": "Проверка", "scene_id": "Nigeria_812045"}).json()["id"]
    valid = "name,longitude,latitude,value_rub,vulnerability,value_source\nA,5.8,9.08,100,0.2,Инвентаризация\n"
    assert client.post(f"/api/projects/{project_id}/assets/import", json={"format": "csv", "content": valid}).status_code == 200
    invalid = valid.replace("Инвентаризация", "")
    assert client.post(f"/api/projects/{project_id}/assets/import", json={"format": "csv", "content": invalid}).status_code == 422
    assert len(client.get(f"/api/projects/{project_id}").json()["assets"]) == 1


def test_imported_assets_drive_a_separate_budgeted_plan_and_immutable_history(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "DB", tmp_path / "projects.sqlite3")
    client = TestClient(app)
    project_id = client.post("/api/projects", json={"name": "Свои объекты", "scene_id": "Nigeria_812045"}).json()["id"]
    point = client.get("/api/analysis", params={"scene_id": "Nigeria_812045"}).json()["assets"][0]
    source = ("name,longitude,latitude,value_rub,vulnerability,value_source\n"
              f"Склад,{point['longitude']},{point['latitude']},2000000,0.3,Реестр\n"
              f"Подстанция,{point['longitude']},{point['latitude']},4000000,0.4,Реестр\n")
    assert client.post(f"/api/projects/{project_id}/assets/import", json={"format": "csv", "content": source}).status_code == 200
    preview = client.post(f"/api/projects/{project_id}/plan", json={"budget": 6500, "deadline": 8})
    assert preview.status_code == 200
    plan = preview.json()
    assert plan["scope"] == "imported_operator_assets_separate_from_fixed_case_portfolio"
    assert plan["asset_count"] == 2 and plan["estimated_asset_count"] == 2
    assert plan["anchor_count"] == 2
    assert plan["strategies"]["C"]["budget_feasible"]
    assert plan["strategies"]["C"]["covered_expected_loss_rub"] <= plan["expected_loss_rub"]
    assert len(plan["strategies"]["C"]["covered_asset_ids"]) <= 2
    assert plan["strategies"]["C"]["decision_cost_rub"] >= plan["strategies"]["C"]["data_cost_rub"]
    assert client.get("/api/analysis", params={"scene_id": "Nigeria_812045"}).json()["metrics"]["portfolio_count"] == 10
    saved = client.post(f"/api/projects/{project_id}/plans", json={"budget": 6500, "deadline": 8})
    assert saved.status_code == 201
    changed = source.replace("2000000", "8000000")
    assert client.post(f"/api/projects/{project_id}/assets/import", json={"format": "csv", "content": changed}).status_code == 200
    history = client.get(f"/api/projects/{project_id}").json()["operator_plans"]
    assert len(history) == 1
    assert any(asset["value_rub"] == 2_000_000 for asset in history[0]["asset_snapshot"])


def test_live_grid_resolution_and_cached_reference_match(tmp_path):
    small = [5.80, 9.08, 5.82, 9.10]
    large = [5.80, 9.08, 5.84, 9.12]
    sw, sh = live.grid_size(small)
    lw, lh = live.grid_size(large)
    assert lw > sw and lh > sh
    assert 8 <= live.GEOD.inv(small[0], small[1], small[2], small[1])[2] / sw <= 10
    folder = tmp_path / "live_scene"
    folder.mkdir()
    (folder / "metadata.json").write_text(json.dumps({"width": sw, "height": sh}))
    (folder / "water_mask.bin").write_bytes(bytes(512 * 512))
    assert live._cached_scene(folder) == {"width": sw, "height": sh}
    assert (folder / "water_mask.bin").stat().st_size == sw * sh
