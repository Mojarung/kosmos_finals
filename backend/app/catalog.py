"""Read compact, reproducible outputs of the frozen Sentinel-1 ensemble."""

from __future__ import annotations

import json
import os
from array import array
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(os.environ.get("DEMO_DATA_DIR", Path(__file__).resolve().parents[2] / "data" / "demo"))


@lru_cache(maxsize=1)
def scene_catalog() -> dict:
    return json.loads((DATA_DIR / "scenes.json").read_text(encoding="utf-8"))


def default_scene_id() -> str:
    return scene_catalog()["default_chip_id"]


def known_scene_ids() -> set[str]:
    return {scene["chip_id"] for scene in scene_catalog()["scenes"]}


@lru_cache(maxsize=8)
def metadata(scene_id: str | None = None) -> dict:
    scene_id = scene_id or default_scene_id()
    if scene_id not in known_scene_ids():
        raise KeyError(scene_id)
    return json.loads((DATA_DIR / scene_id / "metadata.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def quality() -> dict:
    return json.loads((DATA_DIR / "quality.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=24)
def mask(scene_id: str, name: str) -> bytes:
    if name not in {"water", "model", "baseline"}:
        raise KeyError(name)
    values = (DATA_DIR / scene_id / f"{name}_mask.bin").read_bytes()
    info = metadata(scene_id)
    if len(values) != info["width"] * info["height"]:
        raise ValueError(f"Invalid {name} mask dimensions")
    return values


@lru_cache(maxsize=16)
def float_map(scene_id: str, name: str) -> array:
    if name not in {"probability", "uncertainty"}:
        raise KeyError(name)
    result = array("f")
    result.frombytes((DATA_DIR / scene_id / f"{name}.f32").read_bytes())
    info = metadata(scene_id)
    if len(result) != info["width"] * info["height"]:
        raise ValueError(f"Invalid {name} dimensions")
    return result
