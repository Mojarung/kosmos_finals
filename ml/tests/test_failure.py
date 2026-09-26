import numpy as np

from floodvalue.failure import chip_diagnostics


def _scene():
    """Two water bodies: a large dark lake the model finds and grey fragments it misses."""
    size = 128
    vv = np.full((2, size, size), -8.0, np.float32)
    vv[1] = -14.0
    label = np.zeros((size, size), np.int8)
    label[10:70, 10:70] = 1                      # large lake, 60 x 60
    vv[0, 10:70, 10:70], vv[1, 10:70, 10:70] = -18.0, -27.0
    for row in range(90, 120, 6):                # grey fragments, 3 x 3
        for col in range(90, 120, 6):
            label[row:row + 3, col:col + 3] = 1
            vv[0, row:row + 3, col:col + 3], vv[1, row:row + 3, col:col + 3] = -15.0, -22.5
    p = np.where(label == 1, 0.05, 0.01).astype(np.float32)
    p[10:70, 10:70] = 0.95
    std = np.where((label == 1) & (p < 0.5), 0.2, 0.02).astype(np.float32)
    baseline = vv[1] < -23.6
    jrc = np.zeros((size, size), np.uint8)
    return vv, label, p, std, baseline, jrc


def test_diagnostics_separate_grey_fragments_from_dark_cores():
    s1, label, p, std, baseline, jrc = _scene()
    result = chip_diagnostics(s1, label, p, std, baseline, jrc, threshold=0.41, baseline_db=-23.6)
    assert result["model"]["recall"] < 1 and result["water_pixels"] == 60 * 60 + 25 * 9
    by_bin = {row["vh_bin"]: row for row in result["recall_by_vh"]}
    assert by_bin["< -26"]["model_recall"] == 1.0
    assert by_bin["-23.6 .. -21"]["model_recall"] == 0.0
    assert result["fragments"]["share_in_components_below_100px"] == round(25 * 9 / (60 * 60 + 25 * 9), 3)
    assert result["missed"]["member_std_mean"] == 0.2
    assert result["missed"]["baseline_catches_share"] == 0.0
    assert result["permanent_water_share"] == 0.0


def test_texture_is_rougher_where_the_model_misses():
    s1, label, p, std, baseline, jrc = _scene()
    result = chip_diagnostics(s1, label, p, std, baseline, jrc, threshold=0.41, baseline_db=-23.6)
    assert result["texture"]["missed_local_std_median"] > result["texture"]["found_local_std_median"]


def test_separability_compares_raw_vh_with_model_probability():
    s1, label, p, std, baseline, jrc = _scene()
    p[100:128, 0:20] = 0.3                         # bright dry land the model wrongly ranks above missed water
    result = chip_diagnostics(s1, label, p, std, baseline, jrc, threshold=0.41, baseline_db=-23.6)
    assert result["separability"]["auc_vh"] == 1.0
    assert 0.5 < result["separability"]["auc_p"] < 1.0
