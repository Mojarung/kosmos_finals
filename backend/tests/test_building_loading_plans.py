import json
import time

import numpy as np
import pytest
from shapely.geometry import box, mapping

from app import building_sources as source, building_jobs, planning


def building(identifier, bounds, uses=None):
    return {'type': 'Feature', 'id': identifier, 'geometry': mapping(box(*bounds)),
            'properties': {'tags': {'building': 'house'}, 'contained_uses': uses or []}}


def test_region_near_300_km2_loads_all_tiles_with_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(source, 'CACHE', tmp_path)
    calls, progress = [], []
    bounds = [0, 0, .155, .155]  # ~296 km², well above the old 60 km² restriction
    def fetch(tile, deadline, can_split):
        calls.append(tile)
        w, s, _, _ = tile
        data = source.normalize({'type': 'FeatureCollection', 'features': [building(str(tile), [w, s, w+.0001, s+.0001])],
                                 'source': source.osm_source('https://www.openstreetmap.org', tile, 'map')})
        return source.cache_extract(data, tile)
    monkeypatch.setattr(source, '_fetch_tile', fetch)
    data = source.load_osm(bounds, progress=lambda b, n: progress.append(source.area_m2(box(*b))))
    assert len(data['features']) == len(calls) > 16
    assert sum(progress) == pytest.approx(source.area_m2(box(*bounds)), rel=.001)
    assert source.load_osm(bounds) == json.loads(json.dumps(data))
    assert len(calls) == len(data['features'])  # complete-cache hit sends no requests
    with pytest.raises(ValueError, match='300'):
        source.load_osm([0, 0, 1, 1])


def test_density_error_splits_and_completed_tiles_survive_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(source, 'CACHE', tmp_path)
    bounds = [0, 0, .016, .016]
    calls = []
    def fetch(tile, deadline, can_split):
        calls.append(tile)
        if tile == bounds:
            raise source.OSMSplitRequired('too many nodes')
        w, s, _, _ = tile
        return source.cache_extract(source.normalize({'type':'FeatureCollection', 'features':[building(str(tile), [w,s,w+.0001,s+.0001])],
            'source':source.osm_source('https://www.openstreetmap.org', tile, 'map')}), tile)
    monkeypatch.setattr(source, '_fetch_tile', fetch)
    result = source.load_osm(bounds)
    assert len(result['features']) == 4
    assert len(calls) == 5


def test_merge_preserves_whole_footprint_and_deduplicates_relations(tmp_path, monkeypatch):
    monkeypatch.setattr(source, 'CACHE', tmp_path)
    whole = building('way/1', [.0005, 0, .0015, .001])
    relation = building('relation/2', [.002, 0, .003, .001])
    child = building('way/3', [.002, 0, .003, .001])
    def part(features, members):
        return source.normalize({'type': 'FeatureCollection', 'features': features,
            'source': {'name': 'OSM', 'relation_member_way_ids': members}})
    result = source._merge_tiles([part([whole, relation], ['3']), part([whole, child], [])], [0, 0, .004, .002])
    assert {f['id'] for f in result['features']} == {'way/1', 'relation/2'}
    assert next(f for f in result['features'] if f['id']=='way/1')['geometry'] == whole['geometry']


def test_compact_baskets_match_full_search_without_large_coverage_matrix():
    zones = [{'id': str(i), 'bounds': [i, 0, i+2, 1], 'area_km2': 1} for i in range(6)]
    assets = [{'id': str(i), 'longitude': i*.05, 'latitude': .5, 'expected_loss_rub': i*1.37} for i in range(160)]
    dense, compact = planning.BasketSpace(zones, assets), planning.BasketSpace(zones, assets, compact=True)
    assert compact.asset_covered is None
    np.testing.assert_allclose(compact.losses_for(None), dense.losses_for(None), atol=1e-8)
    values = np.arange(len(assets)) * .13
    np.testing.assert_allclose(compact.losses_for(values), dense.losses_for(values), atol=1e-8)
    for i in range(64):
        np.testing.assert_array_equal(compact.covered_assets(i), dense.asset_covered[i])
    big = planning.BasketSpace(zones, assets * 1000, compact=True)
    assert big.asset_masks.nbytes < 2_000_000
    assert big.losses_for(None)[-1] == pytest.approx(dense.losses_for(None)[-1] * 1000)


def test_full_footprint_required_not_only_centroid():
    assert not planning.contains([0,0,1,1], {'longitude':.5,'latitude':.5,'footprint_bounds':[-.1,.2,.9,.8]})
    assert planning.contains([0,0,1,1], {'footprint_bounds':[0,0,1,1]})


def test_background_job_reuses_work_and_reports_progress(tmp_path, monkeypatch):
    from app import buildings
    monkeypatch.setattr(source, 'CACHE', tmp_path)
    monkeypatch.setattr(building_jobs, '_JOBS', {})
    monkeypatch.setattr(building_jobs.catalog, 'metadata', lambda _: {'bbox':[0,0,.01,.01]})
    def load(scene, kind, progress=None):
        progress([0,0,.01,.01], 1)
        return source.normalize({'type':'FeatureCollection','features':[building('one',[0,0,.001,.001])], 'source':{'name':'fixture'}})
    monkeypatch.setattr(buildings, 'load_source', load)
    job = building_jobs.start('job-fixture', 'osm')
    deadline = time.monotonic() + 3
    while (row := building_jobs.status(job['job_id']))['status'] not in {'ready','error'} and time.monotonic() < deadline:
        time.sleep(.01)
    assert row['status'] == 'ready'
    assert row['progress'] == 100 and row['count'] == 1
    assert building_jobs.start('job-fixture','osm')['job_id'] == job['job_id']
    assert len(source.read(row['catalog_id'])['features']) == 1


def test_building_and_point_area_limits_use_identical_measurement():
    from app import live
    for bounds in ([0, 0, .15, .15], [30, 60, 30.22, 60.22]):
        # Both paths agree even at high latitude; geodesic/spherical mismatches
        # must not reject a region accepted by the point workflow.
        if planning.rectangle_area_km2(bounds) <= planning.MAX_REGION_AREA_KM2:
            source.validate_area(bounds)
        else:
            with pytest.raises(ValueError, match='300'):
                source.validate_area(bounds)
    assert live.MAX_REGION_AREA_KM2 == planning.MAX_REGION_AREA_KM2
