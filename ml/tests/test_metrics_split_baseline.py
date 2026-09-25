import numpy as np
import pytest

from floodvalue.baseline import ChannelHistograms, ThresholdModel, accumulate, best_threshold, fit
from floodvalue.chips import Chip
from floodvalue.metrics import Confusion, confusion, summarize
from floodvalue.split import boxes_touch, build_split, touching_neighbours


def test_confusion_ignores_invalid_pixels():
    pred = np.array([[1, 1], [0, 0]], dtype=bool)
    target = np.array([[1, 0], [1, 1]], dtype=bool)
    valid = np.array([[1, 1], [1, 0]], dtype=bool)

    result = confusion(pred, target, valid)

    assert result == Confusion(tp=1, fp=1, fn=1, tn=0)
    assert result.iou == pytest.approx(1 / 3)
    assert result.f1 == pytest.approx(0.5)


def test_metrics_are_none_when_denominator_is_zero():
    empty = Confusion(tn=10)

    assert empty.iou is None
    assert empty.precision is None
    assert empty.recall is None


def test_summarize_pools_pixels_and_skips_empty_chips_in_mean():
    per_chip = {"a": Confusion(tp=1, fp=1), "b": Confusion(tp=3, fn=1), "c": Confusion(tn=5)}

    summary = summarize(per_chip)

    assert summary["micro"]["iou"] == pytest.approx(4 / 6)
    assert summary["chip_mean_iou"] == pytest.approx((0.5 + 0.75) / 2)
    assert summary["chips_with_union"] == 2


def test_chip_name_parsing_keeps_hyphenated_event():
    chip = Chip.from_filename("Sri-Lanka_101973_S1Hand.tif")

    assert chip == Chip("Sri-Lanka", "101973")
    assert Chip.from_name(chip.name) == chip


def test_boxes_touch_with_shared_edge_and_gap():
    left = (0.0, 0.0, 1.0, 1.0)

    assert boxes_touch(left, (1.0, 0.0, 2.0, 1.0))
    assert not boxes_touch(left, (1.01, 0.0, 2.0, 1.0))


def test_touching_neighbours_only_within_same_event():
    bounds = {"A_1": (0, 0, 1, 1), "A_2": (1, 0, 2, 1), "B_3": (1, 0, 2, 1), "A_4": (5, 5, 6, 6)}

    result = touching_neighbours(["A_2", "B_3", "A_4"], ["A_1"], bounds)

    assert result == {"A_2": ["A_1"]}


def test_build_split_moves_touching_test_chips_out_of_core():
    author = {"train": ["A_1"], "valid": [], "test": ["A_2", "A_4"], "bolivia": ["Bolivia_9"]}
    bounds = {"A_1": (0, 0, 1, 1), "A_2": (1, 0, 2, 1), "A_4": (5, 5, 6, 6), "Bolivia_9": (9, 9, 10, 10)}

    split = build_split(author, bounds)

    assert split["parts"]["test_core"] == ["A_4"]
    assert split["parts"]["test_adjacent"] == ["A_2"]
    assert split["counts"]["bolivia"] == 1


def _synthetic_chip(rng, water_db, dry_db, size=64):
    label = (rng.random((size, size)) < 0.3).astype(np.int8)
    vv = np.where(label == 1, water_db, dry_db) + rng.normal(0, 0.5, (size, size))
    vh = vv - 7.0
    s1 = np.stack([vv, vh]).astype(np.float32)
    s1[:, 0, 0] = np.nan
    label[1, 1] = -1
    return s1, label


def test_baseline_recovers_separating_threshold():
    rng = np.random.default_rng(0)
    train = accumulate(_synthetic_chip(rng, -22.0, -8.0) for _ in range(3))
    valid = accumulate(_synthetic_chip(rng, -22.0, -8.0) for _ in range(2))

    model, report = fit(train, valid)

    assert -21.0 < report["VV"]["threshold_db"] < -9.0
    assert report[model.channel]["valid"]["iou"] > 0.99


def test_threshold_model_predicts_below_threshold_and_marks_nan_invalid():
    s1 = np.array([[[-20.0, -5.0], [np.nan, -30.0]], [[-25.0, -12.0], [-20.0, -40.0]]], dtype=np.float32)

    water, valid = ThresholdModel("VH", -15.0).predict(s1)

    assert valid.tolist() == [[True, True], [False, True]]
    assert water.tolist() == [[True, False], [False, True]]


def test_best_threshold_on_hand_made_histogram():
    hist = ChannelHistograms(water=np.array([5, 5, 0, 0]), dry=np.array([0, 1, 5, 5]))

    threshold, conf = best_threshold(hist)

    assert conf.tp == 10 and conf.fp == 1
