"""Procurement math for frozen model outputs and a disclosed scenario tariff."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from math import cos, log, pi

import numpy as np
from pyproj import Transformer

from .zoning import DOUBT_THRESHOLD


BASE_RATE = Decimal("788.64")  # Scenario from the case materials, not an official quote.
# PP RF 840 coefficients of the scenario product: L2 processing, internal use, new acquisition.
PROCESSING_COEF = Decimal("1.2")
USAGE_COEF = Decimal("1")
FRESHNESS_COEF = Decimal("1.8")
RESOLUTION_M = 1
LEGAL_EDITION = "PP_RF_840_2025-08-27"
MONEY = Decimal("0.01")
AREA = Decimal("0.001")
DISCOUNT = Decimal("0.000000001")
TO_EQUAL_AREA = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True)
MAX_EXACT_ZONES = 16
PRIORITIES = {"risk", "information"}


def rounded(value: float | Decimal, unit: Decimal) -> Decimal:
    return Decimal(str(value)).quantize(unit, rounding=ROUND_HALF_UP)


def rectangle_area_km2(bounds: list[float]) -> float:
    west, south, east, north = bounds
    x0, y0 = TO_EQUAL_AREA.transform(west, south)
    x1, y1 = TO_EQUAL_AREA.transform(east, north)
    return abs((x1 - x0) * (y1 - y0)) / 1_000_000


def intersects(a: list[float], b: list[float]) -> bool:
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def contains(bounds: list[float], asset: dict) -> bool:
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


def discount_group_id(year: int | None = None) -> str:
    """The volume discount counts area ordered in one calendar year for one data type and resolution."""
    return f"optical_{RESOLUTION_M}m_{year or datetime.now(timezone.utc).year}"


# Optical 1 m sees water only through a clear sky, and a flood usually comes with cloud.
# The service cannot see the cloud forecast for the tasking window, so every zone is
# assumed usable by the deadline; this is a scenario, like the 6 h delivery.
SUITABILITY_RULE = ("optical 1 m needs a cloud-free pass; cloud cover over the zone at the tasking time is not "
                    "checked, so every zone is assumed usable by the deadline (suitability_status=scenario_usable) "
                    "and covered EL is an upper bound; before a real order check the cloud forecast, and use radar "
                    "(P_sar discount of PP 840) where the sky is closed")


def _zone(zone_id: str, name: str, kind: str, bounds: list[float], **extra) -> dict:
    return {
        "id": zone_id, "name": name, "kind": kind, "bounds": bounds, "geometry": as_polygon(bounds),
        "area_km2": float(rounded(rectangle_area_km2(bounds), AREA)),
        "resolution_m": RESOLUTION_M, "sensor_type": "optical", "delivery_hours": 6,
        "data_role": "event_observation", "acquisition_type": "new",
        "processing_level": "L2", "usage_type": "internal", "guaranteed_purchase": True,
        "base_rate_status": "scenario", "base_rate_source": "case_materials_control_example",
        "base_rate_rub_km2": float(BASE_RATE), "observation_at": None,
        "available_at": None, "legal_edition": LEGAL_EDITION,
        "suitability_status": "scenario_usable", "cloud_check": "not_checked", **extra,
    }


def candidates(assets: list[dict], chip_bounds: list[float], broad_bounds: list[float] | None = None,
               uncertainty: list[dict] | None = None) -> list[dict]:
    """Object squares, doubt rectangles from the map and one broad zone over the analysed area."""
    zones = [_zone(f"Z{asset['id'][1:]}", f"Зона · {asset['name'].lower()}", "object",
                   square_around(asset, chip_bounds), asset_id=asset["id"]) for asset in assets]
    for number, item in enumerate(uncertainty or [], 1):
        zones.append(_zone(f"U{number}", f"Сомнительный участок {number}", "uncertainty", item["bounds"],
                           doubt_share=round(item["doubt_share"], 4),
                           density_vs_mean=round(item["density_vs_mean"], 2)))
    broad_bounds = broad_bounds or chip_bounds
    zones.append(_zone("ZALL", "Широкая съёмка · область анализа", "broad", broad_bounds))
    return zones


def quote(zones: list[dict], tariff_multiplier: float = 1.0) -> dict:
    """Recalculate volume discount for the entire basket, then round each line."""
    group = discount_group_id()
    rule = "optical, 1 m, orders of one calendar year; no earlier orders in the group"
    if not zones:
        return {"total_rub": 0.0, "group_area_km2": 0, "discount_coef": 1.0, "lines": [],
                "discount_group_id": group, "discount_group_rule": rule, "previous_group_area_km2": 0}
    group_area = sum(Decimal(str(z["area_km2"])) for z in zones)
    raw_discount = -0.058 * log(float(group_area) / RESOLUTION_M ** 2) + 1.037
    discount = rounded(min(1.0, max(0.2, raw_discount)), DISCOUNT)
    scenario_rate = BASE_RATE * Decimal(str(tariff_multiplier))
    unit_price = rounded(scenario_rate * PROCESSING_COEF * USAGE_COEF * FRESHNESS_COEF * discount, MONEY)
    lines = [{
        "candidate_id": zone["id"], "area_km2": zone["area_km2"],
        "unit_price_rub_km2": float(unit_price),
        "cost_rub": float(rounded(unit_price * Decimal(str(zone["area_km2"])), MONEY)),
        "discount_coef": float(discount), "group_area_km2": float(group_area),
        "discount_group_id": group,
        "base_rate_rub_km2": float(scenario_rate), "base_rate_status": "scenario",
        "processing_coef": float(PROCESSING_COEF), "usage_coef": float(USAGE_COEF),
        "freshness_coef": float(FRESHNESS_COEF),
        "formula": "B*K*O*P*T*R", "legal_edition": LEGAL_EDITION,
    } for zone in zones]
    total = sum(Decimal(str(line["cost_rub"])) for line in lines)
    return {"total_rub": float(total), "group_area_km2": float(group_area),
            "discount_coef": float(discount), "unit_price_rub_km2": float(unit_price),
            "base_rate_rub_km2": float(scenario_rate), "processing_coef": float(PROCESSING_COEF),
            "usage_coef": float(USAGE_COEF), "freshness_coef": float(FRESHNESS_COEF),
            "discount_group_id": group, "discount_group_rule": rule, "previous_group_area_km2": 0,
            "lines": lines}


def map_error_probability(p: float | None, threshold: float) -> float | None:
    """Chance that the map shows the asset pixel wrongly, from calibrated p."""
    if p is None:
        return None
    return round(1 - p if p >= threshold else p, 5)


def enrich_assets(assets: list[dict], threshold: float = 0.41) -> list[dict]:
    """EL = p·V·q for every estimated point, as the case requires.

    A point on JRC permanent water keeps that EL; flood_expected_loss_rub = 0 beside it
    records that the water was there before the flood (an additional view, not the model).
    """
    result = []
    for asset in assets:
        p = asset["p_flood"]
        permanent = bool(asset.get("permanent_water_at_point"))
        if p is None:
            loss, flood_loss, status = None, None, "no_data"
        else:
            loss = round(p * asset["value_rub"] * asset["vulnerability"], 2)
            flood_loss, status = (0.0 if permanent else loss), "ok"
        result.append({
            **asset,
            "expected_loss_rub": loss, "flood_expected_loss_rub": flood_loss, "loss_status": status,
            "exposure_rub": round(asset["value_rub"] * asset["vulnerability"], 2),
            "map_error_probability": map_error_probability(p, threshold),
            # Rank follows EL; a doubtful point needs a look whatever its rank.
            "map_doubtful": None if p is None else map_error_probability(p, threshold) >= DOUBT_THRESHOLD,
            "probability_status": "frozen_ensemble",
        })
    return sorted(result, key=lambda asset: (-(asset["expected_loss_rub"] or 0), asset["id"]))


def _half_up(values: np.ndarray, digits: int) -> np.ndarray:
    """ROUND_HALF_UP for positive floats, as quote() does with Decimal; np.round would round half to even."""
    scale = 10 ** digits
    return np.floor(values * scale + 0.5) / scale


def covered_assets(zones: list[dict], assets: list[dict]) -> list[dict]:
    return [asset for asset in assets if any(contains(zone["bounds"], asset) for zone in zones)]


class BasketSpace:
    """Every basket of the candidate catalog, scored once per region.

    Coverage of assets and of map doubt does not depend on budget, tariff or
    goal, so those are table lookups; only the price and the choice change.
    Float prices only shortlist; the chosen basket is priced exactly with Decimal.
    """

    def __init__(self, zones: list[dict], assets: list[dict],
                 doubt_masks: np.ndarray | None = None, doubt_mass: np.ndarray | None = None,
                 doubt_total: float = 0.0):
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
        self.asset_covered = (self.subsets[:, None] & self.asset_masks[None, :]) != 0
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
        self._prices: dict[float, np.ndarray] = {}

    def index(self, zone_ids: list[str]) -> int:
        ids = [zone["id"] for zone in self.zones]
        return sum(1 << ids.index(zone_id) for zone_id in zone_ids)

    def prices(self, tariff: float) -> np.ndarray:
        if tariff not in self._prices:
            area = np.maximum(self.area_sum, 1e-12)
            discount = _half_up(np.clip(-0.058 * np.log(area / RESOLUTION_M ** 2) + 1.037, 0.2, 1.0), 9)
            rate = float(BASE_RATE) * tariff * float(PROCESSING_COEF * USAGE_COEF * FRESHNESS_COEF)
            unit = _half_up(rate * discount, 2)
            lines = _half_up(unit[:, None] * self.areas[None, :], 2)
            self._prices[tariff] = np.where(self.count > 0, (lines * self.bits).sum(1), 0.0)
        return self._prices[tariff]

    def losses_for(self, losses: np.ndarray | None) -> np.ndarray:
        return self.asset_covered @ (self.losses if losses is None else losses)

    def feasible(self, budget: float, deadline: int, must_cover: str | None, tariff: float) -> np.ndarray:
        late = sum(1 << k for k, zone in enumerate(self.zones) if zone["delivery_hours"] > deadline)
        ok = (self.prices(tariff) <= budget + 0.005) & ((self.subsets & late) == 0)
        if must_cover:
            ids = [asset["id"] for asset in self.assets]
            ok &= self.asset_covered[:, ids.index(must_cover)] if must_cover in ids else False
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
        order = np.lexsort((self.count[candidates], self.prices(tariff)[candidates], -secondary, -primary))
        # The float price only shortlists; walk the whole order so a basket that fails the exact
        # Decimal check near the budget never hides a feasible one further down.
        for position in order:
            index = int(candidates[position])
            if quote(self.selected(index), tariff)["total_rub"] <= budget:
                return index
        return None

    def selected(self, index: int) -> list[dict]:
        return [zone for k, zone in enumerate(self.zones) if index >> k & 1]

    def summary(self, index: int | None, tariff: float = 1.0, losses: np.ndarray | None = None) -> dict:
        if index is None:
            return {"zone_ids": [], "quote": quote([], tariff), "covered_asset_ids": [],
                    "covered_expected_loss_rub": 0, "covered_doubt_share": 0, "feasible": False,
                    "reason": "Обязательный объект не успевает или не помещается в бюджет"}
        selected = self.selected(index)
        covered = [asset["id"] for asset, flag in zip(self.assets, self.asset_covered[index]) if flag]
        return {"zone_ids": [zone["id"] for zone in selected], "quote": quote(selected, tariff),
                "covered_asset_ids": covered,
                "covered_expected_loss_rub": round(float(self.losses_for(losses)[index]), 2),
                "covered_doubt_share": round(float(self.doubt[index]) / self.doubt_total, 4) if self.doubt_total else None,
                "feasible": True, "reason": None}

    def plan(self, budget: float, priority: str, deadline: int, must_cover: str | None,
             tariff: float = 1.0, losses: np.ndarray | None = None) -> dict:
        return self.summary(self.best(budget, priority, deadline, must_cover, tariff, losses), tariff, losses)

    def frontier(self, deadline: int, must_cover: str | None, tariff: float = 1.0) -> list[dict]:
        """Cheapest basket for each reachable share of doubtful area: the price of certainty."""
        if not self.doubt_total:
            return []
        ok = np.nonzero(self.feasible(float("inf"), deadline, must_cover, tariff))[0]
        prices = self.prices(tariff)[ok]
        order = np.argsort(prices, kind="stable")
        best = np.maximum.accumulate(self.doubt[ok][order])
        steps = np.nonzero(np.diff(np.concatenate(([-1.0], best))) > 1e-9)[0]
        if len(steps) > 48:
            steps = steps[np.unique(np.linspace(0, len(steps) - 1, 48).round().astype(int))]
        return [{"cost_rub": round(float(prices[order][i]), 2),
                 "doubt_share": round(float(best[i]) / self.doubt_total, 4)} for i in steps]

    def decisions(self, index: int | None, budget: float, priority: str, deadline: int,
                  must_cover: str | None, tariff: float = 1.0) -> list[dict]:
        """Why every candidate zone is in the basket or not, in checkable numbers."""
        chosen = index or 0
        prices = self.prices(tariff)
        losses = self.losses_for(None)
        rows = []
        for k, zone in enumerate(self.zones):
            bit = 1 << k
            alone = quote([zone], tariff)
            own_assets = [asset["id"] for asset, mask in zip(self.assets, self.asset_masks) if mask & bit]
            row = {"zone_id": zone["id"], "kind": zone["kind"], "area_km2": zone["area_km2"],
                   "price_alone_rub": alone["total_rub"], "asset_ids": own_assets,
                   "expected_loss_rub": round(float(sum(self.losses[i] for i, mask in enumerate(self.asset_masks) if mask & bit)), 2),
                   "doubt_share": round(float(self.zone_doubt[k]) / self.doubt_total, 4) if self.doubt_total else None}
            if index is not None and chosen & bit:
                row.update(selected=True, reason="selected")
            elif zone["delivery_hours"] > deadline:
                row.update(selected=False, reason="late")
            else:
                grown = chosen | bit
                gain_loss = round(float(losses[grown] - losses[chosen]), 2)
                gain_doubt = float(self.doubt[grown] - self.doubt[chosen])
                gain = gain_loss if priority == "risk" else gain_doubt
                row.update(selected=False, price_with_rub=round(float(prices[grown]), 2),
                           gain_loss_rub=gain_loss,
                           gain_doubt_share=round(gain_doubt / self.doubt_total, 4) if self.doubt_total else None)
                # A zone that adds nothing to the goal is out for that reason, whatever it costs.
                if index is None:
                    row["reason"] = "plan_infeasible"
                elif gain <= 1e-9:
                    row["reason"] = "nothing_new"
                elif prices[grown] > budget + 0.005:
                    row["reason"] = "over_budget"
                else:
                    row["reason"] = "cheaper_equal"
            rows.append(row)
        return rows


def plan(assets: list[dict], zones: list[dict], budget_rub: float, priority: str,
         deadline_hours: int, must_cover: str | None, tariff_multiplier: float = 1.0) -> dict:
    """Plan without map doubt, for callers that only know assets and zones."""
    return BasketSpace(zones, assets).plan(budget_rub, priority, deadline_hours, must_cover, tariff_multiplier)
