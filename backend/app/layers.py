"""Map layers computed from the frozen rasters on request, not stored with the scene."""

from __future__ import annotations

import io
from functools import lru_cache

import numpy as np

from .catalog import on_refresh, scene_dir
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

OVERLAY_SCALE = 2
ZONE_COLORS = {"object": (201, 128, 73), "uncertainty": (122, 91, 208), "broad": (61, 118, 134)}


def label(draw, xy: tuple[float, float], text: str, color: tuple[int, int, int]) -> None:
    box = draw.textbbox(xy, text)
    draw.rectangle((box[0] - 2, box[1] - 2, box[2] + 2, box[3] + 2), fill=(255, 255, 255))
    draw.text(xy, text, fill=color)


def overlay_png(scene_id: str, bbox: list[float], zones: list[dict], selected: list[str], assets: list[dict]) -> bytes:
    """Candidate zones over the source S1 chip, as the case asks to publish: plan C drawn thick."""
    from PIL import Image, ImageDraw

    radar_path = scene_dir(scene_id) / "radar.png"
    base = Image.open(radar_path).convert("RGBA") if radar_path.is_file() else Image.new("RGBA", (512, 512), "gray")
    width, height = base.width * OVERLAY_SCALE, base.height * OVERLAY_SCALE
    canvas = Image.new("RGBA", (width, height + 44), (255, 255, 255, 255))
    canvas.paste(base.resize((width, height), Image.Resampling.NEAREST), (0, 0), base.resize((width, height)))
    draw = ImageDraw.Draw(canvas)
    west, south, east, north = bbox

    def point(longitude: float, latitude: float) -> tuple[float, float]:
        return (longitude - west) / (east - west) * width, (north - latitude) / (north - south) * height

    for zone in sorted(zones, key=lambda item: item["id"] in selected):
        left, bottom = point(zone["bounds"][0], zone["bounds"][1])
        right, top = point(zone["bounds"][2], zone["bounds"][3])
        chosen = zone["id"] in selected
        draw.rectangle((left, top, right, bottom), outline=ZONE_COLORS[zone["kind"]], width=5 if chosen else 2)
        label(draw, (left + 5, top + 4), zone["id"] + (" C" if chosen else ""), ZONE_COLORS[zone["kind"]])
    for asset in assets:
        x, y = point(asset["longitude"], asset["latitude"])
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=(230, 60, 40), outline=(255, 255, 255), width=2)
        label(draw, (x + 8, y - 7), asset["id"], (150, 30, 20))
    draw.text((8, height + 6), f"{scene_id}: Sentinel-1 VH, candidate zones and plan C (thick): "
                               + (", ".join(selected) or "empty"), fill=(20, 20, 20))
    draw.text((8, height + 24), "orange: asset zones, violet: doubt zones, teal: broad zone B, red dots: assets",
              fill=(20, 20, 20))
    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
