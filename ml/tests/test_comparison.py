import numpy as np
import pytest

from floodvalue.comparison import baseline_wins, bootstrap_difference, event_chips, event_table


def _conf(tp: int, fp: int, fn: int, tn: int = 1000) -> dict:
    iou = tp / (tp + fp + fn) if tp + fp + fn else None
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {"iou": iou, "precision": precision, "recall": recall, "n_pixels": tp + fp + fn + tn,
            "n_water": tp + fn, "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def _summary(chips: dict) -> dict:
    tp, fp, fn, tn = (sum(c[k] for c in chips.values()) for k in ("tp", "fp", "fn", "tn"))
    return {"micro": _conf(tp, fp, fn, tn), "chips": len(chips)}


def _part(per_chip: dict[str, tuple[dict, dict]]) -> dict:
    """evaluation.json layout: per method an overall summary, events and per-chip confusions."""
    part = {}
    for index, method in enumerate(("baseline", "main")):
        chips = {name: pair[index] for name, pair in per_chip.items()}
        events: dict[str, dict] = {}
        for name, conf in chips.items():
            events.setdefault(name.rsplit("_", 1)[0], {})[name] = conf
        part[f"all_water/{method}"] = {"overall": _summary(chips),
                                       "events": {e: _summary(c) for e, c in sorted(events.items())},
                                       "per_chip": chips}
    return part


PART = _part({
    "Mekong_1": (_conf(10, 5, 90), _conf(20, 300, 80)),     # few water pixels, many false alarms
    "Mekong_2": (_conf(0, 0, 0), _conf(0, 700, 0)),         # no water at all, pure false alarm
    "Ghana_1": (_conf(500, 50, 450), _conf(800, 60, 150)),
    "Ghana_2": (_conf(900, 100, 100), _conf(950, 40, 50)),
})


def test_bootstrap_of_identical_methods_is_zero():
    counts = np.array([[[5, 1, 2], [5, 1, 2]], [[9, 0, 3], [9, 0, 3]], [[1, 4, 0], [1, 4, 0]]])

    result = bootstrap_difference(counts, resamples=200, seed=0)

    assert result["difference"] == 0.0 and result["ci95"] == [0.0, 0.0]


def test_bootstrap_resamples_chips_and_keeps_the_pooled_point_estimate():
    rng = np.random.default_rng(1)
    tp = rng.integers(50, 500, size=30)
    baseline = np.stack([tp, rng.integers(50, 200, size=30), rng.integers(100, 300, size=30)], axis=1)
    main = baseline + np.array([60, -20, -60])               # better on every chip
    counts = np.stack([baseline, main], axis=1)

    result = bootstrap_difference(counts, resamples=500, seed=0)
    again = bootstrap_difference(counts, resamples=500, seed=0)

    pooled = counts.sum(axis=0)
    expected = pooled[1, 0] / pooled[1].sum() - pooled[0, 0] / pooled[0].sum()
    assert result["difference"] == pytest.approx(expected, abs=1e-4)
    assert 0 < result["ci95"][0] < result["difference"] < result["ci95"][1]
    assert result == again and result["chips"] == 30 and result["share_resamples_not_above_zero"] == 0.0


def test_event_table_reads_both_methods_per_event():
    rows = {row["event"]: row for row in event_table(PART)}

    assert set(rows) == {"Ghana", "Mekong"}
    assert rows["Mekong"]["chips"] == 2 and rows["Mekong"]["water_pixels"] == 100
    assert rows["Mekong"]["main"]["iou"] == round(20 / (20 + 1000 + 80), 4)
    assert rows["Ghana"]["iou_gain"] == round(rows["Ghana"]["main"]["iou"] - rows["Ghana"]["baseline"]["iou"], 4)


def test_event_chips_split_false_alarms_and_misses_by_chip():
    result = event_chips(PART, "Mekong")

    chips = {row["chip"]: row for row in result["chips"]}
    assert chips["Mekong_2"]["main"]["fp"] == 700 and chips["Mekong_2"]["water_pixels"] == 0
    assert chips["Mekong_2"]["share_of_event_false_alarms"] == 0.7
    assert chips["Mekong_1"]["share_of_event_misses"] == 1.0
    assert result["event"]["main"]["precision"] == round(20 / 1020, 4)


def test_baseline_wins_lists_chips_where_the_threshold_scores_higher():
    assert [row["chip"] for row in baseline_wins(PART)] == ["Mekong_1"]   # Mekong_2 has no baseline IoU

    flipped = {**PART, "all_water/main": {**PART["all_water/main"], "per_chip": {
        **PART["all_water/main"]["per_chip"], "Ghana_2": _conf(100, 10, 900)}}}
    wins = baseline_wins(flipped)

    assert [row["chip"] for row in wins] == ["Ghana_2", "Mekong_1"]      # largest water first
    assert wins[0]["water_pixels"] == 1000
    assert wins[0]["iou_gap"] == round(100 / 1010 - 900 / 1100, 4)
