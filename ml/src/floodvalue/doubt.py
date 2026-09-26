"""Doubt threshold of the service, chosen on the validation part only.

The service calls a pixel doubtful when its map error chance (1 - p where the mask
says water, p where it says dry land; p is the calibrated ensemble probability) is
at least a threshold. The threshold is the lower root of binary entropy at the edge
of the top entropy decile, found with the evaluation's rule
(uncertainty.error_detection: numpy quantiles over at most 2,000,000 pixels drawn
with seed 0) on ``valid``: the part that already fitted Platt and the mask
threshold. Pixels are the evaluation's, finite(VV) & finite(VH) & label != -1, and
the frozen v1 ensemble runs one chip at a time.

test_core and bolivia are scored only after the value is fixed, with that value,
and land under ``report_only``: nothing read from them changes the threshold.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import log2
from pathlib import Path

import numpy as np

from floodvalue.uncertainty import DECILES, binary_entropy, error_detection

SOURCE_PART = "valid"
REPORT_PARTS = ("test_core", "bolivia")
REPORT_ROLE = "report_only_not_used_for_selection"
CHANCE_DECIMALS = 4
RULE = ("map error chance = 1 - p where p >= mask threshold, p elsewhere (p: Platt-calibrated mean logit of "
        "the v1 ensemble); doubtful pixel: chance >= the lower root of binary entropy at the top-decile edge of "
        "entropy on the valid part (numpy quantiles over <= 2,000,000 pixels, seed 0, as in evaluation.json), "
        "rounded to 4 decimals; test parts are scored with the frozen value and reported only")


@dataclass(frozen=True)
class PartPixels:
    """Calibrated p and mask errors on the evaluated pixels of one split part."""

    chips: int
    prob: np.ndarray  # float32, calibrated p
    error: np.ndarray  # bool, (p >= mask threshold) != label
    member_std: np.ndarray | None = None  # float32, spread of member probabilities


def inverse_entropy(bits: float) -> float:
    """The p in (0, 0.5] whose binary entropy is ``bits``."""
    low, high = 1e-9, 0.5
    for _ in range(80):
        middle = (low + high) / 2
        value = -(middle * log2(middle) + (1 - middle) * log2(1 - middle))
        low, high = (middle, high) if value < bits else (low, middle)
    return (low + high) / 2


def map_error_chance(prob: np.ndarray, mask_threshold: float) -> np.ndarray:
    """Chance that the mask is wrong: 1 - p where it says water, p where it says dry land.

    The water test runs in the dtype of ``prob``, exactly as the shipped mask was made.
    """
    prob = np.asarray(prob)
    water = prob >= mask_threshold
    as_float = prob.astype(np.float64)
    return np.where(water, 1.0 - as_float, as_float)


def select_threshold(entropy: np.ndarray, error: np.ndarray, seed: int = 0) -> dict:
    """Top entropy decile edge by the evaluation's rule and the map error chance at that edge."""
    stats = error_detection(entropy, error, seed)
    bits = stats["decile_edges"][DECILES - 1]
    return {
        "pixels_used_for_deciles": stats["pixels_used"],
        "decile_edges": stats["decile_edges"],
        "entropy_top_decile_bits": bits,
        "map_error_chance_threshold": round(inverse_entropy(bits), CHANCE_DECIMALS),
        "auroc_entropy": stats["auroc"],
        "error_rate_by_decile": stats["error_rate_by_decile"],
    }


def doubtful_stats(chance: np.ndarray, error: np.ndarray, chance_threshold: float) -> dict:
    """Share of pixels at or above the chance threshold and how often they are wrong."""
    doubtful = chance >= chance_threshold
    error = np.asarray(error, dtype=bool)
    n, k, wrong = int(chance.size), int(doubtful.sum()), int(error.sum())
    return {
        "pixels": n,
        "doubtful_pixels": k,
        "doubtful_share": k / n if n else None,
        "error_rate": wrong / n if n else None,
        "error_rate_doubtful": float(error[doubtful].mean()) if k else None,
        "error_rate_not_doubtful": float(error[~doubtful].mean()) if k < n else None,
        "errors_inside_doubtful_share": float(error[doubtful].sum() / wrong) if wrong else None,
    }


