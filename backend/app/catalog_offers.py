"""One offer shape for every live imagery catalog (Roscosmos, SCANEX, Copernicus, SkyFi).

The gallery, comparison, recommendation and export read these fields, so a new source only
has to map its records here. Prices of Russian sources are the case's PP RF 840 formula
(planning.quote, scenario base rate); free data costs 0.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from shapely.geometry import mapping

from .planning import OPERATIONAL_DAYS, quote


def pp840_price(area_km2: float, sensor: str, resolution_m: float, captured: str, on: date) -> tuple[float, float]:
    """Case formula for an existing image cropped to the AOI; the order minimum is 1 km²."""
    billable = max(area_km2, 1.0)
    age = (on - date.fromisoformat(captured)).days
    order = {"candidate_id": "offer", "area_km2": billable, "sensor_type": sensor,
             "resolution_m": resolution_m, "processing_level": "L2", "usage_type": "internal",
             "acquisition_type": "operational" if age <= OPERATIONAL_DAYS else "archive",
             "guaranteed_purchase": False, "observation_at": captured}
    total = float(quote([order], calculation_date=on)["total_rub"])
    return round(total, 2), round(total / billable, 2)


def make_offer(*, provider: str, identifier: str, title: str, sensor: str, footprint, aoi, resolution_m: float | None,
               captured: str, timestamp: str, cloud, preview: str | None, event_date: str | None, on: date,
               order_url: str, free: bool = False, price_note: str | None = None, extra: dict | None = None) -> dict | None:
    """Offer for the part of the footprint inside the AOI; None when they do not overlap."""
    from .procurement import area

    if not footprint.is_valid:
        footprint = footprint.buffer(0)
    overlap = footprint.intersection(aoi)
    if overlap.is_empty or area(overlap) <= 0:
        return None
    sqkm = area(overlap)
    if free:
        price, unit, status = 0.0, 0.0, "free_open_data"
    else:
        price, unit = pp840_price(sqkm, sensor, resolution_m or 1.0, captured, on)
        status = "pp840_case_formula"
    return {"id": identifier, "archive_id": identifier, "provider": provider, "title": title,
            "product_type": "SAR" if sensor == "sar" else "OPTICAL",
            "geometry": mapping(overlap), "footprint": mapping(footprint), "coverage_geometry": mapping(overlap),
            "area_km2": round(sqkm, 4), "coverage_km2": round(sqkm, 4),
            "coverage_percent": round(min(100, sqkm / area(aoi) * 100), 2),
            "technical_min_km2": None if free else 1, "technical_max_km2": None,
            "price": price, "unit_price": unit, "currency": "RUB",
            "resolution_m": resolution_m, "source_gsd_cm": round(resolution_m * 100) if resolution_m else None,
            "delivery_hours": 0 if free else None,
            "capture_date": captured, "capture_timestamp": timestamp,
            "days_from_event": (date.fromisoformat(captured) - date.fromisoformat(event_date)).days if event_date else None,
            "price_status": status, "price_note": price_note,
            "price_source": "бесплатные открытые данные" if free else "planning.quote (ПП РФ № 840, сценарная ставка)",
            "quote_at": datetime.now(timezone.utc).isoformat(), "quote_validated": False, "quality": "unknown",
            "full_scene_only": False, "open_data": free,
            "order_issue": None if free or sqkm >= 1 else "Меньше 1 км²: оплата по минимуму заказа",
            "preview_url": preview, "cloud_percent": cloud, "order_url": order_url, **(extra or {})}
