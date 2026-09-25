"""Label-only proxy diagnostics; never used by the operational plan."""

from __future__ import annotations

from functools import lru_cache

from .catalog import mask, metadata, quality

CONTROL_POINT_RULE = ("the ten seeded portfolio asset pixels (placed independently of p, masks and labels); "
                      "points whose manual label is -1 are skipped and counted")


def _pixel(info: dict, asset: dict) -> tuple[int, int]:
    if "row" in asset and "col" in asset:
        return asset["row"], asset["col"]
    west, south, east, north = info["bbox"]
    col = min(info["width"] - 1, max(0, int((asset["longitude"] - west) / (east - west) * info["width"])))
    row = min(info["height"] - 1, max(0, int((north - asset["latitude"]) / (north - south) * info["height"])))
    return row, col


@lru_cache(maxsize=16)
def asset_proxy_check(scene_id: str) -> dict:
    """Compare EL = p·V·q with EL_ref = y·V·q, y ∈ {0, 1} the manual label at the asset pixel."""
    info = metadata(scene_id)
    reference = mask(scene_id, "water")
    rows, skipped = [], 0
    for asset in info["assets"]:
        if asset.get("p_flood") is None:
            skipped += 1
            continue
        row, col = _pixel(info, asset)
        label = reference[row * info["width"] + col]
        if label == 255:
            skipped += 1
            continue
        value = asset["value_rub"] * asset["vulnerability"]
        rows.append({"asset_id": asset["id"], "p_flood": asset["p_flood"], "y": int(label),
                     "el_rub": round(asset["p_flood"] * value, 2), "el_ref_rub": round(label * value, 2)})
    count = len(rows)
    modeled = sum(item["el_rub"] for item in rows)
    proxy = sum(item["el_ref_rub"] for item in rows)
    if info.get("reference_status") == "no_manual_label":
        status = "not_checked_no_manual_label"
    elif not count:
        status = "not_checked_no_valid_label_at_assets"
    else:
        status = "diagnostic_label_proxy_not_observed_damage"
    mean_p_error = round(sum(abs(item["p_flood"] - item["y"]) for item in rows) / count, 4) if count else None
    mean_loss_error = round(sum(abs(item["el_rub"] - item["el_ref_rub"]) for item in rows) / count, 2) if count else None
    return {
        "status": status, "chip_id": scene_id,
        "control_point_rule": CONTROL_POINT_RULE,
        "labels_independent": info.get("metrics_independent", True),
        "n_points": count, "n_skipped_invalid_label": skipped, "n_assets": count,
        "fixed_values": "case asset table V and q",
        "error_metric": "mean absolute error of EL against EL_ref in RUB; bias = sum(EL) - sum(EL_ref)",
        "mean_absolute_probability_error": mean_p_error,
        "mean_absolute_error_rub": mean_loss_error,
        "mean_absolute_proxy_loss_error_rub": mean_loss_error,
        "bias_rub": round(modeled - proxy, 2) if count else None,
        "modeled_total_rub": round(modeled, 2), "label_proxy_total_rub": round(proxy, 2),
        "rows": rows,
        "test_core": quality().get("proxy_test_core"),
        "method": "EL = p·V·q against EL_ref = y·V·q with the manual label y at each control point pixel",
        "limitation": "The manual water label is not measured monetary loss; this only tests the p-to-EL propagation.",
    }
