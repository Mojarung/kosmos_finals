"""Map layers computed from the frozen rasters on request, not stored with the scene."""

from __future__ import annotations

import io
from functools import lru_cache

import numpy as np

from .catalog import on_refresh
from .zoning import DOUBT_THRESHOLD, doubt_map


@lru_cache(maxsize=16)
def doubt_png(scene_id: str) -> bytes:
    """Map error chance in violet: faint below the doubt threshold, solid above it."""
    from PIL import Image

    doubt, valid = doubt_map(scene_id)
    strength = np.clip(doubt / 0.5, 0, 1)
    alpha = np.where(doubt >= DOUBT_THRESHOLD, 90 + 150 * strength, 140 * strength ** 1.5)
    layer = np.zeros((*doubt.shape, 4), dtype=np.uint8)
    layer[..., :3] = (122, 91, 208)
    layer[..., 3] = np.where(valid, alpha, 0).astype(np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(layer, "RGBA").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


on_refresh(doubt_png.cache_clear)
