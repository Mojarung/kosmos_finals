"""Imported assets, actual captured SkyFi catalog, and immutable request export."""
import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app import procurement, projects
from app.main import app

FIXTURES = Path(__file__).parent / 'fixtures'


def _catalog(name):
    return json.loads((FIXTURES / f'skyfi_{name}.json').read_text())


def test_project_catalog_plan_and_export(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, 'DB', tmp_path / 'projects.sqlite3')
    monkeypatch.setattr(procurement, 'DB', tmp_path / 'procurement.sqlite3')
    client = TestClient(app)
    case = client.get('/api/analysis', params={'scene_id': 'Nigeria_812045'}).json()
    points = {item['id']: item for item in case['assets']}
    project = client.post('/api/projects', json={'name': 'Реестр города', 'scene_id': 'Nigeria_812045'}).json()
    root = f"/api/projects/{project['id']}"
    csv = ('name,longitude,latitude,value_rub,vulnerability,value_source\n'
           f"Склад,{points['A10']['longitude']},{points['A10']['latitude']},2000000,0.3,Реестр\n"
           f"Подстанция,{points['A07']['longitude']},{points['A07']['latitude']},4000000,0.4,Реестр\n")
    assert client.post(root + '/assets/import', json={'format': 'csv', 'content': csv}).status_code == 200
    assert client.post(root + '/imagery/plan', json={'catalog_id': 'missing'}).status_code == 404
    catalog = procurement.persist('catalog', _catalog('Nigeria_812045'))
    input_data = {'catalog_id': catalog['id'], 'budget': 500, 'resolution_m': 1,
                  'max_cloud_percent': 30, 'deadline_hours': 24}
    response = client.post(root + '/imagery/plan', json=input_data)
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan['asset_count'] == plan['estimated_asset_count'] == 2
    assert plan['total_expected_loss_rub'] > 0
    assert plan['currency'] == 'USD'
    assert plan['recommendation']['covered_asset_count'] == 2
    assert plan['recommendation']['covered_el_percent'] == 100
    assert plan['recommendation']['cost'] <= 500
    assert any(o['covered_asset_ids'] for o in plan['offers'])
    assert plan['covered_expected_loss_rub'] == plan['total_expected_loss_rub']

    # A short deadline or zero budget removes the automatic commercial shortlist.
    assert client.post(root + '/imagery/plan', json={**input_data, 'deadline_hours': 1}).json()['recommendation'] is None
    assert client.post(root + '/imagery/plan', json={**input_data, 'budget': 0}).json()['recommendation'] is None
    picked = plan['recommendation']['ids']
    manual = client.post(root + '/imagery/plan', json={**input_data, 'budget': 0, 'selected_ids': picked}).json()
    assert manual['covered_asset_count'] == 2
    assert any('превышает' in note for note in manual['blockers'])
    saved = client.post(root + '/imagery/requests', json={**input_data, 'selected_ids': picked})
    assert saved.status_code == 201, saved.text
    request = saved.json()
    assert request['purchase_submitted'] is False
    assert request['status'] == 'draft_catalog_estimate'
    assert request['covered_asset_count'] == 2
    archive = client.get(root + f"/imagery/requests/{request['id']}/export")
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as document:
        assert set(document.namelist()) == {'request.json', 'project_assets.csv', 'selected_offers.geojson', 'README.txt'}
        snapshot = json.loads(document.read('request.json'))
        assert snapshot['purchase_submitted'] is False
        assert snapshot['catalog_id'] == catalog['id']
        assert snapshot['asset_snapshot'][0]['value_source'] == 'Реестр'
        assert 'Склад' in document.read('project_assets.csv').decode()
        assert len(json.loads(document.read('selected_offers.geojson'))['features']) == len(picked)
    assert len(client.get(root).json()['imagery_requests']) == 1
    assert client.post(root + '/assets/import', json={'format': 'csv', 'content': csv.replace('2000000', '9000000')}).status_code == 200
    with zipfile.ZipFile(io.BytesIO(client.get(root + f"/imagery/requests/{request['id']}/export").content)) as document:
        assert {a['name']: a['value_rub'] for a in json.loads(document.read('request.json'))['asset_snapshot']}['Склад'] == 2_000_000
    assert client.get('/api/analysis', params={'scene_id': 'Nigeria_812045'}).json()['metrics']['portfolio_count'] == 10


def test_project_rejects_catalog_of_another_scene(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, 'DB', tmp_path / 'projects.sqlite3')
    monkeypatch.setattr(procurement, 'DB', tmp_path / 'procurement.sqlite3')
    client = TestClient(app)
    project = client.post('/api/projects', json={'name': 'X', 'scene_id': 'Nigeria_812045'}).json()
    root = f"/api/projects/{project['id']}"
    source = 'name,longitude,latitude,value_rub,vulnerability,value_source\nA,5.8,9.08,100,0.2,Реестр\n'
    client.post(root + '/assets/import', json={'format': 'csv', 'content': source})
    foreign = procurement.persist('catalog', _catalog('Ghana_313799'))
    response = client.post(root + '/imagery/plan', json={'catalog_id': foreign['id']})
    assert response.status_code == 422
    assert 'другому снимку' in response.json()['detail']
