"""Contracts at the research model / application boundary, using shipped predictions."""
import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient

from app import ingest
from app.main import app
from app.model_runtime import acquisition, identity, optical_allowed

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('chip,mode', [('Nigeria_812045', 'S1+S2'), ('Ghana_313799', 'S1 fallback'),
                                     ('Mekong_333434', 'S1+S2')])
def test_shipped_predictions_and_passport_use_latest_recipe(chip, mode):
    folder = ROOT / 'data/demo' / chip
    meta = json.loads((folder / 'metadata.json').read_text())
    assert meta['model']['recipe_sha256'] == identity()['recipe_sha256']
    assert meta['model']['mode'] == mode
    assert meta['model_threshold'] == identity()['threshold'] == .46
    with rasterio.open(folder / 'flood_mask.tif') as src:
        mask, grid = src.read(1), (src.crs, src.transform, src.shape)
    with rasterio.open(folder / 'flood_probability.tif') as src:
        p = src.read(1)
        assert grid == (src.crs, src.transform, src.shape)
    valid = mask != 255
    assert np.isfinite(p[valid]).all() and ((0 <= p[valid]) & (p[valid] <= 1)).all()
    assert np.array_equal(mask[valid], p[valid] >= .46)
    assert (meta['model']['optical_used_pixels'] > 0) == (mode == 'S1+S2')
    assert any(row['layer'] == 'S2Hand' and row['role'] == 'model_input' for row in meta['source_manifest']) == (mode == 'S1+S2')


def test_future_optical_is_rejected_and_export_reproduces_actual_mode():
    assert optical_allowed(acquisition('Nigeria_812045'))
    assert not optical_allowed(acquisition('Ghana_313799'))
    assert not optical_allowed({'s1_date': '2018-09-21', 's2_date': '2018-09-22'})
    client = TestClient(app)
    for chip in ['Nigeria_812045', 'Ghana_313799']:
        with zipfile.ZipFile(io.BytesIO(client.get('/api/export', params={'scene_id': chip}).content)) as archive:
            meta = json.loads(archive.read('run_metadata.json'))
            assert meta['model']['recipe_sha256'] == identity()['recipe_sha256']
            threshold = next(p for p in meta['parameters'] if p['name'] == 'model_threshold')
            assert threshold['value'] == .46
            assert 'legacy' in meta['metrics_scope']
            commands = '\n'.join(meta['commands'])
            assert '--recipe ml/artifacts/next_optical/recipe.json' in commands
            assert ('--s2 data/source/' in commands) == (chip.startswith('Nigeria'))


def test_ingest_passes_recipe_and_temporally_valid_optics(tmp_path, monkeypatch):
    seen = []
    class Result:
        returncode, stderr, stdout = 0, '', ''
    def run(command, **kwargs):
        seen.append(command)
        return Result()
    monkeypatch.setattr(ingest.subprocess, 'run', run)
    for chip in ['Nigeria_812045', 'Ghana_313799']:
        s1 = tmp_path / f'{chip}_S1Hand.tif'
        (tmp_path / f'{chip}_S2Hand.tif').touch()
        ingest._run_model(s1, tmp_path / 'output')
    assert '--recipe' in seen[0] and '--s2' in seen[0]
    assert '--no-optical' in seen[1] and '--s2' not in seen[1]
