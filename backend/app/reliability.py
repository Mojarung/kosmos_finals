"""Where the calibrated p misses the observed water share on independent chips.

Platt was fitted on validation. On test_core its reliability bins bend: water is rarer than p in the middle
of the scale and more common than p below 0.4. Two parameters cannot remove that bend and refitting on
test_core would leak, so the service keeps p (EL = p x V x q is the case formula) and says where it misses.
"""
import json
import os
from pathlib import Path

RELIABILITY_FILE = Path(os.environ.get("RELIABILITY_FILE", Path(__file__).resolve().parents[2] / "ml" / "artifacts"
                                       / "evaluation" / "reliability_test_core.json"))
_RECORD = json.loads(RELIABILITY_FILE.read_text(encoding="utf-8"))
_TEST = _RECORD["test_core"]["calibrated"]
_BINS = _TEST["equal_width_bins"]
# A bin is a miss when its mean p and its water share differ by 3 p.p. or more: the edge behind the bands
# of the ML report (p 0.07-0.40 and 0.47-0.93); the overall test_core ECE is 0.012.
GAP = 0.03


def _gap(item: dict) -> float:
    return item["mean_p"] - item["water_fraction"]


def _bin(p: float) -> dict:
    p = min(max(p, 0.0), 1.0)
    return next((item for item in _BINS if item["lo"] <= p < item["hi"]), _BINS[-1])


def _band(sign: int) -> list[float] | None:
    hits = [item for item in _BINS if sign * _gap(item) >= GAP]
    return [round(hits[0]["lo"], 3), round(hits[-1]["hi"], 3)] if hits else None


def p_check(p: float | None) -> dict | None:
    """How often pixels with this calibrated p were water on test_core, and which way p misses."""
    if p is None:
        return None
    item = _bin(p)
    gap = _gap(item)
    status = "overestimated" if gap >= GAP else "underestimated" if gap <= -GAP else "consistent"
    return {"status": status, "observed_water_share": round(item["water_fraction"], 3),
            "bin_p": [round(item["lo"], 3), round(item["hi"], 3)], "bin_mean_p": round(item["mean_p"], 3)}


_WORST = max(_BINS, key=lambda item: abs(_gap(item)))
_UNDER_GAPS = [-_gap(item) for item in _BINS if _gap(item) <= -GAP]

BIAS = {
    "part": "test_core",
    "source": "ml/artifacts/evaluation/reliability_test_core.json (floodvalue reliability; Platt fitted on validation)",
    "ece": round(_TEST["ece_equal_width"], 4),
    "ece_mid_range": round(_TEST["ece_mid_range"], 4),
    "validation_ece_mid_range": round(_RECORD["validation"]["calibrated"]["ece_mid_range"], 4),
    "mid_range_share_of_pixels": _TEST["mid_range_share_of_pixels"],
    "gap_flag": GAP,
    "overestimated_p": _band(1),
    "underestimated_p": _band(-1),
    "worst_bin": {"p": [round(_WORST["lo"], 3), round(_WORST["hi"], 3)], "mean_p": round(_WORST["mean_p"], 3),
                  "observed_water_share": round(_WORST["water_fraction"], 3)},
    "rule": ("p_check.status of an asset is overestimated (underestimated) when, on test_core, pixels of its "
             "1/15-wide p bin were water at least 0.03 less (more) often than their mean p; EL stays p x V x q"),
}


def _ru(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def _limitation() -> str:
    over, under, worst = BIAS["overestimated_p"], BIAS["underestimated_p"], BIAS["worst_bin"]
    parts = []
    if over:
        parts.append(f"при p от {_ru(over[0])} до {_ru(over[1])} воды меньше, чем обещает p "
                     f"(при p около {_ru(worst['mean_p'])} водой были "
                     f"{round(worst['observed_water_share'] * 100)}% пикселей)")
    if under:
        parts.append(f"при p от {_ru(under[0])} до {_ru(under[1])} воды больше на "
                     f"{round(min(_UNDER_GAPS) * 100)}–{round(max(_UNDER_GAPS) * 100)} п.п.")
    text = "Калибровка p: на независимых снимках test_core " + ", ".join(parts)
    # «п.п.» уже кончается точкой, вторая не нужна.
    return text + ("" if text.endswith(".") else ".") + " EL таких объектов считается по p, как требует кейс, " \
                                                         "а сами объекты помечены."


LIMITATION = _limitation() if BIAS["overestimated_p"] or BIAS["underestimated_p"] else None
