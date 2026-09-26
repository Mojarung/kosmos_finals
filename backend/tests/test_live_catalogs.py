"""SCANEX and free Copernicus catalogs; records shaped as both services answered on 2026-09-26."""
import pytest
from fastapi.testclient import TestClient

from app import copernicus, procurement, scanex
from app.catalog import metadata
from app.main import app


def nigeria_box(pad=0.01):
    w, s, e, n = metadata("Nigeria_812045")["bbox"]
    return w - pad, s - pad, e + pad, n + pad


def scanex_rows():
    w, s, e, n = nigeria_box()
    corners = {"x1": w, "y1": n, "x2": e, "y2": n, "x3": e, "y3": s, "x4": w, "y4": s}
    return [
        {"sceneid": "10893679", "acqdate": 1769299200, "cloudness": 0.0, "tilt": None, "platform": "Канопус-В-ИК",
         "url": "https://gptl.ru/previews/Kanopus-V-IK/2026/01/10893679_1.jpg", **corners},
        {"sceneid": "LC81900542018295LGN00", "acqdate": 1540166400, "cloudness": 71.2, "tilt": None,
         "platform": "LANDSAT_8", "url": "https://earthexplorer.usgs.gov/browse/x.jpg", **corners},
        {"sceneid": "X-UNKNOWN", "acqdate": 1540166400, "cloudness": 5.0, "tilt": None, "platform": "NEWSAT",
         "url": None, **corners},
    ]


def copernicus_features():
    w, s, e, n = nigeria_box()
    polygon = {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}
    return [
        {"id": "S2B_MSIL2A_20260923T095029_N0511_R079_T31PHL", "geometry": polygon,
         "properties": {"datetime": "2026-09-23T09:50:29Z", "platform": "sentinel-2b", "constellation": "sentinel-2",
                        "gsd": 10, "eo:cloud_cover": 68.1},
         "assets": {"thumbnail": {"href": "https://datahub.creodias.eu/odata/v1/Assets(a)/$value"}}},
        {"id": "S1A_IW_GRDH_1SDV_20181010T174608_COG", "geometry": polygon,
         "properties": {"datetime": "2018-10-10T17:46:08Z", "platform": "sentinel-1a", "constellation": "sentinel-1",
                        "sar:instrument_mode": "IW", "sar:pixel_spacing_range": 10.0, "sar:polarizations": ["VV", "VH"]},
         "assets": {"thumbnail": {"href": "https://datahub.creodias.eu/odata/v1/Assets(b)/$value"}}},
    ]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(procurement, "DB", tmp_path / "procurement.sqlite3")
    monkeypatch.setattr(scanex, "search", lambda aoi, start, end: scanex_rows())
    monkeypatch.setattr(copernicus, "search", lambda aoi, start, end: copernicus_features())
    return TestClient(app)


def catalog(client, source):
    r = client.post("/api/procurement/catalogs", json={"scene_id": "Nigeria_812045", "source": source,
                                                       "from_date": "2018-09-01"})
    assert r.status_code == 200, r.text
    return r.json()


def test_scanex_needs_no_account_and_prices_russian_and_free_scenes(client):
    assert client.get("/api/procurement/status").json()["scanex_configured"] is True
    c = catalog(client, "scanex")
    by_id = {o["id"]: o for o in c["offers"]}
    kanopus, landsat, unknown = by_id["10893679"], by_id["LC81900542018295LGN00"], by_id["X-UNKNOWN"]
    assert c["currency"] == "RUB" and "СКАНЭКС" in c["disclaimer"]
    assert kanopus["resolution_m"] == 2.5 and kanopus["capture_date"] == "2026-01-25" and kanopus["price"] > 0
    assert kanopus["price_status"] == "pp840_case_formula" and "СКАНЭКС" in kanopus["price_note"]
    assert landsat["price"] == 0 and landsat["open_data"] and landsat["resolution_m"] == 15
    assert unknown["resolution_m"] is None
    plan = client.post("/api/procurement/plan", json={"catalog_id": c["id"], "budget": 50000, "resolution_m": 3}).json()
    # At 3 m only Kanopus qualifies: Landsat is 15 m, the unknown platform has no stated resolution.
    assert plan["recommendation"]["ids"] == ["10893679"]
    unknown_row = next(o for o in plan["offers"] if o["id"] == "X-UNKNOWN")
    assert "Недостаточная детализация" in unknown_row["filter_reasons"]


def test_free_copernicus_scenes_cost_nothing_and_keep_their_thumbnails(client):
    c = catalog(client, "free")
    optical, radar = sorted(c["offers"], key=lambda o: o["product_type"])
    assert optical["title"] == "Sentinel-2B MSI L2A" and optical["cloud_percent"] == 68.1
    assert radar["title"] == "Sentinel-1A GRD IW" and radar["cloud_percent"] is None
    assert all(o["price"] == 0 and o["open_data"] and o["preview_url"].startswith("https://") for o in c["offers"])
    plan = client.post("/api/procurement/plan", json={"catalog_id": c["id"], "budget": 0, "resolution_m": 10,
                                                      "max_cloud_percent": 100}).json()
    assert plan["recommendation"]["cost"] == 0 and plan["recommendation"]["coverage_percent"] == 100


def test_supplier_price_of_scanex_and_free_is_not_claimed_as_verified(client):
    c = catalog(client, "scanex")
    r = client.post(f"/api/procurement/catalogs/{c['id']}/quotes/10893679")
    assert r.status_code == 409 and "по заявке" in r.json()["detail"]
