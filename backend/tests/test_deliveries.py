"""Imagery providers and deliveries uploaded from a folder after buying outside the service."""
import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_bounds

from app import deliveries, procurement
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(procurement, "DB", tmp_path / "procurement.sqlite3")
    monkeypatch.setattr(deliveries, "STORE", tmp_path / "deliveries")
    return TestClient(app)


def geotiff(path, bounds, crs="EPSG:4326"):
    with rasterio.open(path, "w", driver="GTiff", width=64, height=64, count=3, dtype="uint16",
                       crs=crs, transform=from_bounds(*bounds, 64, 64)) as dst:
        dst.write(np.ones((3, 64, 64), dtype="uint16"))
    return path.read_bytes()


def test_every_provider_names_where_to_order_and_its_source(client):
    rows = client.get("/api/procurement/providers").json()["providers"]
    ids = {row["id"] for row in rows}
    assert {"roscosmos_geoportal", "terratech", "sovzond", "scanex", "innoter", "copernicus", "skyfi"} <= ids
    for row in rows:
        assert row["url"].startswith("https://") and row["sources"] and row["russia"] and row["order"]
    geoportal = next(row for row in rows if row["id"] == "roscosmos_geoportal")
    assert "sar" in geoportal["sensors"] and "840" in geoportal["price_basis"]


def test_folder_upload_reports_which_order_zones_a_raster_covers(client, tmp_path):
    zones = deliveries.scene_zones("Nigeria_812045")
    z10 = next(zone for zone in zones if zone["id"] == "Z10")
    west, south = [min(c) for c in zip(*z10["geometry"]["coordinates"][0])]
    east, north = [max(c) for c in zip(*z10["geometry"]["coordinates"][0])]
    created = client.post("/api/procurement/deliveries", json={
        "scene_id": "Nigeria_812045", "provider_id": "roscosmos_geoportal", "acquired_at": "2026-09-24"})
    assert created.status_code == 201, created.text
    delivery = created.json()["id"]
    body = geotiff(tmp_path / "scene.tif", (west - 0.001, south - 0.001, east + 0.001, north + 0.001))
    raster = client.post(f"/api/procurement/deliveries/{delivery}/files", params={"path": "order_1/scene.tif"},
                         content=body, headers={"content-type": "application/octet-stream"}).json()
    assert raster["kind"] == "raster" and raster["status"] == "georeferenced" and raster["bands"] == 3
    assert raster["zones"][0] == {"zone_id": "Z10", "share": 1.0}
    other = client.post(f"/api/procurement/deliveries/{delivery}/files", params={"path": "order_1/metadata.xml"},
                        content=b"<meta/>", headers={"content-type": "application/octet-stream"}).json()
    assert other["kind"] == "other"
    listed = client.get("/api/procurement/deliveries", params={"scene_id": "Nigeria_812045"}).json()
    assert listed["zones_covered"] == ["Z10"] and len(listed["deliveries"][0]["files"]) == 2


def test_raster_without_georeference_is_flagged(client, tmp_path):
    delivery = client.post("/api/procurement/deliveries", json={
        "scene_id": "Nigeria_812045", "provider_id": "other"}).json()["id"]
    path = tmp_path / "plain.tif"
    with rasterio.open(path, "w", driver="GTiff", width=8, height=8, count=1, dtype="uint8") as dst:
        dst.write(np.zeros((1, 8, 8), dtype="uint8"))
    entry = client.post(f"/api/procurement/deliveries/{delivery}/files", params={"path": "plain.tif"},
                        content=path.read_bytes(), headers={"content-type": "application/octet-stream"}).json()
    assert entry["status"] == "not_georeferenced" and entry["zones"] == []


@pytest.mark.parametrize("bad", ["../escape.tif", "C:/abs.tif", "a/../../b.tif", ""])
def test_paths_outside_the_delivery_are_refused(client, bad):
    delivery = client.post("/api/procurement/deliveries", json={
        "scene_id": "Nigeria_812045", "provider_id": "sovzond"}).json()["id"]
    response = client.post(f"/api/procurement/deliveries/{delivery}/files", params={"path": bad},
                           content=b"x", headers={"content-type": "application/octet-stream"})
    assert response.status_code == 422


def test_unknown_provider_and_scene_are_refused(client):
    assert client.post("/api/procurement/deliveries", json={
        "scene_id": "Nigeria_812045", "provider_id": "nobody"}).status_code == 422
    assert client.post("/api/procurement/deliveries", json={
        "scene_id": "missing", "provider_id": "sovzond"}).status_code == 404
