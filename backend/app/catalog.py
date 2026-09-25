"""Read compact, reproducible outputs of the frozen Sentinel-1 ensemble.

Two kinds of scenes share one catalog: the committed demo scenes in data/demo
and scenes computed on request by the live ingest in data/runtime.
"""

from __future__ import annotations

import json
import os
import threading
from array import array
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("DEMO_DATA_DIR", ROOT / "data" / "demo"))
RUNTIME_DIR = Path(os.environ.get("FLOODVALUE_RUNTIME_DIR", ROOT / "data" / "runtime"))
_write_lock = threading.Lock()


@lru_cache(maxsize=1)
def _demo_catalog() -> dict:
    return json.loads((DATA_DIR / "scenes.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _runtime_scenes() -> tuple[dict, ...]:
    path = RUNTIME_DIR / "scenes.json"
    if not path.exists():
        return ()
    return tuple(json.loads(path.read_text(encoding="utf-8"))["scenes"])


def _demo_ids() -> set[str]:
    return {scene["chip_id"] for scene in _demo_catalog()["scenes"]}


def scene_catalog() -> dict:
    demo = _demo_catalog()
    ids = _demo_ids()
    scenes = [{**scene, "origin": scene.get("origin", "demo")} for scene in demo["scenes"]]
    scenes += [scene for scene in _runtime_scenes() if scene["chip_id"] not in ids]
    return {"default_chip_id": demo["default_chip_id"], "scenes": scenes}


def default_scene_id() -> str:
    return _demo_catalog()["default_chip_id"]


def known_scene_ids() -> set[str]:
    return {scene["chip_id"] for scene in scene_catalog()["scenes"]}


def scene_dir(scene_id: str) -> Path:
    return (DATA_DIR if scene_id in _demo_ids() else RUNTIME_DIR) / scene_id


def refresh() -> None:
    """Forget cached catalog and scene files, e.g. after a live scene was added."""
    for cached in (_demo_catalog, _runtime_scenes, metadata, mask, float_map):
        cached.cache_clear()


def register_runtime_scene(row: dict) -> None:
    """Add or replace one live scene in data/runtime/scenes.json atomically."""
    with _write_lock:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        path = RUNTIME_DIR / "scenes.json"
        scenes = json.loads(path.read_text(encoding="utf-8"))["scenes"] if path.exists() else []
        scenes = [scene for scene in scenes if scene["chip_id"] != row["chip_id"]] + [row]
        partial = path.with_suffix(".json.partial")
        partial.write_text(json.dumps({"scenes": scenes}, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(partial, path)
        refresh()


@lru_cache(maxsize=16)
def metadata(scene_id: str | None = None) -> dict:
    scene_id = scene_id or default_scene_id()
    if scene_id not in known_scene_ids():
        raise KeyError(scene_id)
    return json.loads((scene_dir(scene_id) / "metadata.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def quality() -> dict:
    return json.loads((DATA_DIR / "quality.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=48)
def mask(scene_id: str, name: str) -> bytes:
    if name not in {"water", "model", "baseline"}:
        raise KeyError(name)
    values = (scene_dir(scene_id) / f"{name}_mask.bin").read_bytes()
    info = metadata(scene_id)
    if len(values) != info["width"] * info["height"]:
        raise ValueError(f"Invalid {name} mask dimensions")
    return values


@lru_cache(maxsize=32)
def float_map(scene_id: str, name: str) -> array:
    if name not in {"probability", "uncertainty"}:
        raise KeyError(name)
    result = array("f")
    result.frombytes((scene_dir(scene_id) / f"{name}.f32").read_bytes())
    info = metadata(scene_id)
    if len(result) != info["width"] * info["height"]:
        raise ValueError(f"Invalid {name} dimensions")
    return result
