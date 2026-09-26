import csv
import io
import json
import zipfile
from array import array

import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Polygon, box, mapping

from app import building_rates, building_sources, buildings, catalog
from app.main import app


def feature(identifier="one", geometry=None, tags=None):
    return {"type": "Feature", "id": identifier, "geometry": mapping(geometry if geometry is not None else box(.00025, .0001, .00125, .0009)),
            "properties": {"tags": tags or {"building": "house"}}}


@pytest.fixture
def scene(monkeypatch, tmp_path):
    monkeypatch.setattr(building_sources, "CACHE", tmp_path)
    info = {"bbox": [0, 0, .002, .001], "width": 2, "height": 1, "observed_at": "2024-11-01"}
    monkeypatch.setattr(catalog, "metadata", lambda *_: info)
    monkeypatch.setattr(catalog, "float_map", lambda *_: array("f", [0, 1]))
    monkeypatch.setattr(catalog, "mask", lambda *_: bytes([0, 1]))
    monkeypatch.setattr(catalog, "permanent_mask", lambda *_: None)
    return info


def evaluate(features, **overrides):
    dataset = building_sources.normalize({"type": "FeatureCollection", "features": features, "source": {"name": "fixture"}})
    request = buildings.Evaluation(scene_id="fixture", catalog_id=building_sources.save(dataset), **overrides)
    return buildings.evaluate(request), request


def test_fractional_pixels_and_cost_basis(scene):
    result, _ = evaluate([feature(tags={"building": "house", "building:levels": "12"})])
    p = result["features"][0]["properties"]
    assert p["radar_coverage"] == pytest.approx(1)
    assert p["water_probability_proxy"] == pytest.approx(.25)
    assert p["water_fraction"] == pytest.approx(.25)
    assert p["expected_loss_rub"] == pytest.approx(p["area_m2"] * 55000 * .3 * .25)
    assert p["exposure_rub"] == pytest.approx(p["area_m2"] * 55000)  # not twelve floors


def test_holes_not_counted(scene):
    scene.update(bbox=[0, 0, .003, .003], width=3, height=3)
    polygon = Polygon(box(0, 0, .003, .003).exterior.coords, [box(.001, .001, .002, .002).exterior.coords])
    probability = np.zeros(9); probability[4] = 1
    result = buildings.raster_stats(polygon, scene, probability, probability.astype('uint8'), None)
    assert result['water_probability_proxy'] == 0
    assert result['observed_pixels'] == 8
    assert result['radar_coverage'] == pytest.approx(1)


@pytest.mark.parametrize("geometry,mask,status", [
    (box(-.001, 0, .001, .001), [0, 1], "partial_radar"),
    (box(.004, 0, .005, .001), [0, 1], "no_radar"),
    (box(0, 0, .002, .001), [0, 255], "partial_radar"),
])
def test_missing_coverage_never_zero_loss(scene, monkeypatch, geometry, mask, status):
    monkeypatch.setattr(catalog, 'mask', lambda *_: bytes(mask))
    result, _ = evaluate([feature(geometry=geometry)])
    assert result['features'][0]['properties']['status'] == status
    assert result['summary']['expected_loss_rub'] is None
    assert result['summary']['unestimated'] == 1


def test_permanent_water_zeroes_effective_probability(scene, monkeypatch):
    monkeypatch.setattr(catalog, 'permanent_mask', lambda *_: bytes([0, 1]))
    result, _ = evaluate([feature()])
    assert result['features'][0]['properties']['expected_loss_rub'] == 0
    assert result['features'][0]['properties']['water_fraction'] == 0


def test_unknown_manual_type_and_per_type_rates(scene):
    rows = [feature('unknown', box(0, 0, .001, .001), {'building': 'yes'}), feature('school', box(.001, 0, .002, .001), {'building': 'school'})]
    result, _ = evaluate(rows)
    assert result['summary']['unknown_type'] == 1
    assert result['summary']['estimated'] == 1
    original = result['features'][1]['properties']['expected_loss_rub']
    result, _ = evaluate(rows, rate_overrides={'school': {'cost_rub_m2': 170000, 'damage_ratio': .3}}, type_overrides={'unknown': 'warehouse'})
    assert result['summary']['unknown_type'] == 0
    assert result['features'][1]['properties']['expected_loss_rub'] == pytest.approx(2 * original)
    assert result['features'][0]['properties']['cost_rub_m2'] == 30000
    zero, _ = evaluate(rows, rate_overrides={'school': {'cost_rub_m2': 85000, 'damage_ratio': 0}})
    assert zero['features'][1]['properties']['expected_loss_rub'] == 0


