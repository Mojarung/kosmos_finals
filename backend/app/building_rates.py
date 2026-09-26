"""Editable scenario assumptions, not a market appraisal or measured repair bill."""

VERSION = "building-scenario-rub-v1"
SOURCE = "Сценарные ставки команды, 26.09.2026; рыночная оценка не проводилась"
# RUB per square metre of footprint / exposed ground floor. Upper floors are not multiplied in.
_ROWS = [
    ("house", "Частный дом", 55000), ("apartments", "Многоквартирный дом", 75000),
    ("school", "Школа", 85000), ("kindergarten", "Детский сад", 80000),
    ("hospital", "Больница / клиника", 120000), ("university", "Университет", 95000),
    ("warehouse", "Склад", 30000), ("industrial", "Производство", 50000),
    ("retail", "Магазин", 65000), ("office", "Офис", 80000),
    ("hotel", "Гостиница", 90000), ("public", "Общественное здание", 70000),
    ("religious", "Религиозное здание", 72000), ("sports", "Спортивное здание", 68000),
    ("farm", "Сельхозздание", 25000), ("garage", "Гараж", 18000),
    ("utility", "Техническое здание", 45000), ("shed", "Хозпостройка", 12000),
    ("roof", "Навес", 10000), ("other", "Другое — выбранный тип", 40000),
]
RATES = {key: {"label": label, "cost_rub_m2": cost, "damage_ratio": 0.3} for key, label, cost in _ROWS}

TYPE_TAGS = {
    "house": "house", "detached": "house", "semidetached_house": "house", "terrace": "house", "bungalow": "house",
    "apartments": "apartments", "residential": "apartments", "dormitory": "apartments",
    "school": "school", "kindergarten": "kindergarten", "hospital": "hospital", "clinic": "hospital",
    "university": "university", "college": "university", "warehouse": "warehouse",
    "industrial": "industrial", "manufacture": "industrial", "retail": "retail", "supermarket": "retail",
    "office": "office", "commercial": "office", "hotel": "hotel",
    "public": "public", "civic": "public", "townhall": "public", "theatre": "public", "library": "public",
    "community_centre": "public", "fire_station": "public", "police": "public", "train_station": "public",
    "church": "religious", "cathedral": "religious", "mosque": "religious", "synagogue": "religious", "temple": "religious",
    "sports_hall": "sports", "stadium": "sports", "grandstand": "sports", "sports_centre": "sports",
    "farm": "farm", "farm_auxiliary": "farm", "barn": "farm", "cowshed": "farm", "greenhouse": "farm",
    "garage": "garage", "garages": "garage", "parking": "garage", "shed": "shed",
    "service": "utility", "transformer_tower": "utility", "roof": "roof", "carport": "roof",
}


def classify(tags: dict) -> tuple[str, str]:
    # Generic residential/commercial tags do not establish a precise use category.
    for key in ("building:use", "amenity", "shop", "tourism", "leisure", "building"):
        value = tags.get(key, "").lower()
        if key == "shop" and value and value not in {"no", "vacant", "yes"}:
            return "retail", f"{key}={value}"
        if value in {"residential", "commercial"}:
            continue
        if value in TYPE_TAGS:
            return TYPE_TAGS[value], f"{key}={value}"
    return "unknown", "Назначение не указано или неоднозначно"
