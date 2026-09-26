"""Checks against real SkyFi responses captured 2026-09-26; never served as app data."""
import io
import json
import zipfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import shape
from shapely.ops import unary_union
from app.main import app
from app import procurement as p

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(p, 'DB', tmp_path / 'procurement.sqlite3')
    monkeypatch.delenv('SKYFI_API_KEY', raising=False)
    return TestClient(app)

def snapshot(scene='Nigeria_812045'):
    return json.loads((Path(__file__).parent / 'fixtures' / f'skyfi_{scene}.json').read_text())

def catalog():
    return p.persist('catalog', snapshot())

def request(client, c, **kwargs):
    r = client.post('/api/procurement/plan', json={'catalog_id':c['id'], **kwargs})
    assert r.status_code == 200, r.text
    return r.json()

def test_only_real_provider_is_available(client):
    assert client.post('/api/procurement/catalogs',json={'scene_id':'Nigeria_812045','source':'demo'}).status_code == 422
    r=client.post('/api/procurement/catalogs',json={'scene_id':'Nigeria_812045','source':'skyfi'})
    assert r.status_code == 503 and 'SKYFI_API_KEY' in r.json()['detail']

def test_real_estimates_work_without_claiming_verified_quality(client):
    r=request(client,catalog())
    assert r['recommendation']['coverage_percent'] == 100 and r['cost'] == 385.81
    assert not r['price_verified'] and r['status'] == 'estimate'
    assert all(o['quality'] == 'unknown' for o in r['offers'])

@pytest.mark.parametrize('scene',['Nigeria_812045','Ghana_313799','Mekong_333434'])
@pytest.mark.parametrize('budget',[0,100,500,1000])
def test_real_budgets_and_union(scene,budget):
    c=snapshot(scene)
    r=p.calculate(c,p.Plan(catalog_id=c['id'],budget=budget))
    assert r['cost'] is None or r['cost'] <= budget
    chosen=[o for o in c['offers'] if o['id'] in r['selected_ids']]
    assert len(chosen) <= 2
    if chosen:
        union=unary_union([shape(o['coverage_geometry']) for o in chosen])
        assert r['coverage_km2'] == pytest.approx(p.area(union),abs=.001)
        assert r['coverage_percent'] <= 100
    else:
        assert r['cost'] is None and r['status'] == 'empty'

def test_empty_is_not_free(client):
    r=request(client,catalog(),budget=0)
    assert r['recommendation'] is None and r['cost'] is None
    assert r['budget_gap'] == 385.81 and not r['selected_ids']

def test_overlap_is_counted_once(client):
    c=catalog()
    ids=[o['id'] for o in c['offers'] if o['coverage_percent'] == 100][:2]
    r=request(client,c,selected_ids=ids)
    assert r['coverage_percent'] == 100
    assert r['overlap_km2'] == pytest.approx(c['area_km2'],abs=.002)
    assert r['cost'] == sum(o['price'] for o in c['offers'] if o['id'] in ids)

def test_filters_keep_catalog_visible(client):
    c=catalog()
    r=request(client,c,resolution_m=.1)
    assert r['recommendation'] is None and len(r['offers']) == len(c['offers'])
    assert all('Недостаточная детализация' in o['filter_reasons'] for o in r['offers'])

def test_provenance_and_dates(client):
    c=catalog()
    assert client.get('/api/procurement/catalogs/latest?scene_id=Nigeria_812045').json()['id'] == c['id']
    assert all(o['price_source'] == p.SKYFI+'/archives' and o['preview_url'].startswith('https://') for o in c['offers'])
    assert all(o['days_from_event'] > 0 for o in c['offers'])

def test_invalid_selection_dates_bounds(client):
    c=catalog()
    for extra in [{'selected_ids':['unknown']},{'selected_ids':[c['offers'][0]['id']]*2},{'budget':-1},{'resolution_m':0}]:
        assert client.post('/api/procurement/plan',json={'catalog_id':c['id'],**extra}).status_code == 422
    for extra in [{'from_date':'2020-01-01','to_date':'2018-01-01'},{'bbox':[0,0,0,1]},{'bbox':[0,0,100,80]}]:
        assert client.post('/api/procurement/catalogs',json={'scene_id':'Nigeria_812045',**extra}).status_code == 422

def test_real_export_and_no_empty_export(client):
    c=catalog()
    saved=client.post('/api/procurement/decisions',json={'catalog_id':c['id']}).json()
    z=zipfile.ZipFile(io.BytesIO(client.get('/api/procurement/decisions/'+saved['id']+'/export').content))
    assert z.testzip() is None
    report=z.read('comparison.html').decode()
    assert saved['plan']['selected_ids'][0] in report and 'metadata-proxy' in report
    assert 'не подтверждают состояние территории' in report
    assert json.loads(z.read('decision.json')) == saved
    assert len(json.loads(z.read('coverage.geojson'))['features']) == 1
    assert client.post('/api/procurement/decisions',json={'catalog_id':c['id'],'budget':0}).status_code == 422

def test_legacy_synthetic_catalog_rejected(client):
    c=p.persist('catalog',{**snapshot(),'source':'demo'})
    assert client.get('/api/procurement/catalogs/'+c['id']).status_code == 409
    assert client.get('/api/procurement/catalogs/latest?scene_id=Nigeria_812045').status_code == 404


def test_latest_full_scene_does_not_restore_a_different_region(client):
    c=catalog()
    altered={**snapshot(), 'bbox':[0,0,.01,.01]}
    p.persist('catalog',altered)
    assert client.get('/api/procurement/catalogs/latest?scene_id=Nigeria_812045').json()['id'] == c['id']
