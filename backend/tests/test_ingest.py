import io
import json
import shutil
import threading
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import catalog, ingest
from app.chips import chip_record, official_chips, official_events
from app.main import app
from synthetic import make_chip

PARTS = {"train", "valid", "test_core", "test_adjacent", "bolivia"}


def test_official_catalog_lists_every_hand_labeled_chip():
    chips = official_chips()
    assert len(chips) == 446
    assert len({chip["chip_id"] for chip in chips}) == 446
    assert {chip["split_part"] for chip in chips} == PARTS
    assert all(chip["observed_at"] and chip["event"] for chip in chips)
    nigeria = chip_record("Nigeria_812045")
    assert nigeria["split_part"] == "test_core" and nigeria["observed_at"] == "2018-09-21"
    assert chip_record("Mekong_333434")["observed_at"] == "2018-08-05"
    events = {event["key"]: event for event in official_events()}
    assert events["Sri-Lanka"]["chips"] == 42 and events["Bolivia"]["label"] == "Боливия"


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "RUNTIME_DIR", tmp_path / "runtime")
    catalog.refresh()
    source = tmp_path / "source"

    def fake_fetch(chip_id, layer, target):
        files = make_chip(source, chip_id)
        path = files["s1"] if layer == "S1Hand" else files["label"]
        shutil.copyfile(path, target / path.name)
        return target / path.name

    def fake_model(s1_path, out_dir):
        files = make_chip(source, s1_path.name.removesuffix("_S1Hand.tif"))
        shutil.copytree(files["inference"], out_dir, dirs_exist_ok=True)

    monkeypatch.setattr(ingest, "_fetch", fake_fetch)
    monkeypatch.setattr(ingest, "_run_model", fake_model)
    yield tmp_path
    monkeypatch.undo()
    catalog.refresh()


def wait_for(job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = ingest.get(job_id)
        if job["stage"] in {"done", "error"}:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {ingest.get(job_id)}")


def test_live_ingest_builds_scene_visible_to_api(runtime):
    client = TestClient(app)
    response = client.post("/api/scenes", json={"chip_id": "Ghana_103272"})
    assert response.status_code == 202
    job = wait_for(response.json()["id"])
    assert job["stage"] == "done" and job["scene_id"] == "Ghana_103272"
    assert set(job["timings"]) == {"download", "inference", "prepare"}
    live = next(scene for scene in client.get("/api/scenes").json()["scenes"]
                if scene["chip_id"] == "Ghana_103272")
    assert live["origin"] == "live" and live["split_part"] == "train"
    assert live["metrics_independent"] is False
    analysis = client.get("/api/analysis", params={"scene_id": "Ghana_103272"})
    assert analysis.status_code == 200 and len(analysis.json()["assets"]) == 10
    assert client.get("/api/demo/Ghana_103272/radar.png").status_code == 200
    exported = client.get("/api/export", params={"scene_id": "Ghana_103272"})
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        run = json.loads(archive.read("run_metadata.json"))
    assert run["split_part"] == "train" and run["metrics_independent"] is False
    assert run["portfolio_seed"] == ingest.LIVE_SEED
    assert client.get(f"/api/jobs/{job['id']}").json()["stage"] == "done"


def test_repeated_request_reuses_running_job(runtime, monkeypatch):
    release = threading.Event()
    model = ingest._run_model

    def slow_model(s1_path, out_dir):
        release.wait(10)
        model(s1_path, out_dir)

    monkeypatch.setattr(ingest, "_run_model", slow_model)
    client = TestClient(app)
    first = client.post("/api/scenes", json={"chip_id": "Ghana_24858"}).json()
    second = client.post("/api/scenes", json={"chip_id": "Ghana_24858"}).json()
    assert first["id"] == second["id"]
    release.set()
    assert wait_for(first["id"])["stage"] == "done"
    again = client.post("/api/scenes", json={"chip_id": "Ghana_24858"})
    assert again.status_code == 200 and again.json()["scene_id"] == "Ghana_24858"


def test_existing_demo_scene_is_not_recomputed(runtime):
    response = TestClient(app).post("/api/scenes", json={"chip_id": "Nigeria_812045"})
    assert response.status_code == 200 and response.json()["scene_id"] == "Nigeria_812045"


def test_offline_download_reports_error(runtime, monkeypatch):
    def offline(chip_id, layer, target):
        raise OSError("network is unreachable")

    monkeypatch.setattr(ingest, "_fetch", offline)
    job = wait_for(TestClient(app).post("/api/scenes", json={"chip_id": "Ghana_147015"}).json()["id"])
    assert job["stage"] == "error" and job["message"].startswith("Не удалось скачать S1")
    assert "Ghana_147015" not in catalog.known_scene_ids()


def test_unknown_chip_is_rejected():
    assert TestClient(app).post("/api/scenes", json={"chip_id": "Atlantis_1"}).status_code == 404


def test_chips_endpoint_lists_catalog_and_prepared_scenes():
    data = TestClient(app).get("/api/chips").json()
    assert len(data["chips"]) == 446 and "Nigeria_812045" in data["prepared"]
