"""Roscosmos Geoportal catalog: responses shaped as in the ЦБГД API guide (edition 2025-09-09).

The optical record copies the fields of the guide's example answer of api.gptl.ru/catalog/v2/search;
the radar record follows its STAC-API section. Footprints are moved onto the Nigeria demo scene.
"""
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import procurement, roscosmos
from app.catalog import metadata
from app.main import app
from app.planning import quote, today


def box_wkt(w, s, e, n):
    return f"MULTIPOLYGON((({w} {s},{e} {s},{e} {n},{w} {n},{w} {s})))"


def fixtures():
    w, s, e, n = metadata("Nigeria_812045")["bbox"]
    optical = {
        "date_time_stamp": "2018-09-24T10:38:10.000Z", "resolution": 2.5, "platform_name": "Канопус-В1",
        "instrument_identifier": "PSS", "instrument_name": "ПСС", "nadir_tilt_angle": -7.36, "cloudiness": 10,
        "identifier": "ETRIS.KV1.PSS.41514.1.0.2018-09-24.L0.FKL_KLG.NTSOMZ_MSK", "platform_identifier": "KV1",
        "platform_type_identifier": "KV", "acquisition_date_begin": "2018-09-24T10:22:53.000Z",
        "geometry": box_wkt(w - 0.01, s - 0.01, e + 0.01, n + 0.01),
        "previews": [{"geometry": box_wkt(w, s, e, n), "url": "http://www.gptl.ru/previews/Kanopus-V1/2018/09/1_1.png"}],
    }
    radar = {
        "type": "Feature", "id": "ETRIS.CNDR2.SPOT.3247.4.189.2018-09-25.SAR-2A.NTSOMZ_MSK.NTSOMZ_MSK",
        "geometry": {"type": "Polygon", "coordinates": [[[w, s], [(w + e) / 2, s], [(w + e) / 2, n], [w, n], [w, s]]]},
        "properties": {"datetime": "2018-09-25T18:18:28Z", "platform": "CNDR2", "gsd": 1.0,
                       "sar:instrument_mode": "SPOT", "sar:polarizations": ["HH"]},
        "assets": {"quicklook": {"href": "https://s3.gptl.ru/stac-assets/Kondor/SPOT/ql.3857.png"}},
    }
    return optical, radar


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(procurement, "DB", tmp_path / "procurement.sqlite3")
    monkeypatch.setenv("ETRIS_ACCESS_TOKEN", "test-token")
    optical, radar = fixtures()
    calls = []

    def fake_call(request, what):
        calls.append((request.full_url, request.get_header("Authorization")))
        return {"features": [radar]} if "stac" in request.full_url else {"count": 1, "results": [optical]}

    monkeypatch.setattr(roscosmos, "_call", fake_call)
    test_client = TestClient(app)
    test_client.calls = calls
    return test_client


def test_catalog_lists_optics_and_radar_in_roubles_by_the_case_formula(client):
    r = client.post("/api/procurement/catalogs", json={"scene_id": "Nigeria_812045", "from_date": "2018-09-20",
                                                       "to_date": "2018-10-01"})
    assert r.status_code == 200, r.text
    catalog = r.json()
    assert catalog["source"] == "roscosmos" and catalog["currency"] == "RUB"
    assert all(auth == "Bearer test-token" for _, auth in client.calls)
    by_type = {o["product_type"]: o for o in catalog["offers"]}
    optical, radar = by_type["OPTICAL"], by_type["SAR"]
    assert optical["title"] == "Канопус-В1 ПСС" and optical["cloud_percent"] == 10
    assert optical["preview_url"].startswith("https://www.gptl.ru/previews/")
    assert optical["coverage_percent"] == 100 and radar["coverage_percent"] == pytest.approx(50, abs=1)
    assert radar["preview_url"].startswith(roscosmos.QUICKLOOK)
    order = {"candidate_id": "offer", "area_km2": max(optical["area_km2"], 1.0), "sensor_type": "optical",
             "resolution_m": 2.5, "processing_level": "L2", "usage_type": "internal",
             "acquisition_type": "archive", "guaranteed_purchase": False, "observation_at": "2018-09-24"}
    assert optical["price"] == round(quote([order], calculation_date=today())["total_rub"], 2)


def test_plan_and_export_keep_roubles_and_name_the_geoportal(client):
    catalog = client.post("/api/procurement/catalogs", json={"scene_id": "Nigeria_812045", "from_date": "2018-09-20",
                                                             "to_date": "2018-10-01"}).json()
    decision = client.post("/api/procurement/decisions", json={"catalog_id": catalog["id"], "budget": 1e6})
    assert decision.status_code == 200, decision.text
    exported = client.get(f"/api/procurement/decisions/{decision.json()['id']}/export")
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        report = archive.read("comparison.html").decode()
        coverage = json.loads(archive.read("coverage.geojson"))
    assert "Геопортал Роскосмоса" in report and "₽" in report and "$" not in report
    assert {f["properties"]["currency"] for f in coverage["features"]} == {"RUB"}


def test_roscosmos_price_is_confirmed_only_by_a_real_order(client):
    catalog = client.post("/api/procurement/catalogs", json={"scene_id": "Nigeria_812045", "from_date": "2018-09-20",
                                                             "to_date": "2018-10-01"}).json()
    offer = catalog["offers"][0]["id"]
    r = client.post(f"/api/procurement/catalogs/{catalog['id']}/quotes/{offer}")
    assert r.status_code == 409 and "Order API" in r.json()["detail"]


def test_without_credentials_the_catalog_says_what_to_configure(tmp_path, monkeypatch):
    monkeypatch.setattr(procurement, "DB", tmp_path / "procurement.sqlite3")
    for name in ("ETRIS_ACCESS_TOKEN", "ETRIS_CLIENT_ID", "ETRIS_USERNAME", "ETRIS_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    client = TestClient(app)
    assert client.get("/api/procurement/status").json()["roscosmos_configured"] is False
    r = client.post("/api/procurement/catalogs", json={"scene_id": "Nigeria_812045"})
    assert r.status_code == 503 and "ETRIS_ACCESS_TOKEN" in r.json()["detail"]
