"""Label-only proxy diagnostics; never used by the operational plan."""

from __future__ import annotations

from functools import lru_cache

from .catalog import mask, metadata


@lru_cache(maxsize=8)
def asset_proxy_check(scene_id: str) -> dict:
    info = metadata(scene_id)
    reference = mask(scene_id, "water")
    west, south, east, north = info["bbox"]
    errors_p = []
    errors_loss = []
    modeled_total = proxy_total = 0.0
    for asset in info["assets"]:
        if asset["p_flood"] is None:
            continue
        col = min(info["width"] - 1, max(0, int(
            (asset["longitude"] - west) / (east - west) * info["width"])))
        row = min(info["height"] - 1, max(0, int(
            (north - asset["latitude"]) / (north - south) * info["height"])))
        values = [reference[r * info["width"] + c]
                  for r in range(max(0, row - 1), min(info["height"], row + 2))
                  for c in range(max(0, col - 1), min(info["width"], col + 2))]
        valid = [value for value in values if value != 255]
        if not valid:
            continue
        proxy_p = sum(value == 1 for value in valid) / len(valid)
        modeled = asset["p_flood"] * asset["value_rub"] * asset["vulnerability"]
        proxy = proxy_p * asset["value_rub"] * asset["vulnerability"]
        errors_p.append(abs(asset["p_flood"] - proxy_p))
        errors_loss.append(abs(modeled - proxy))
        modeled_total += modeled
        proxy_total += proxy
    return {
        "status": "diagnostic_label_proxy_not_observed_damage",
        "chip_id": scene_id, "n_assets": len(errors_p),
        "mean_absolute_probability_error": round(sum(errors_p) / len(errors_p), 4) if errors_p else None,
        "mean_absolute_proxy_loss_error_rub": round(sum(errors_loss) / len(errors_loss), 2) if errors_loss else None,
        "modeled_total_rub": round(modeled_total, 2),
        "label_proxy_total_rub": round(proxy_total, 2),
        "method": "same independent 3x3 asset neighborhoods in frozen p and test manual water label; V/q fixed",
        "limitation": "The manual water label is not measured monetary loss; this only tests the p-to-EL propagation.",
    }