@pytest.mark.parametrize('tags,kind', [({'building':'yes'},'unknown'), ({'building':'residential'},'unknown'),
    ({'building':'church','building:use':'warehouse'},'warehouse'), ({'building':'yes','amenity':'school'},'school'),
    ({'building':'house','shop':'supermarket'},'retail'), ({'building':'commercial'},'unknown')])
def test_type_evidence(tags, kind):
    assert building_rates.classify(tags)[0] == kind


def test_duplicates_overlap_and_invalid_geometry(scene):
    result, _ = evaluate([feature(), feature('copy')])
    assert result['summary']['count'] == 1
    result, _ = evaluate([feature(), feature('overlap', box(.0005, .0001, .0015, .0009))])
    assert result['summary']['estimated'] == 0
    assert all(f['properties']['status'] == 'overlap' for f in result['features'])
    with pytest.raises(ValueError):
        evaluate([feature(), feature()])
    with pytest.raises(ValueError):
        evaluate([feature(geometry=Polygon([(0,0),(.001,.001),(0,.001),(.001,0),(0,0)]))])


def test_osm_relation_hole_and_member_dedup():
    xml = b'''<osm><node id="1" lon="0" lat="0"/><node id="2" lon=".003" lat="0"/>
    <node id="3" lon=".003" lat=".003"/><node id="4" lon="0" lat=".003"/>
    <node id="5" lon=".001" lat=".001"/><node id="6" lon=".002" lat=".001"/>
    <node id="7" lon=".002" lat=".002"/><node id="8" lon=".001" lat=".002"/>
    <way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/><tag k="building" v="yes"/></way>
    <way id="11"><nd ref="5"/><nd ref="6"/><nd ref="7"/><nd ref="8"/><nd ref="5"/></way>
    <relation id="20"><member type="way" ref="10" role="outer"/><member type="way" ref="11" role="inner"/>
    <tag k="type" v="multipolygon"/><tag k="building" v="school"/></relation></osm>'''
    result = building_sources.parse_osm(xml, {'name':'OSM'})
    assert len(result['features']) == 1
    assert result['features'][0]['id'] == 'relation/20'
    assert len(result['features'][0]['geometry']['coordinates']) == 2


def test_api_import_export_same_values_and_validation(scene):
    client = TestClient(app)
    content = json.dumps({'type':'FeatureCollection','features':[feature(tags={'building':'school','name':'=1+1'})]})
    response = client.post('/api/buildings/import', json={'scene_id':'fixture','content':content})
    assert response.status_code == 200
    result = response.json()
    request = {'scene_id':'fixture','catalog_id':result['catalog_id'], 'rate_overrides':{'school':{'cost_rub_m2':9999,'damage_ratio':.2}}}
    result = client.post('/api/buildings/evaluate', json=request).json()
    exported = client.post('/api/buildings/export', json=request)
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert json.loads(archive.read('buildings.geojson')) == result
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['summary'] == result['summary']
        assert manifest['rates']['school']['cost_rub_m2'] == 9999
        row = next(csv.DictReader(io.StringIO(archive.read('buildings.csv').decode('utf-8-sig'))))
        assert row['name'] == "'=1+1"
        assert float(row['expected_loss_rub']) == result['summary']['expected_loss_rub']
    request['rate_overrides']['school']['damage_ratio'] = -1
    assert client.post('/api/buildings/evaluate', json=request).status_code == 422
    assert client.post('/api/buildings/evaluate', json={'scene_id':'fixture','catalog_id':'../../etc/passwd'}).status_code == 422
    assert client.post('/api/buildings/import', json={'scene_id':'fixture','content':'[]'}).status_code == 422


def test_real_paiporta_snapshot_is_reproducible(tmp_path, monkeypatch):
    monkeypatch.setattr(building_sources, 'CACHE', tmp_path)
    result = buildings.load(buildings.LoadRequest(scene_id='Paiporta_20241101',source='paiporta'))
    assert result['summary']['count'] == 908
    assert result['summary']['estimated'] == 237
    assert result['summary']['unknown_type'] == 671
    assert result['observed_at'].startswith('2024-11-01')
    assert result['source']['retrieved_date'] == '2026-09-26'
    assert result['model']['mode'] == 'S1 fallback'
    assert result['summary']['with_water'] == 0  # late urban radar: do not claim detected damage
    assert result['summary']['expected_loss_rub'] == pytest.approx(7606862.806959196)
