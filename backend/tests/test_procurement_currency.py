"""The SkyFi workspace prices in catalog dollars; the case plan prices in PP 840 rubles. Its export must not blur them."""
import io
import json
import zipfile

from test_procurement import catalog, client  # noqa: F401  (client is a fixture)


def test_export_keeps_catalog_dollars_apart_from_the_case_rubles(client):
    c = catalog()
    saved = client.post('/api/procurement/decisions', json={'catalog_id': c['id']}).json()
    z = zipfile.ZipFile(io.BytesIO(client.get('/api/procurement/decisions/' + saved['id'] + '/export').content))
    features = json.loads(z.read('coverage.geojson'))['features']
    assert features and all(f['properties']['currency'] == 'USD' for f in features)
    report = z.read('comparison.html').decode()
    assert 'долларах США' in report and 'ПП № 840' in report
