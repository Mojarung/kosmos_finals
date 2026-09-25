"""Authors' Sen1Floods11 split with a spatial-adjacency check against the training chips.

The official hand-labeled split samples chips at random inside each event, so the
validation and test parts share events and scenes with train. We keep the authors'
lists for comparability, flag every evaluation chip whose footprint touches a train
chip, and report the touching ones as a separate ``test_adjacent`` part. Bolivia is
the only event that is fully held out.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from floodvalue.chips import Chip, s1_bounds

SPLIT_DIR = Path("splits") / "flood_handlabeled"
AUTHOR_FILES = {
    "train": "flood_train_data.csv",
    "valid": "flood_valid_data.csv",
    "test": "flood_test_data.csv",
    "bolivia": "flood_bolivia_data.csv",
}
# One pixel of the 10 m grid in degrees; footprints closer than this count as touching.
TOUCH_TOLERANCE_DEG = 9e-5

Bounds = tuple[float, float, float, float]


def read_author_split(root: Path) -> dict[str, list[str]]:
    parts: dict[str, list[str]] = {}
    for part, filename in AUTHOR_FILES.items():
        with (root / SPLIT_DIR / filename).open(newline="", encoding="utf-8") as handle:
            parts[part] = [Chip.from_filename(row[0]).name for row in csv.reader(handle) if row]
    return parts


def boxes_touch(a: Bounds, b: Bounds, tolerance: float = TOUCH_TOLERANCE_DEG) -> bool:
    """True if two (left, bottom, right, top) boxes overlap or are within ``tolerance``."""
    return not (
        a[2] + tolerance < b[0] or b[2] + tolerance < a[0] or a[3] + tolerance < b[1] or b[3] + tolerance < a[1]
    )


def touching_neighbours(
    candidates: Sequence[str], reference: Sequence[str], bounds: Mapping[str, Bounds]
) -> dict[str, list[str]]:
    """Map each candidate chip to the reference chips of the same event whose footprint touches it."""
    by_event: dict[str, list[str]] = {}
    for name in reference:
        by_event.setdefault(Chip.from_name(name).event, []).append(name)
    result: dict[str, list[str]] = {}
    for name in candidates:
        same_event = by_event.get(Chip.from_name(name).event, [])
        hits = [ref for ref in same_event if boxes_touch(bounds[name], bounds[ref])]
        if hits:
            result[name] = hits
    return result


def build_split(author: Mapping[str, Sequence[str]], bounds: Mapping[str, Bounds]) -> dict:
    """Final parts: train, valid, test_core (no contact with train), test_adjacent, bolivia."""
    test_touch = touching_neighbours(author["test"], author["train"], bounds)
    valid_touch = touching_neighbours(author["valid"], author["train"], bounds)
    parts = {
        "train": list(author["train"]),
        "valid": list(author["valid"]),
        "test_core": [name for name in author["test"] if name not in test_touch],
        "test_adjacent": [name for name in author["test"] if name in test_touch],
        "bolivia": list(author["bolivia"]),
    }
    return {
        "source": "Sen1Floods11 v1.1 splits/flood_handlabeled (authors' random per-event split)",
        "rule": "authors' lists; test chips whose footprint touches a train chip of the same event "
        f"(tolerance {TOUCH_TOLERANCE_DEG} deg) are moved to test_adjacent; bolivia is a held-out event",
        "seed": None,
        "parts": parts,
        "counts": {part: len(names) for part, names in parts.items()},
        "events": {part: dict(Counter(Chip.from_name(n).event for n in names)) for part, names in parts.items()},
        "valid_touching_train": valid_touch,
        "test_touching_train": test_touch,
    }


def make_split(root: Path, out_path: Path) -> dict:
    author = read_author_split(root)
    names = {name for names in author.values() for name in names}
    bounds = {name: s1_bounds(root, Chip.from_name(name)) for name in sorted(names)}
    split = build_split(author, bounds)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(split, indent=2, ensure_ascii=False), encoding="utf-8")
    return split


def load_split(path: Path) -> dict[str, list[str]]:
    return json.loads(path.read_text(encoding="utf-8"))["parts"]
