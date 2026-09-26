import numpy as np

from floodvalue.chips import s1_valid
from floodvalue.failure import chip_diagnostics, label_shift_search, shift_label, spread_of


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


def test_shift_label_moves_content_and_marks_vacated_pixels_invalid():
    label = np.arange(1, 17, dtype=np.int8).reshape(4, 4) % 2
    moved = shift_label(label, 1, -2)
    assert (moved[1:, :2] == label[:3, 2:]).all()
    assert (moved[0] == -1).all() and (moved[:, 2:] == -1).all()
    assert (shift_label(label, 0, 0) == label).all()


def test_label_shift_search_recovers_a_misregistered_label():
    s1, label, p, std, baseline, jrc = _scene()
    displaced = shift_label(label, 4, -6)          # label drawn 4 px too low and 6 px too far left
    result = label_shift_search(s1_valid(s1), displaced, p >= 0.41, baseline)

    assert result["shifts_px"] == list(range(-16, 17, 2))
    assert len(result["model_iou"]) == 17 and all(len(row) == 17 for row in result["model_iou"])
    best = result["best"]["model"]
    assert (best["dy"], best["dx"]) == (-4, 6)
    aligned = chip_diagnostics(s1, label, p, std, baseline, jrc, threshold=0.41, baseline_db=-23.6)
    assert best["iou"] == round(aligned["model"]["tp"] / (aligned["model"]["tp"] + aligned["model"]["fp"]
                                                          + aligned["model"]["fn"]), 4)
    assert best["gain_vs_zero_shift"] == round(best["iou"] - result["zero_shift"]["model"], 4) > 0.2


def test_label_shift_search_keeps_zero_shift_for_an_aligned_label():
    s1, label, p, _, baseline, _ = _scene()
    result = label_shift_search(s1_valid(s1), label, p >= 0.41, baseline)

    assert (result["best"]["model"]["dy"], result["best"]["model"]["dx"]) == (0, 0)
    assert result["best"]["model"]["gain_vs_zero_shift"] == 0.0


def test_doubt_shares_follow_the_service_rule():
    s1, label, p, std, baseline, jrc = _scene()
    s1[:, 0:4, :] = np.nan                         # 512 px without S1: never counted
    p[0:4, :] = 0.3
    label[4:6, :] = -1                             # 256 px without a label: counted, S1 is valid
    p[4:6, :] = 0.3
    p[90:100][label[90:100] == 1] = 0.2            # 10 of 25 missed fragments become doubtful (90 px)
    p[20:30, 90:100] = 0.3                         # dry, below the mask threshold, doubtful
    p[40:50, 90:100] = 0.6                         # false alarm, chance 0.4, doubtful
    result = chip_diagnostics(s1, label, p, std, baseline, jrc, threshold=0.41, baseline_db=-23.6,
                              chance_threshold=0.0826)

    doubt = result["doubt"]
    assert doubt["chance_threshold"] == 0.0826
    assert doubt["valid_s1_pixels"] == 128 * 128 - 512
    assert doubt["doubtful_pixels"] == 256 + 90 + 100 + 100
    assert doubt["doubtful_share_of_valid_s1"] == round(546 / (128 * 128 - 512), 3)
    assert doubt["missed_water_doubtful_share"] == round(90 / 225, 3)
    assert doubt["false_alarm_doubtful_share"] == 1.0
    assert doubt["found_water_doubtful_share"] == 0.0


def test_spread_of_skips_chips_without_a_value():
    items = [{"doubt": {"a": 0.1, "b": None}}, {"doubt": {"a": 0.3, "b": 0.5}}, {"doubt": {"a": 0.2, "b": 0.7}}]

    assert spread_of(items, "doubt", ("a", "b")) == {
        "a": {"chips": 3, "median": 0.2, "min": 0.1, "max": 0.3},
        "b": {"chips": 2, "median": 0.6, "min": 0.5, "max": 0.7},
    }
