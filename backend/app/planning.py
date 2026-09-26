"""Procurement math for frozen model outputs and a disclosed scenario tariff."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from math import cos, pi

import numpy as np
from pyproj import Transformer

from .reliability import p_check
from .zoning import DOUBT_THRESHOLD


BASE_RATE = Decimal("788.64")  # Scenario from the case materials, not an official quote.
BASE_RATE_SOURCE = "case_materials_control_example"
LEGAL_EDITION = "PP_RF_840_2025-08-27"
FORMULA = "B*K*O*P*T*R"
# PP RF 840 tables in the case edition: processing level О, usage terms П, freshness Т.
PROCESSING_COEFS = {"L0": Decimal("1"), "L1": Decimal("1"), "L2": Decimal("1.2")}
USAGE_COEFS = {"internal": Decimal("1"), "limited": Decimal("1.2"), "unrestricted": Decimal("1.5")}
FRESHNESS_NEW = Decimal("1.8")          # new acquisition with guaranteed purchase
FRESHNESS_OPERATIONAL = Decimal("1")    # existing imagery not older than 90 calendar days
FRESHNESS_ARCHIVE = Decimal("0.6")      # existing imagery older than 90 calendar days
OPERATIONAL_DAYS = 90
# Volume discount P = min(1; max(floor; slope·ln(S/r²) + intercept)), S in km², r in metres.
DISCOUNT_CURVES = {
    "optical": (Decimal("-0.058"), Decimal("1.037"), Decimal("0.2")),
    "sar": (Decimal("-0.020"), Decimal("1.031"), Decimal("0.7")),
}
REQUIRED_ORDER_FIELDS = ("area_km2", "sensor_type", "resolution_m", "processing_level", "usage_type",
                         "acquisition_type", "guaranteed_purchase")
GROUP_RULE = ("one discount group per basket, sensor_type, resolution_m and order calendar year (orders are placed "
              "on the calculation date); S = area of earlier orders of the group (previous_group_area_km2, 0 by "
              "default) + area of the group in this basket, recomputed for every basket; "
              "discount_group_id = <basket>_<sensor_type>_<resolution>m_<year>")
ROUNDING = {"area_km2": "0.001 ROUND_HALF_UP; 1 km² minimum checked before rounding",
            "discount_coef": "9 digits ROUND_HALF_UP",
            "unit_price_rub_km2": "kopecks ROUND_HALF_UP of B*O*P*T*R",
            "cost_rub": "kopecks ROUND_HALF_UP of unit_price_rub_km2*K",
            "total_rub": "sum of the rounded lines"}
MONEY = Decimal("0.01")
AREA = Decimal("0.001")
DISCOUNT = Decimal("0.000000001")
TO_EQUAL_AREA = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True)
MAX_EXACT_ZONES = 16
PRIORITIES = {"risk", "information"}

# Scenario products offered over every footprint; none of these terms is a supplier quote.
OPTICAL_DELIVERY_HOURS = 6   # new 1 m optics: tasking and delivery inside the 6-12 h decision window
SAR_DELIVERY_HOURS = 4       # new 3 m radar: all-weather tasking, faster delivery
ARCHIVE_LEAD_DAYS = 180      # the last clear dry-season optical frame; any lead above 90 days gives T = 0.6
CANDIDATE_RULE = ("footprints: 1.12 km square around each asset, uncertainty rectangles from the doubt map and the "
                  "broad selected-region rectangle, min 1 km²; each footprint is offered as three catalog orders: "
                  "<id> new optical 1 m L2 (6 h, scenario), <id>-SAR new SAR 3 m L1 all-weather (4 h, scenario), "
                  f"<id>-ARC archive optical 1 m L2 acquired {ARCHIVE_LEAD_DAYS} days before the event (context)")
PRODUCT_RULE = ("per footprint: new optical 1 m if it arrives by the decision deadline, otherwise new SAR 3 m if it "
                "does; the archive frame is context only, never bought as an observation of current water and never "
                "counted in coverage; late products add no coverage. B takes the broad footprint ZALL under this rule "
                "(the optical order when nothing arrives in time, shown late); C searches every basket of footprints "
                "exactly, each footprint with the product the rule takes")


# New optics sees water only through a clear sky, and a flood usually comes with cloud. The service
# cannot see the cloud forecast for the tasking window, so a new optical order is assumed usable by
# the deadline: a scenario, like its delivery time. Radar needs no clear sky.
SUITABILITY_RULE = ("new optical 1 m needs a cloud-free pass; cloud cover at the tasking time is not checked, so it is "
                    "assumed usable by the deadline (suitability_status=scenario_usable, cloud_check=not_checked) "
                    "and its coverage is an upper bound; new SAR 3 m sees water through cloud (all_weather); "
                    "the archive frame is context only; before a real order check the cloud forecast and switch "
                    "footprints under closed sky to the -SAR order")


def rounded(value: float | Decimal, unit: Decimal) -> Decimal:
    return Decimal(str(value)).quantize(unit, rounding=ROUND_HALF_UP)


def today() -> date:
    return datetime.now(timezone.utc).date()


def as_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _lookup(table: dict, key: object, name: str):
    if key not in table:
        raise ValueError(f"{name} {key!r} is outside the PP RF 840 table {sorted(table)}")
    return table[key]


def processing_coef(level: str) -> Decimal:
    return _lookup(PROCESSING_COEFS, level, "processing_level")


def usage_coef(usage: str) -> Decimal:
    return _lookup(USAGE_COEFS, usage, "usage_type")


def _freshness(order: dict, calculation_date: date) -> tuple[Decimal, int | None]:
    """Т and the image age in calendar days on the calculation date (None for a new acquisition)."""
    kind, guaranteed = order.get("acquisition_type"), order.get("guaranteed_purchase")
    if kind == "new":
        if guaranteed is not True:
            raise ValueError("A new acquisition takes T = 1.8 only with guaranteed purchase")
        return FRESHNESS_NEW, None
    if kind not in ("operational", "archive"):
        raise ValueError(f"acquisition_type {kind!r} is not new, operational or archive")
    if guaranteed is not False:
        raise ValueError("Imagery that already exists is never a guaranteed purchase")
    acquired = as_date(order.get("observation_at"))
    if acquired is None:
        raise ValueError("Existing imagery needs observation_at to count its age")
    age = (calculation_date - acquired).days
    if age < 0:
        raise ValueError(f"Image acquired {-age} days after the calculation date")
    expected = "operational" if age <= OPERATIONAL_DAYS else "archive"
    if kind != expected:
        raise ValueError(f"An image {age} days old on the calculation date is {expected}, not {kind}")
    return (FRESHNESS_OPERATIONAL if age <= OPERATIONAL_DAYS else FRESHNESS_ARCHIVE), age


def freshness_coef(order: dict, calculation_date: object) -> Decimal:
    """Т of PP RF 840: 1.8 for a new acquisition with guaranteed purchase; existing imagery by age in
    calendar days on the calculation date, 1 up to and including 90 days, 0.6 above."""
    on = as_date(calculation_date)
    if on is None:
        raise ValueError("Freshness needs the calculation date")
    return _freshness(order, on)[0]


def discount_coef(sensor_type: str, area_km2: object, resolution_m: object) -> Decimal:
    """Volume discount P for the group area S (km²) at resolution r (m), natural log, 9 digits HALF_UP."""
    slope, intercept, floor = _lookup(DISCOUNT_CURVES, sensor_type, "sensor_type")
    area, resolution = Decimal(str(area_km2)), Decimal(str(resolution_m))
    if area <= 0 or resolution <= 0:
        raise ValueError("S and r of the discount formula must be positive")
    raw = slope * (area / (resolution * resolution)).ln() + intercept
    return min(Decimal(1), max(floor, raw)).quantize(DISCOUNT, rounding=ROUND_HALF_UP)


def discount_formula(sensor_type: str) -> str:
    slope, intercept, floor = _lookup(DISCOUNT_CURVES, sensor_type, "sensor_type")
    return f"{sensor_type}: min(1; max({floor}; {slope}*ln(S/r^2)+{intercept}))"


def group_key(sensor_type: str, resolution_m: object, year: int) -> str:
    """Case discount group without the basket prefix: data type, resolution and calendar year."""
    return f"{sensor_type}_{format(Decimal(str(resolution_m)).normalize(), 'f')}m_{year}"


def discount_group_id(strategy: str, sensor_type: str, resolution_m: object, year: int) -> str:
    """discount_group_id is assigned per basket, so B and C never share one."""
    return f"{strategy}_{group_key(sensor_type, resolution_m, year)}"


def pricing_rules() -> dict:
    """Tables, formulas, grouping and rounding the price function applies, for metadata and the screen."""
    return {
        "legal_edition": LEGAL_EDITION, "formula": FORMULA,
        "base_rate_rub_km2": float(BASE_RATE), "base_rate_status": "scenario", "base_rate_source": BASE_RATE_SOURCE,
        "processing_coef_O": {level: float(value) for level, value in PROCESSING_COEFS.items()},
        "usage_coef_P": {usage: float(value) for usage, value in USAGE_COEFS.items()},
        "freshness_coef_T": {"new_guaranteed_purchase": float(FRESHNESS_NEW),
                             f"operational_up_to_{OPERATIONAL_DAYS}_days": float(FRESHNESS_OPERATIONAL),
                             f"archive_older_than_{OPERATIONAL_DAYS}_days": float(FRESHNESS_ARCHIVE)},
        "freshness_rule": (f"age = calculation_date - observation_at in calendar days; exactly {OPERATIONAL_DAYS} days "
                           "is operational (T = 1); existing imagery always has guaranteed_purchase = false"),
        "discount_formulas": {sensor: discount_formula(sensor) for sensor in DISCOUNT_CURVES},
        "discount_rule": "S in km², r = resolution_m in metres, natural log; non-positive S or r are invalid",
        "discount_group_rule": GROUP_RULE, "previous_group_area_km2": 0,
        "previous_group_area_rule": "no earlier orders in any group of the order year; the price function takes "
                                    "them per group key, e.g. optical_1m_2026",
        "rounding": ROUNDING,
    }


def _shared(lines: list[dict], key: str) -> object:
    """A basket-level value only when every line agrees on it."""
    if not lines:
        return None
    first = lines[0][key]
    return first if all(line[key] == first for line in lines) else None


def quote(zones: list[dict], tariff_multiplier: float = 1.0, strategy: str = "C", calculation_date: object = None,
          previous_group_area_km2: dict | None = None) -> dict:
    """Price one basket under PP RF 840: group the orders, recompute S and P for every group, round each line.

    Every order states its product (sensor_type, resolution_m, processing_level, usage_type, acquisition_type,
    guaranteed_purchase and, for existing imagery, observation_at); nothing defaults to a product.
    previous_group_area_km2 maps a group key such as optical_1m_2026 to the area of earlier orders.
    """
    on = as_date(calculation_date) or today()
    previous = {key: Decimal(str(value)) for key, value in (previous_group_area_km2 or {}).items()}
    rate = BASE_RATE * Decimal(str(tariff_multiplier))
    prepared = []
    for order in zones:
        candidate_id = order.get("candidate_id") or order.get("id")
        missing = [field for field in REQUIRED_ORDER_FIELDS if field not in order]
        if missing:
            raise ValueError(f"Order {candidate_id} lacks {missing}")
        area = rounded(order["area_km2"], AREA)
        if area < 1:
            raise ValueError(f"Order {candidate_id} is below 1 km²")
        _lookup(DISCOUNT_CURVES, order["sensor_type"], "sensor_type")
        if Decimal(str(order["resolution_m"])) <= 0:
            raise ValueError(f"Order {candidate_id}: resolution_m must be positive")
        freshness, age = _freshness(order, on)
        prepared.append({"order": order, "candidate_id": candidate_id, "area": area, "freshness": freshness,
                         "age": age, "processing": processing_coef(order["processing_level"]),
                         "usage": usage_coef(order["usage_type"]),
                         "key": group_key(order["sensor_type"], order["resolution_m"], on.year)})
    groups: dict[str, dict] = {}
    for item in prepared:
        group = groups.setdefault(item["key"], {"order": item["order"], "area": Decimal(0), "ids": []})
        group["area"] += item["area"]
        group["ids"].append(item["candidate_id"])
    for key, group in groups.items():
        group["previous"] = previous.get(key, Decimal(0))
        group["s"] = group["previous"] + group["area"]
        sensor, resolution = group["order"]["sensor_type"], group["order"]["resolution_m"]
        group["discount"] = discount_coef(sensor, group["s"], resolution)
        group["id"] = discount_group_id(strategy, sensor, resolution, on.year)
    lines = []
    for item in prepared:
        order, group = item["order"], groups[item["key"]]
        unit = rounded(rate * item["processing"] * item["usage"] * item["freshness"] * group["discount"], MONEY)
        lines.append({
            "candidate_id": item["candidate_id"], "area_km2": float(item["area"]),
            "sensor_type": order["sensor_type"], "resolution_m": order["resolution_m"],
            "processing_level": order["processing_level"], "usage_type": order["usage_type"],
            "acquisition_type": order["acquisition_type"], "guaranteed_purchase": order["guaranteed_purchase"],
            "observation_at": order.get("observation_at"), "age_days": item["age"],
            "base_rate_rub_km2": float(rate), "base_rate_status": "scenario",
            "processing_coef": float(item["processing"]), "usage_coef": float(item["usage"]),
            "freshness_coef": float(item["freshness"]), "discount_coef": float(group["discount"]),
            "discount_group_id": group["id"], "previous_group_area_km2": float(group["previous"]),
            "basket_group_area_km2": float(group["area"]), "group_area_km2": float(group["s"]),
            "discount_formula": discount_formula(order["sensor_type"]),
            "unit_price_rub_km2": float(unit), "cost_rub": float(rounded(unit * item["area"], MONEY)),
            "formula": FORMULA, "legal_edition": LEGAL_EDITION,
        })
    total = sum((Decimal(str(line["cost_rub"])) for line in lines), Decimal(0))
    result = {
        "total_rub": float(total), "strategy": strategy, "calculation_date": on.isoformat(), "order_year": on.year,
        "base_rate_rub_km2": float(rate), "base_rate_status": "scenario", "formula": FORMULA,
        "legal_edition": LEGAL_EDITION, "discount_group_rule": GROUP_RULE,
        "groups": [{"discount_group_id": group["id"], "group_key": key,
                    "sensor_type": group["order"]["sensor_type"], "resolution_m": group["order"]["resolution_m"],
                    "order_year": on.year, "previous_group_area_km2": float(group["previous"]),
                    "basket_group_area_km2": float(group["area"]), "group_area_km2": float(group["s"]),
                    "discount_coef": float(group["discount"]),
                    "discount_formula": discount_formula(group["order"]["sensor_type"]),
                    "candidate_ids": group["ids"]} for key, group in groups.items()],
        "lines": lines,
    }
    # One formula line on the screen: basket-level numbers only where every line shares them.
    for key in ("discount_group_id", "group_area_km2", "previous_group_area_km2", "discount_coef",
                "unit_price_rub_km2", "processing_coef", "usage_coef", "freshness_coef", "sensor_type",
                "resolution_m", "processing_level", "acquisition_type"):
        result[key] = _shared(lines, key)
    return result


MAX_REGION_AREA_KM2 = 300


def rectangle_area_km2(bounds: list[float]) -> float:
    west, south, east, north = bounds
    x0, y0 = TO_EQUAL_AREA.transform(west, south)
    x1, y1 = TO_EQUAL_AREA.transform(east, north)
    return abs((x1 - x0) * (y1 - y0)) / 1_000_000


def intersects(a: list[float], b: list[float]) -> bool:
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def contains(bounds: list[float], asset: dict) -> bool:
    if asset.get("footprint_bounds"):
        west, south, east, north = asset["footprint_bounds"]
        return bounds[0] <= west and bounds[1] <= south and bounds[2] >= east and bounds[3] >= north
    return bounds[0] <= asset["longitude"] <= bounds[2] and bounds[1] <= asset["latitude"] <= bounds[3]


def as_polygon(bounds: list[float]) -> dict:
    w, s, e, n = bounds
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def square_around(asset: dict, chip_bounds: list[float]) -> list[float]:
    # A 1.12 km side leaves a margin above the case's 1 km² minimum.
    half_lat = 0.56 / 111.32
    half_lon = 0.56 / (111.32 * cos(asset["latitude"] * pi / 180))
    lon = min(max(asset["longitude"], chip_bounds[0] + half_lon), chip_bounds[2] - half_lon)
    lat = min(max(asset["latitude"], chip_bounds[1] + half_lat), chip_bounds[3] - half_lat)
    bounds = [lon - half_lon, lat - half_lat, lon + half_lon, lat + half_lat]
    if rectangle_area_km2(bounds) < 1:
        raise ValueError("Candidate zone is below 1 km²")
    return bounds


def products(zone: dict, event_date: date) -> list[dict]:
    """Three catalog orders over one footprint, in the order the product rule prefers them."""
    common = {"footprint_id": zone["id"], "area_km2": zone["area_km2"], "usage_type": "internal",
              "base_rate_rub_km2": float(BASE_RATE), "base_rate_status": "scenario",
              "base_rate_source": BASE_RATE_SOURCE, "legal_edition": LEGAL_EDITION}
    new = {"acquisition_type": "new", "data_role": "event_observation", "guaranteed_purchase": True,
           "observation_at": None, "available_at": None, "observation_status": "scenario_future_acquisition"}
    archived = event_date - timedelta(days=ARCHIVE_LEAD_DAYS)
    return [
        {"candidate_id": zone["id"], "product": "optical_1m_new", **common, **new, "sensor_type": "optical",
         "resolution_m": 1, "processing_level": "L2", "delivery_hours": OPTICAL_DELIVERY_HOURS,
         "all_weather": False, "availability_status": f"scenario_{OPTICAL_DELIVERY_HOURS}_hours_after_order",
         "suitability_status": "scenario_usable", "cloud_check": "not_checked"},
        {"candidate_id": f"{zone['id']}-SAR", "product": "sar_3m_new", **common, **new, "sensor_type": "sar",
         "resolution_m": 3, "processing_level": "L1", "delivery_hours": SAR_DELIVERY_HOURS,
         "all_weather": True, "availability_status": f"scenario_{SAR_DELIVERY_HOURS}_hours_after_order",
         "suitability_status": "all_weather", "cloud_check": "not_needed"},
        {"candidate_id": f"{zone['id']}-ARC", "product": "optical_1m_archive", **common, "sensor_type": "optical",
         "resolution_m": 1, "processing_level": "L2", "acquisition_type": "archive", "data_role": "context",
         "guaranteed_purchase": False, "delivery_hours": 0, "all_weather": False,
         "observation_at": archived.isoformat(), "available_at": event_date.isoformat(),
         "observation_status": f"scenario_{ARCHIVE_LEAD_DAYS}_days_before_event",
         "availability_status": "archive_available_now",
         "suitability_status": "context_only", "cloud_check": "clear_frame_assumed"},
    ]


def chosen_product(zone: dict, deadline: int) -> dict:
    """The product rule: optics if it arrives by the deadline, else radar if it does, else optics shown late."""
    observations = [product for product in zone["products"] if product["data_role"] == "event_observation"]
    return next((product for product in observations if product["delivery_hours"] <= deadline), observations[0])


def _zone(zone_id: str, name: str, kind: str, bounds: list[float], **extra) -> dict:
    return {"id": zone_id, "name": name, "kind": kind, "bounds": bounds, "geometry": as_polygon(bounds),
            "area_km2": float(rounded(rectangle_area_km2(bounds), AREA)), **extra}


def candidates(assets: list[dict], chip_bounds: list[float], broad_bounds: list[float] | None = None,
               uncertainty: list[dict] | None = None, event_date: object = None) -> list[dict]:
    """Object squares, doubt rectangles from the map and one broad zone, each with its three products."""
    zones = [_zone(f"Z{asset['id'][1:]}", f"Зона · {asset['name'].lower()}", "object",
                   square_around(asset, chip_bounds), asset_id=asset["id"]) for asset in assets]
    for number, item in enumerate(uncertainty or [], 1):
        zones.append(_zone(f"U{number}", f"Сомнительный участок {number}", "uncertainty", item["bounds"],
                           doubt_share=round(item["doubt_share"], 4),
                           density_vs_mean=round(item["density_vs_mean"], 2)))
    broad_bounds = broad_bounds or chip_bounds
    zones.append(_zone("ZALL", "Широкая съёмка · область анализа", "broad", broad_bounds))
    event = as_date(event_date) or today()
    for zone in zones:
        zone["products"] = products(zone, event)
    return zones


def map_error_probability(p: float | None, threshold: float) -> float | None:
    """Chance that the map shows the asset pixel wrongly, from calibrated p."""
    if p is None:
        return None
    return round(1 - p if p >= threshold else p, 5)


def enrich_assets(assets: list[dict], threshold: float = 0.41) -> list[dict]:
    """Two loss readings of every asset.

    case_expected_loss_rub is the mandatory case model EL = p·V·q for every asset with p,
    wherever the point stands; case_loss_status is ok or no_data. It goes to asset_loss.csv.
    expected_loss_rub is the mandatory case EL and drives the basket search.
    flood_expected_loss_rub is an explicitly separate interpretation that becomes
    zero at a point classified as permanent water by JRC.
    """
    result = []
    for asset in assets:
        p = asset["p_flood"]
        permanent = bool(asset.get("permanent_water_at_point"))
        case_loss = None if p is None else round(p * asset["value_rub"] * asset["vulnerability"], 2)
        flood_loss = None if p is None else (0.0 if permanent else case_loss)
        status = "no_data" if p is None else "ok"
        result.append({
            **asset,
            "expected_loss_rub": case_loss, "loss_status": status, "flood_expected_loss_rub": flood_loss,
            "flood_loss_status": "no_data" if p is None else "permanent_water" if permanent else "ok",
            "case_expected_loss_rub": case_loss, "case_loss_status": "no_data" if p is None else "ok",
            "exposure_rub": round(asset["value_rub"] * asset["vulnerability"], 2),
            "map_error_probability": map_error_probability(p, threshold),
            # Rank follows EL; a doubtful point needs a look whatever its rank.
            "map_doubtful": None if p is None else map_error_probability(p, threshold) >= DOUBT_THRESHOLD,
            # Where test_core showed calibrated p above or below the observed water share; EL keeps p.
            "p_check": p_check(p),
            "probability_status": "frozen_ensemble",
        })
    return sorted(result, key=lambda asset: (-(asset["case_expected_loss_rub"] or 0), asset["id"]))


def _half_up(values: np.ndarray, digits: int) -> np.ndarray:
    """ROUND_HALF_UP for positive floats, as quote() does with Decimal; np.round would round half to even."""
    scale = 10 ** digits
    return np.floor(values * scale + 0.5) / scale


def covered_assets(zones: list[dict], assets: list[dict]) -> list[dict]:
    return [asset for asset in assets if any(contains(zone["bounds"], asset) for zone in zones)]


def _product_fields(product: dict) -> dict:
    return {key: product[key] for key in ("candidate_id", "product", "sensor_type", "resolution_m",
                                          "acquisition_type", "data_role", "processing_level", "delivery_hours")}


def _line(quoted: dict, candidate_id: str) -> dict:
    return next(line for line in quoted["lines"] if line["candidate_id"] == candidate_id)


class BasketSpace:
    """Every basket of footprints, scored once per region.

    Coverage of assets and of map doubt does not depend on budget, tariff, goal or product,
    so those are table lookups; the deadline picks one product per footprint (the product
    rule), which sets the price. Float prices only shortlist; the chosen basket is priced
    exactly with Decimal.
    """

    def __init__(self, zones: list[dict], assets: list[dict],
                 doubt_masks: np.ndarray | None = None, doubt_mass: np.ndarray | None = None,
                 doubt_total: float = 0.0, *, compact: bool = False):
        if len(zones) > MAX_EXACT_ZONES:
            raise ValueError(f"Exact search supports up to {MAX_EXACT_ZONES} zones")
        self.zones = zones
        self.assets = assets
        n = len(zones)
        self.subsets = np.arange(1 << n, dtype=np.int64)
        self.bits = ((self.subsets[:, None] >> np.arange(n, dtype=np.int64)) & 1).astype(bool)
        self.count = self.bits.sum(1)
        self.areas = np.array([zone["area_km2"] for zone in zones], dtype=float)
        self.area_sum = self.bits @ self.areas
        self.asset_masks = np.array([sum(1 << k for k, zone in enumerate(zones) if contains(zone["bounds"], asset))
                                     for asset in assets], dtype=np.int64)
        self.asset_covered = None if compact else (self.subsets[:, None] & self.asset_masks[None, :]) != 0
        self.losses = np.array([asset["expected_loss_rub"] or 0 for asset in assets], dtype=float)
        self.doubt_total = doubt_total
        if doubt_masks is not None and len(doubt_masks):
            self.doubt_masks, self.doubt_mass = doubt_masks, doubt_mass
            self.doubt = ((self.subsets[:, None] & doubt_masks[None, :]) != 0).astype(float) @ doubt_mass
            self.zone_doubt = np.array([float(doubt_mass[(doubt_masks >> k) & 1 == 1].sum()) for k in range(n)])
        else:
            self.doubt_masks, self.doubt_mass = np.zeros(0, np.int64), np.zeros(0)
            self.doubt = np.zeros(len(self.subsets))
            self.zone_doubt = np.zeros(n)
        self._prices: dict[tuple, np.ndarray] = {}

    def index(self, zone_ids: list[str]) -> int:
        ids = [zone["id"] for zone in self.zones]
        return sum(1 << ids.index(zone_id) for zone_id in zone_ids)

    def chosen(self, deadline: int) -> list[dict]:
        """The product the rule takes for every footprint at this deadline."""
        return [chosen_product(zone, deadline) for zone in self.zones]

    def late(self, deadline: int) -> int:
        return sum(1 << k for k, product in enumerate(self.chosen(deadline)) if product["delivery_hours"] > deadline)

    def orders(self, index: int | None, deadline: int) -> list[dict]:
        if index is None:
            return []
        return [product for k, product in enumerate(self.chosen(deadline)) if index >> k & 1]

    def prices(self, tariff: float, deadline: int) -> np.ndarray:
        chosen = self.chosen(deadline)
        key = (tariff, tuple(product["candidate_id"] for product in chosen))
        if key not in self._prices:
            on = today()
            total = np.zeros(len(self.subsets))
            members: dict[str, list[int]] = {}
            for k, product in enumerate(chosen):
                members.setdefault(group_key(product["sensor_type"], product["resolution_m"], on.year), []).append(k)
            for group in members.values():
                first = chosen[group[0]]
                slope, intercept, floor = DISCOUNT_CURVES[first["sensor_type"]]
                area = np.maximum(self.bits[:, group] @ self.areas[group], 1e-12)
                raw = float(slope) * np.log(area / float(first["resolution_m"]) ** 2) + float(intercept)
                discount = _half_up(np.clip(raw, float(floor), 1.0), 9)
                for k in group:
                    product = chosen[k]
                    coefficient = (processing_coef(product["processing_level"]) * usage_coef(product["usage_type"])
                                   * _freshness(product, on)[0])
                    unit = _half_up(float(BASE_RATE * coefficient) * tariff * discount, 2)
                    total += np.where(self.bits[:, k], _half_up(unit * self.areas[k], 2), 0.0)
            self._prices[key] = total
        return self._prices[key]

    def covered_assets(self, index: int) -> np.ndarray:
        return (index & self.asset_masks) != 0

    def losses_for(self, losses: np.ndarray | None) -> np.ndarray:
        weights = self.losses if losses is None else losses
        if self.asset_covered is not None:
            return self.asset_covered @ weights
        # Sum weights by coverage pattern, then a subset zeta transform. Memory is
        # O(buildings + 2**zones), never O(buildings * 2**zones).
        sums = np.bincount(self.asset_masks, weights=weights, minlength=len(self.subsets))
        for bit in range(len(self.zones)):
            step = 1 << bit
            blocks = sums.reshape(-1, step * 2)
            blocks[:, step:] += blocks[:, :step]
        return np.maximum(0, sums[-1] - sums[(len(sums) - 1) ^ self.subsets])

    def feasible(self, budget: float, deadline: int, must_cover: str | None, tariff: float) -> np.ndarray:
        ok = (self.prices(tariff, deadline) <= budget + 0.005) & ((self.subsets & self.late(deadline)) == 0)
        if must_cover:
            ids = [asset["id"] for asset in self.assets]
            ok &= (self.subsets & self.asset_masks[ids.index(must_cover)]) != 0 if must_cover in ids else False
        return ok

    def best(self, budget: float, priority: str, deadline: int, must_cover: str | None,
             tariff: float = 1.0, losses: np.ndarray | None = None) -> int | None:
        """Basket index: most of the goal, then most of the other measure, then cheapest and smallest."""
        ok = self.feasible(budget, deadline, must_cover, tariff)
        candidates = np.nonzero(ok)[0]
        if not len(candidates):
            return None
        covered_loss = np.round(self.losses_for(losses)[candidates], 2)
        doubt = np.round(self.doubt[candidates], 6)
        primary, secondary = (covered_loss, doubt) if priority == "risk" else (doubt, covered_loss)
        order = np.lexsort((self.count[candidates], self.prices(tariff, deadline)[candidates], -secondary, -primary))
        # The float price only shortlists; walk the whole order so a basket that fails the exact
        # Decimal check near the budget never hides a feasible one further down.
        for position in order:
            index = int(candidates[position])
            if quote(self.orders(index, deadline), tariff)["total_rub"] <= budget:
                return index
        return None

    def selected(self, index: int) -> list[dict]:
        return [zone for k, zone in enumerate(self.zones) if index >> k & 1]

    def summary(self, index: int | None, deadline: int, tariff: float = 1.0, losses: np.ndarray | None = None,
                strategy: str = "C", on: date | None = None) -> dict:
        if index is None:
            return {"zone_ids": [], "candidate_ids": [], "quote": quote([], tariff, strategy, on),
                    "covered_asset_ids": [], "covered_expected_loss_rub": 0, "covered_doubt_share": 0,
                    "feasible": False, "reason": "Обязательный объект не успевает или не помещается в бюджет"}
        orders = self.orders(index, deadline)
        covered = [asset["id"] for asset, flag in zip(self.assets, self.covered_assets(index)) if flag]
        return {"zone_ids": [zone["id"] for zone in self.selected(index)],
                "candidate_ids": [order["candidate_id"] for order in orders],
                "quote": quote(orders, tariff, strategy, on), "covered_asset_ids": covered,
                "covered_expected_loss_rub": round(float(self.losses_for(losses)[index]), 2),
                "covered_doubt_share": round(float(self.doubt[index]) / self.doubt_total, 4) if self.doubt_total else None,
                "feasible": True, "reason": None}

    def plan(self, budget: float, priority: str, deadline: int, must_cover: str | None,
             tariff: float = 1.0, losses: np.ndarray | None = None) -> dict:
        return self.summary(self.best(budget, priority, deadline, must_cover, tariff, losses), deadline, tariff, losses)

    def frontier(self, deadline: int, must_cover: str | None, tariff: float = 1.0) -> list[dict]:
        """Cheapest basket for each reachable share of doubtful area: the price of certainty."""
        if not self.doubt_total:
            return []
        ok = np.nonzero(self.feasible(float("inf"), deadline, must_cover, tariff))[0]
        prices = self.prices(tariff, deadline)[ok]
        order = np.argsort(prices, kind="stable")
        best = np.maximum.accumulate(self.doubt[ok][order])
        steps = np.nonzero(np.diff(np.concatenate(([-1.0], best))) > 1e-9)[0]
        if len(steps) > 48:
            steps = steps[np.unique(np.linspace(0, len(steps) - 1, 48).round().astype(int))]
        return [{"cost_rub": round(float(prices[order][i]), 2),
                 "doubt_share": round(float(best[i]) / self.doubt_total, 4)} for i in steps]

    def decisions(self, index: int | None, budget: float, priority: str, deadline: int,
                  must_cover: str | None, tariff: float = 1.0, on: date | None = None,
                  losses: np.ndarray | None = None) -> list[dict]:
        """Why every footprint is in basket C or not, with the product the rule takes, in checkable numbers."""
        chosen = index or 0
        covered_losses = self.losses_for(losses)
        asset_losses = self.losses if losses is None else losses
        basket = quote(self.orders(chosen, deadline), tariff, "C", on)
        rows = []
        for k, (zone, product) in enumerate(zip(self.zones, self.chosen(deadline))):
            bit = 1 << k
            timely = product["delivery_hours"] <= deadline
            own_assets = [asset["id"] for asset, mask in zip(self.assets, self.asset_masks) if mask & bit]
            row = {"zone_id": zone["id"], "kind": zone["kind"], "area_km2": zone["area_km2"],
                   "price_alone_rub": quote([product], tariff, "C", on)["total_rub"], "asset_ids": own_assets,
                   "expected_loss_rub": round(float(sum(asset_losses[i] for i, mask in enumerate(self.asset_masks) if mask & bit)), 2),
                   "doubt_share": round(float(self.zone_doubt[k]) / self.doubt_total, 4) if self.doubt_total else None,
                   **_product_fields(product), "timely": timely}
            if index is not None and chosen & bit:
                line = _line(basket, product["candidate_id"])
                row.update(selected=True, reason="selected", unit_price_rub_km2=line["unit_price_rub_km2"],
                           cost_rub=line["cost_rub"])
                rows.append(row)
                continue
            grown = quote(self.orders(chosen | bit, deadline), tariff, "C", on)
            line = _line(grown, product["candidate_id"])
            row.update(selected=False, unit_price_rub_km2=line["unit_price_rub_km2"], cost_rub=line["cost_rub"])
            if not timely:
                row["reason"] = "late"
            else:
                gain_loss = round(float(covered_losses[chosen | bit] - covered_losses[chosen]), 2)
                gain_doubt = float(self.doubt[chosen | bit] - self.doubt[chosen])
                gain = gain_loss if priority == "risk" else gain_doubt
                row.update(price_with_rub=grown["total_rub"], gain_loss_rub=gain_loss,
                           gain_doubt_share=round(gain_doubt / self.doubt_total, 4) if self.doubt_total else None)
                # An order that adds nothing to the goal is out for that reason, whatever it costs.
                if index is None:
                    row["reason"] = "plan_infeasible"
                elif gain <= 1e-9:
                    row["reason"] = "nothing_new"
                elif grown["total_rub"] > budget:
                    row["reason"] = "over_budget"
                else:
                    row["reason"] = "cheaper_equal"
            rows.append(row)
        return rows

    def catalog(self, c_index: int | None, b_index: int | None, deadline: int, decisions: list[dict],
                tariff: float = 1.0, on: date | None = None) -> list[dict]:
        """Every product of every footprint: price alone and in a basket, timeliness, role and why it is used or not.

        The basket price is the product's line in C or B when it is there; otherwise its line in C
        grown by it (basket_hypothetical), since adding it moves the discount of its whole group.
        """
        c_orders, b_orders = self.orders(c_index, deadline), self.orders(b_index, deadline)
        in_c = {order["candidate_id"] for order in c_orders}
        in_b = {order["candidate_id"] for order in b_orders}
        baskets = {"C": quote(c_orders, tariff, "C", on), "B": quote(b_orders, tariff, "B", on)}
        by_zone = {row["zone_id"]: row for row in decisions}
        rows = []
        for k, zone in enumerate(self.zones):
            bit = 1 << k
            pick = chosen_product(zone, deadline)
            footprint = {"footprint_id": zone["id"], "kind": zone["kind"], "name": zone["name"],
                         "asset_ids": [asset["id"] for asset, mask in zip(self.assets, self.asset_masks) if mask & bit],
                         "doubt_share": by_zone[zone["id"]]["doubt_share"],
                         **({"asset_id": zone["asset_id"]} if "asset_id" in zone else {})}
            for product in zone["products"]:
                candidate_id = product["candidate_id"]
                alone = quote([product], tariff, "C", on)
                basket = "C" if candidate_id in in_c or candidate_id not in in_b else "B"
                hypothetical = candidate_id not in in_c and candidate_id not in in_b
                quoted = quote(c_orders + [product], tariff, "C", on) if hypothetical else baskets[basket]
                line = _line(quoted, candidate_id)
                timely = product["delivery_hours"] <= deadline
                observation = product["data_role"] == "event_observation"
                if candidate_id in in_c:
                    reason = "selected"
                elif not observation:
                    reason = "context_only"
                elif not timely:
                    reason = "late"
                elif product is not pick:
                    reason = "product_rule"
                else:
                    reason = by_zone[zone["id"]]["reason"]
                alone_line = alone["lines"][0]
                rows.append({
                    **product, **footprint, "timely": timely, "counts_for_coverage": observation and timely,
                    "chosen": product is pick, "in_b": candidate_id in in_b, "in_c": candidate_id in in_c,
                    "price_alone_rub": alone["total_rub"], "unit_price_alone_rub_km2": alone_line["unit_price_rub_km2"],
                    "processing_coef": alone_line["processing_coef"], "usage_coef": alone_line["usage_coef"],
                    "freshness_coef": alone_line["freshness_coef"], "discount_coef_alone": alone_line["discount_coef"],
                    "basket": basket, "basket_hypothetical": hypothetical, "basket_total_rub": quoted["total_rub"],
                    "basket_unit_price_rub_km2": line["unit_price_rub_km2"], "basket_cost_rub": line["cost_rub"],
                    "basket_discount_coef": line["discount_coef"], "basket_discount_group_id": line["discount_group_id"],
                    "reason": reason,
                })
        return rows


def plan(assets: list[dict], zones: list[dict], budget_rub: float, priority: str,
         deadline_hours: int, must_cover: str | None, tariff_multiplier: float = 1.0) -> dict:
    """Plan without map doubt, for callers that only know assets and zones."""
    return BasketSpace(zones, assets).plan(budget_rub, priority, deadline_hours, must_cover, tariff_multiplier)
