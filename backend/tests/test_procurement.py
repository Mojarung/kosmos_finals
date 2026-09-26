import io
import json
import zipfile
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box, mapping
from app.main import app
from app import procurement as p

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(p, 'DB', tmp_path / 'procurement.sqlite3')
    monkeypatch.delenv('SKYFI_API_KEY', raising=False)
    return TestClient(app)

def catalog(client):
    r = client.post('/api/procurement/catalogs', json={'scene_id': 'Nigeria_812045'})
    assert r.status_code == 200, r.text
    return r.json()

def plan(client, c, **kw):
    r = client.post('/api/procurement/plan', json={'catalog_id': c['id'], **kw})
    assert r.status_code == 200, r.text
    return r.json()

def test_filters_and_unique_coverage(client):
    c = catalog(client)
    r = plan(client, c)
    assert r['cost'] <= 500
    assert len(r['covered_ids']) == len(set(r['covered_ids'])) == 10
    assert r['covered_el_rub'] == pytest.approx(sum(a['expected_loss_rub'] for a in c['assets']))
    rejected = {o['id']: o['reason'] for o in r['offers']}
    for key, fragment in [('free','детализация'),('old','периода'),('late','позже'),('cloud','не проверена')]:
        assert fragment in rejected[key]

def test_zero_budget_and_impossible_deadline(client):
    c = catalog(client)
    r = plan(client, c, budget=0, mandatory=['A01'])
    assert not r['mandatory_satisfied'] and r['missing_ids'] == ['A01']
    assert r['cost'] == 0 and r['budget_gap'] > 0
    r = plan(client, c, deadline_hours=1, mandatory=['A01'])
    assert r['minimum_cost'] is None and not r['mandatory_satisfied']

def test_free_only_meets_coarse_requirement(client):
    r = plan(client, catalog(client), budget=0, resolution_m=10, mandatory=['A01'])
    assert r['mandatory_satisfied'] and r['selected_ids'] == ['free'] and r['cost'] == 0

def test_exclusion_changes_basket(client):
    c = catalog(client)
    first = plan(client, c)
    other = plan(client, c, excluded=first['selected_ids'])
    assert not set(other['selected_ids']) & set(first['selected_ids'])
    assert other['cost'] != first['cost']

def test_cheapest_requires_only_requested_assets(client):
    c = catalog(client)
    assert plan(client, c, objective='cheapest')['cost'] == 0
    r = plan(client, c, objective='cheapest', mandatory=['A01'])
    assert r['cost'] == r['minimum_cost'] and 'A01' in r['covered_ids']

def test_immutable_saved_request_and_export(client):
    c = catalog(client)
    saved = client.post('/api/procurement/decisions', json={'catalog_id': c['id'], 'mandatory': ['A01']}).json()
    plan(client, c, budget=0)
    assert client.get('/api/procurement/decisions/' + saved['id']).json() == saved
    z = zipfile.ZipFile(io.BytesIO(client.get('/api/procurement/decisions/' + saved['id'] + '/export').content))
    assert z.testzip() is None and json.loads(z.read('decision.json')) == saved
    assert 'Учебные' in z.read('request.md').decode()
    assert len(json.loads(z.read('orders.geojson'))['features']) == len(saved['plan']['selected_ids'])

def test_missing_key_is_explicit(client):
    r = client.post('/api/procurement/catalogs', json={'scene_id': 'Nigeria_812045', 'source': 'skyfi'})
    assert r.status_code == 503 and 'SKYFI_API_KEY' in r.json()['detail']

def test_quote_and_quality_version_catalog_without_purchase(client, monkeypatch):
    c = catalog(client)
    c['source'] = 'skyfi'
    c['offers'] = [dict(c['offers'][0], archive_id='real-id', quality='unknown', quote_validated=False)]
    c = p.persist('catalog', c)
    calls = []
    def provider(path, payload):
        calls.append((path, payload))
        return {'estimatedCostCents': 12345, 'provider': 'VANTOR', 'aoiSqkm': 1.2}
    monkeypatch.setattr(p, 'skyfi_post', provider)
    endpoint = '/api/procurement/catalogs/' + c['id']
    quoted = client.post(endpoint + '/quotes/' + c['offers'][0]['id']).json()
    assert quoted['id'] != c['id'] and quoted['offers'][0]['price'] == 123.45
    assert calls[0][0] == '/order-archive/validate' and calls[0][1]['archiveId'] == 'real-id'
    assert not plan(client, quoted)['selected_ids']
    reviewed = client.post('/api/procurement/catalogs/' + quoted['id'] + '/review',
        json={'offer_id': c['offers'][0]['id'], 'note': 'Проверено по исходной маске качества сцены'}).json()
    assert plan(client, reviewed)['selected_ids']
    assert client.get(endpoint).json()['offers'][0]['quality'] == 'unknown'

