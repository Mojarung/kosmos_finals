import numpy as np
import pytest

from floodvalue.external_data import geographic_overlaps, kuro_water_target, linear_power_db


def test_linear_power_not_amplitude_and_no_data():
    result = linear_power_db(np.array([1, .1, .01, 0, -1, np.nan]))
    np.testing.assert_allclose(result[:3], [0, -10, -20], atol=1e-5)
    assert np.isnan(result[3:]).all()


def test_permanent_and_flood_both_count_as_water():
    target, valid = kuro_water_target(np.array([0, 1, 2, 3, 2]), np.array([1, 1, 1, 1, 0]))
    np.testing.assert_array_equal(target, [False, True, True, False, False])
    np.testing.assert_array_equal(valid, [True, True, True, False, False])
    with pytest.raises(ValueError, match="unknown"):
        kuro_water_target(np.array([9]), np.array([1]))


def test_geometry_screen_includes_neighbours():
    pytest.importorskip("shapely", reason="external geometry audit requires the research dependency group")
    geometry = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
    protected = {"touch": (1, .2, 2, .8), "near": (1.01, .2, 2, .8), "far": (3, 3, 4, 4)}
    assert geographic_overlaps(geometry, protected) == ["near", "touch"]
