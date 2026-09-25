"""Fit and freeze the threshold baseline on the stored split."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from floodvalue import baseline
from floodvalue.chips import Chip, read_label, read_s1


def _chips(root: Path, names: Sequence[str]):
    for name in names:
        chip = Chip.from_name(name)
        yield read_s1(root, chip), read_label(root, chip)


def fit_baseline(root: Path, split: dict[str, list[str]], out_path: Path) -> dict:
    train = baseline.accumulate(_chips(root, split["train"]))
    valid = baseline.accumulate(_chips(root, split["valid"]))
    model, report = baseline.fit(train, valid)
    curves = {}
    for channel in baseline.CHANNELS:
        thresholds, curve = baseline.threshold_curve(valid[channel])
        keep = slice(None, None, 10)  # every 0.5 dB is enough for the plot
        curves[channel] = {
            "threshold_db": [round(float(t), 3) for t in thresholds[keep]],
            "valid_iou": [c.iou for c in curve[keep]],
        }
    result = {
        "method": "global threshold on one S1 channel in dB; water if value < threshold",
        "selection": "threshold per channel maximises pooled IoU on train; channel chosen by pooled IoU on valid",
        "model": model.as_dict(),
        "per_channel": report,
        "valid_curves": curves,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def load_baseline(path: Path) -> baseline.ThresholdModel:
    data = json.loads(path.read_text(encoding="utf-8"))["model"]
    return baseline.ThresholdModel(data["channel"], data["threshold_db"])