def test_reject_bad_inputs(client):
    c = catalog(client)
    for extra in [{'mandatory':['unknown']},{'excluded':['unknown']},{'budget':-1},{'resolution_m':0}]:
        assert client.post('/api/procurement/plan', json={'catalog_id':c['id'],**extra}).status_code == 422
    assert client.post('/api/procurement/catalogs', json={'scene_id':'Nigeria_812045','from_date':'2020-01-01','to_date':'2018-01-01'}).status_code == 422

def test_full_scene_price_and_unknown_delivery():
    item = {'archiveId':'x','provider':'SAR','footprint':box(5,9,5.1,9.1).wkt,'fullSceneOnly':True,
        'priceFullScene':900,'priceForOneSquareKm':2,'gsd':1,'captureTimestamp':'2018-09-21T00:00:00Z'}
    o = p.normalize_archive(item, box(5.02,9.02,5.04,9.04))
    assert o['price'] == 900 and o['delivery_hours'] is None
    assert o['geometry'] == mapping(box(5,9,5.1,9.1)) and o['billable_km2'] is None

def test_new_geometry_invalidates_real_quote_and_keeps_original(client):
    c = catalog(client)
    c['source'] = 'skyfi'
    c['offers'] = [dict(next(o for o in c['offers'] if o['id'] == 'wide'), archive_id='source-scene',
                         price_status='provider_estimate', quote_response={'estimatedCostCents': 100}, quality='operator_review')]
    c = p.persist('catalog', c)
    r = client.post('/api/procurement/catalogs/' + c['id'] + '/variants', json={'offer_id':'wide','asset_ids':['A01']})
    assert r.status_code == 200, r.text
    variant = r.json()['offers'][-1]
    assert not variant['quote_validated'] and variant['quality'] == 'unknown'
    assert 'quote_response' not in variant and variant['area_km2'] < c['offers'][0]['area_km2']
    assert len(client.get('/api/procurement/catalogs/' + c['id']).json()['offers']) == 1


def test_marginal_price_preserves_already_covered_targets(client):
    c = catalog(client)
    r = plan(client, c, objective='cheapest', mandatory=['A01'])
    for asset_id in r['covered_ids']:
        assert r['incremental_cost'][asset_id] == 0
    assert r['free_coverage_count'] == 0 and r['broad_cost'] > 0


def test_normalized_search_retains_provider_evidence_and_unknown_quality(client, monkeypatch):
    info = p.metadata('Nigeria_812045')
    calls = []
    def provider(path, payload):
        calls.append((path,payload))
        return {'archives':[{'archiveId':'real','provider':'VANTOR','footprint':box(*info['bbox']).wkt,
            'priceFullScene':900,'priceForOneSquareKm':10,'minSqKm':1,'maxSqKm':1000,
            'gsd':.5,'captureTimestamp':'2018-09-21T12:00:00Z'}], 'nextPage':'more'}
    monkeypatch.setattr(p,'skyfi_post',provider)
    response = client.post('/api/procurement/catalogs',json={'scene_id':'Nigeria_812045','source':'skyfi'})
    assert response.status_code == 200
    c=response.json()
    assert calls[0][0]=='/archives' and c['truncated'] and c['source']=='skyfi'
    assert calls[0][1]['openData'] is False
    assert c['offers'][0]['archive_id']=='real' and c['offers'][0]['quality']=='unknown'
    assert not plan(client,c)['selected_ids']

@pytest.mark.parametrize('raw,meters', [(1000,10),(58.623189,.58623189),(36.78312,.3678312)])
def test_skyfi_resolution_is_centimeters(raw, meters):
    item = {'archiveId':'x','provider':'VANTOR','footprint':box(5,9,5.1,9.1).wkt,
        'priceFullScene':900,'priceForOneSquareKm':15,'gsd':raw,'captureTimestamp':'2026-08-14T00:00:00Z'}
    offer = p.normalize_archive(item, box(5.02,9.02,5.04,9.04))
    assert offer['resolution_m'] == pytest.approx(meters)
    assert offer['source_gsd_cm'] == raw


def test_blockers_explain_empty_basket_and_legacy_catalog_requires_refresh(client):
    c = catalog(client)
    result = plan(client,c,deadline_hours=1)
    assert result['eligible_count'] == 0
    assert result['blockers']['Доставка позже срока'] > 0
    c['source'] = 'skyfi'
    c.pop('normalization_version')
    c = p.persist('catalog',c)
    assert client.post('/api/procurement/plan',json={'catalog_id':c['id']}).status_code == 409


def test_provider_rate_limit_has_actionable_message(client, monkeypatch):
    from urllib.error import HTTPError
    monkeypatch.setenv('SKYFI_API_KEY','test-placeholder')
    def limited(*args, **kwargs):
        raise HTTPError(p.SKYFI + '/archives',429,'rate limit',{},None)
    monkeypatch.setattr(p,'urlopen',limited)
    r=client.post('/api/procurement/catalogs',json={'scene_id':'Nigeria_812045','source':'skyfi'})
    assert r.status_code == 429 and 'Подождите минуту' in r.json()['detail']