def build_record(selection: PartPixels, reports: Mapping[str, PartPixels], mask_threshold: float,
                 seed: int = 0) -> dict:
    """Fix the threshold on the selection part, then score the report parts with it.

    ``measure_auroc`` compares the two uncertainty measures on the selection part: the
    service ranks doubt by entropy of calibrated p, not by the ensemble spread.
    """
    entropy = binary_entropy(selection.prob)
    chosen = select_threshold(entropy, selection.error, seed)
    threshold = chosen["map_error_chance_threshold"]
    measures = {"part": SOURCE_PART, "entropy": chosen["auroc_entropy"]}
    if selection.member_std is not None:
        measures["member_std"] = error_detection(selection.member_std, selection.error, seed)["auroc"]
    record = {
        "rule": RULE,
        "source_part": SOURCE_PART,
        "chips": selection.chips,
        "pixels": int(selection.prob.size),
        "subsample_seed": seed,
        "mask_threshold": mask_threshold,
        **chosen,
        "valid_at_threshold": doubtful_stats(map_error_chance(selection.prob, mask_threshold),
                                             selection.error, threshold),
        "measure_auroc": measures,
        "report_only": {
            "role": REPORT_ROLE,
            "note": "scored after the threshold was fixed on valid; own_top_decile_* repeat the old "
                    "per-part rule only to compare with evaluation.json",
            "parts": {},
        },
    }
    for part, pixels in reports.items():
        own = error_detection(binary_entropy(pixels.prob), pixels.error, seed)
        record["report_only"]["parts"][part] = {
            "chips": pixels.chips,
            **doubtful_stats(map_error_chance(pixels.prob, mask_threshold), pixels.error, threshold),
            "auroc_entropy": own["auroc"],
            "auroc_member_std": (error_detection(pixels.member_std, pixels.error, seed)["auroc"]
                                 if pixels.member_std is not None else None),
            "own_top_decile_bits": own["decile_edges"][DECILES - 1],
            "own_top_decile_error_rate": own["error_rate_by_decile"][DECILES - 1],
        }
    return record


def part_pixels(root: Path, names: Sequence[str], members: Sequence, platt, mask_threshold: float) -> PartPixels:
    """Run the ensemble chip by chip and keep p and mask errors of the evaluated pixels."""
    from floodvalue.chips import LABEL_INVALID, LABEL_WATER, Chip, read_label, read_s1, s1_valid
    from floodvalue.ensemble import run_ensemble

    probs, errors, spreads = [], [], []
    for number, name in enumerate(names, 1):
        chip = Chip.from_name(name)
        s1, label = read_s1(root, chip), read_label(root, chip)
        valid = s1_valid(s1) & (label != LABEL_INVALID)
        out = run_ensemble(members, [s1], platt)
        prob = out.prob[0][valid]
        probs.append(prob)
        spreads.append(out.member_std[0][valid])
        errors.append((prob >= mask_threshold) != (label[valid] == LABEL_WATER))
        if number % 10 == 0 or number == len(names):
            print(f"  {number}/{len(names)} chips", file=sys.stderr, flush=True)
    empty = np.zeros(0, dtype=np.float32)
    return PartPixels(len(names), np.concatenate(probs) if probs else empty,
                      np.concatenate(errors) if errors else empty.astype(bool),
                      np.concatenate(spreads) if spreads else empty)


def doubt_threshold(root: Path, split: Mapping[str, Sequence[str]], members: Sequence, platt,
                    mask_threshold: float) -> dict:
    """The record for doubt_threshold.json: valid fixes the value, test parts are only reported."""
    print(f"{SOURCE_PART}:", file=sys.stderr, flush=True)
    selection = part_pixels(root, split[SOURCE_PART], members, platt, mask_threshold)
    reports = {}
    for part in REPORT_PARTS:
        if split.get(part):
            print(f"{part} (report only):", file=sys.stderr, flush=True)
            reports[part] = part_pixels(root, split[part], members, platt, mask_threshold)
    return build_record(selection, reports, mask_threshold)
