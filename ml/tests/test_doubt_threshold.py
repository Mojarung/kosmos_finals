import numpy as np
import pytest

from floodvalue.doubt import (
    REPORT_ROLE,
    PartPixels,
    build_record,
    doubtful_stats,
    inverse_entropy,
    map_error_chance,
    select_threshold,
)
from floodvalue.uncertainty import binary_entropy


def test_inverse_entropy_returns_the_lower_root():
    for p in (0.01, 0.0817, 0.2, 0.45):
        bits = float(binary_entropy(np.array([p]))[0])
        assert inverse_entropy(bits) == pytest.approx(p, abs=1e-6)
    assert inverse_entropy(1.0) == pytest.approx(0.5, abs=1e-6)


def test_map_error_chance_follows_the_mask_threshold():
    prob = np.array([0.05, 0.3, 0.41, 0.9], dtype=np.float32)

    chance = map_error_chance(prob, 0.41)

    assert chance == pytest.approx([0.05, 0.3, 0.59, 0.1], abs=1e-6)


def test_threshold_is_the_top_entropy_decile_of_the_selection_pixels():
    entropy = np.arange(1000, dtype=np.float32) / 1000
    error = entropy >= 0.9

    chosen = select_threshold(entropy, error)

    assert chosen["entropy_top_decile_bits"] == pytest.approx(0.8991, abs=1e-6)
    assert chosen["decile_edges"][9] == chosen["entropy_top_decile_bits"]
    assert chosen["error_rate_by_decile"][9] == 1.0 and chosen["error_rate_by_decile"][8] == 0.0
    assert chosen["map_error_chance_threshold"] == round(inverse_entropy(chosen["entropy_top_decile_bits"]), 4)


def test_doubtful_stats_count_pixels_at_or_above_the_chance_threshold():
    chance = np.array([0.01, 0.05, 0.1, 0.2, 0.3])
    error = np.array([0, 0, 1, 0, 1], dtype=bool)

    stats = doubtful_stats(chance, error, 0.1)

    assert stats["pixels"] == 5 and stats["doubtful_pixels"] == 3
    assert stats["doubtful_share"] == pytest.approx(0.6)
    assert stats["error_rate"] == pytest.approx(0.4)
    assert stats["error_rate_doubtful"] == pytest.approx(2 / 3)
    assert stats["error_rate_not_doubtful"] == 0.0


def _part(seed: int, scale: float) -> PartPixels:
    rng = np.random.default_rng(seed)
    prob = rng.beta(scale, scale, 20_000).astype(np.float32)
    error = rng.random(prob.size) < np.minimum(prob, 1 - prob)
    return PartPixels(chips=4, prob=prob, error=error)


def test_record_takes_the_threshold_from_the_selection_part_only():
    selection = _part(1, 0.3)
    first = build_record(selection, {"test_core": _part(2, 0.3)}, mask_threshold=0.41)
    second = build_record(selection, {"test_core": _part(3, 2.0), "bolivia": _part(4, 0.5)}, mask_threshold=0.41)

    for key in ("decile_edges", "entropy_top_decile_bits", "map_error_chance_threshold"):
        assert first[key] == second[key]
    assert first["source_part"] == "valid" and first["chips"] == 4 and first["pixels"] == 20_000
    report = second["report_only"]
    assert report["role"] == REPORT_ROLE == "report_only_not_used_for_selection"
    assert set(report["parts"]) == {"test_core", "bolivia"}
    threshold = second["map_error_chance_threshold"]
    test_core = report["parts"]["test_core"]
    chance = map_error_chance(_part(3, 2.0).prob, 0.41)
    assert test_core["doubtful_share"] == pytest.approx(float((chance >= threshold).mean()))
