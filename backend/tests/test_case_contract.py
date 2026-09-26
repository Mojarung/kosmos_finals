"""Case contract of the exported package: mandatory EL, reproduction data and source licenses."""

import csv
import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import catalog
from app.main import app
from test_planning import validator

SCENE = "Nigeria_812045"
ON_WATER = "A10"  # p ≈ 0.98: a flood-only rule would drop almost its whole EL


@pytest.fixture
def permanent_point(tmp_path, monkeypatch):
    """The demo scene with one asset marked as standing on JRC permanent water in its metadata only."""
    demo = tmp_path / "demo"
    demo.mkdir()
    for name in ("scenes.json", "quality.json"):
        shutil.copyfile(catalog.DATA_DIR / name, demo / name)
    shutil.copytree(catalog.DATA_DIR / SCENE, demo / SCENE)
    path = demo / SCENE / "metadata.json"
    info = json.loads(path.read_text(encoding="utf-8"))
    for asset in info["assets"]:
        if asset["id"] == ON_WATER:
            asset["permanent_water_at_point"] = True
    path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    monkeypatch.setattr(catalog, "DATA_DIR", demo)
    catalog.refresh()
    yield
    monkeypatch.undo()
    catalog.refresh()


def export(client: TestClient, **params) -> dict[str, bytes]:
    response = client.get("/api/export", params={"scene_id": SCENE, **params})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def table(files: dict[str, bytes], name: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(files[name].decode("utf-8"))))


def pack(files: dict[str, bytes], path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def mandatory_el(files: dict[str, bytes]) -> dict[str, float]:
    """p·V·q from the package's own p_flood, V and q, independent of the exported EL column."""
    assets = {row["asset_id"]: row for row in table(files, "assets.csv")}
    return {row["asset_id"]: round(float(row["p_flood"]) * float(assets[row["asset_id"]]["asset_value_rub"])
                                   * float(assets[row["asset_id"]]["vulnerability_coef"]), 2)
            for row in table(files, "asset_loss.csv") if row["p_flood"]}


def test_point_on_permanent_water_keeps_the_mandatory_el(permanent_point, tmp_path):
    client = TestClient(app)
    files = export(client)
    el = mandatory_el(files)
    losses = {row["asset_id"]: row for row in table(files, "asset_loss.csv")}
    point = losses[ON_WATER]
    assert point["permanent_water_at_point"] == "True"
    assert point["status"] == "ok"
    assert float(point["expected_loss_rub"]) == pytest.approx(el[ON_WATER], abs=0.011)
    assert float(point["flood_expected_loss_rub"]) == 0
    for asset_id, row in losses.items():
        assert row["status"] == "ok", asset_id
        assert float(row["expected_loss_rub"]) == pytest.approx(el[asset_id], abs=0.011), asset_id
        flood = 0.0 if asset_id == ON_WATER else el[asset_id]
        assert float(row["flood_expected_loss_rub"]) == pytest.approx(flood, abs=0.011), asset_id
    by_rank = sorted(losses, key=lambda asset_id: int(losses[asset_id]["rank"]))
    assert by_rank == sorted(el, key=lambda asset_id: (-el[asset_id], asset_id))
    assert validator.validate(pack(files, tmp_path / "permanent.zip"))["status"] == "ok"

    shown = client.get("/api/analysis", params={"scene_id": SCENE}).json()
    point = next(asset for asset in shown["assets"] if asset["id"] == ON_WATER)
    # The screen keeps its flood-only reading; the case EL travels next to it.
    assert point["loss_status"] == "permanent_water" and point["expected_loss_rub"] == 0
    assert point["case_expected_loss_rub"] == pytest.approx(el[ON_WATER], abs=0.011)
    assert point["case_loss_status"] == "ok"
    assert ON_WATER in {row["asset_id"] for row in shown["asset_proxy_check"]["rows"]}


def test_validator_rejects_the_flood_only_status(tmp_path):
    files = export(TestClient(app))
    rows = table(files, "asset_loss.csv")
    target = rows[0]
    # What the exporter used to write for a point on permanent water.
    target.update(status="permanent_water", expected_loss_rub="0.0", permanent_water_at_point="True")
    if "flood_expected_loss_rub" in target:
        target["flood_expected_loss_rub"] = "0.0"
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    files["asset_loss.csv"] = output.getvalue().encode()
    with pytest.raises(ValueError, match="status"):
        validator.validate(pack(files, tmp_path / "tampered.zip"))


def test_strategy_comparison_covers_the_mandatory_el(permanent_point):
    files = export(TestClient(app))
    el = mandatory_el(files)
    assets = {row["asset_id"]: row for row in table(files, "assets.csv")}
    comparison = {row["strategy"]: row for row in table(files, "strategy_comparison.csv")}
    plans = json.loads(files["strategy_plans.json"])
    rings = {feature["properties"]["candidate_id"]: feature["geometry"]["coordinates"][0]
             for feature in json.loads(files["candidate_orders.geojson"])["features"]}
    total = sum(el.values())
    covered = {}
    for key in ("B", "C"):
        covered[key] = {asset_id for asset_id, asset in assets.items() if any(
            ring[0][0] <= float(asset["longitude"]) <= ring[2][0] and ring[0][1] <= float(asset["latitude"]) <= ring[2][1]
            for ring in (rings[zone_id] for zone_id in plans[key]))}
        case = sum(el[asset_id] for asset_id in covered[key])
        flood = sum(el[asset_id] for asset_id in covered[key] if asset_id != ON_WATER)
        row = comparison[key]
        assert float(row["covered_expected_loss_rub"]) == pytest.approx(case, abs=0.02), key
        assert float(row["coverage_share"]) == pytest.approx(case / total, abs=1e-4), key
        assert float(row["covered_flood_loss_rub"]) == pytest.approx(flood, abs=0.02), key
    assert ON_WATER in covered["B"], "the broad zone covers the whole chip"
    meta = json.loads(files["run_metadata.json"])
    assert meta["expected_loss_model"] == "p_flood * asset_value_rub * vulnerability_coef"
    assert meta["decision_loss_rule"]
    plan_rows = {row["scenario_id"]: row for row in table(files, "sensitivity.csv")}
    same_budget = plan_rows["plan_budget_1.0"]
    assert float(same_budget["covered_expected_loss_rub"]) == pytest.approx(
        float(comparison["C"]["covered_expected_loss_rub"]), abs=0.02)
