"""Render a Markdown summary of the saved evaluation, so reported numbers always come from code."""

from __future__ import annotations

import json
from pathlib import Path

PART_TITLES = {
    "valid": "validation (выбор параметров)",
    "test_core": "test_core (не касается train)",
    "test_adjacent": "test_adjacent (касается train)",
    "test_all": "test целиком",
    "bolivia": "Bolivia (новое событие)",
}


def _fmt(value: float | None, digits: int = 3) -> str:
    return "" if value is None else f"{value:.{digits}f}"


def methods_table(evaluation: dict, slice_name: str) -> list[str]:
    lines = [
        "| Часть | Метод | IoU | F1 | Precision | Recall | IoU по чипам | Пикселей | Воды |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for part, title in PART_TITLES.items():
        if part not in evaluation["parts"]:
            continue
        for method in ("baseline", "main"):
            block = evaluation["parts"][part][f"{slice_name}/{method}"]["overall"]
            m = block["micro"]
            lines.append(
                f"| {title} | {method} | {_fmt(m['iou'])} | {_fmt(m['f1'])} | {_fmt(m['precision'])} | "
                f"{_fmt(m['recall'])} | {_fmt(block['chip_mean_iou'])} | {m['n_pixels']} | {m['n_water']} |"
            )
    return lines


def events_table(evaluation: dict, part: str) -> list[str]:
    block = evaluation["parts"][part]
    lines = ["| Событие | Чипов | baseline IoU | main IoU | main Precision | main Recall |", "|---|---|---|---|---|---|"]
    for event, main in block["all_water/main"]["events"].items():
        base = block["all_water/baseline"]["events"][event]
        lines.append(
            f"| {event} | {main['chips']} | {_fmt(base['micro']['iou'])} | {_fmt(main['micro']['iou'])} | "
            f"{_fmt(main['micro']['precision'])} | {_fmt(main['micro']['recall'])} |"
        )
    return lines


def probability_table(evaluation: dict) -> list[str]:
    lines = [
        "| Часть | ECE до | ECE после | Brier до | Brier после | AUROC разброс ансамбля | AUROC энтропия |",
        "|---|---|---|---|---|---|---|",
    ]
    for part, block in evaluation["probability"].items():
        raw, cal = block["reliability_raw"], block["reliability_calibrated"]
        lines.append(
            f"| {PART_TITLES.get(part, part)} | {_fmt(raw['ece'], 4)} | {_fmt(cal['ece'], 4)} | "
            f"{_fmt(raw['brier'], 4)} | {_fmt(cal['brier'], 4)} | "
            f"{_fmt(block['uncertainty_member_std']['auroc'])} | {_fmt(block['uncertainty_entropy']['auroc'])} |"
        )
    return lines


def members_table(evaluation: dict) -> list[str]:
    members = evaluation.get("members_alone_p05", {})
    count = max((len(v) for v in members.values()), default=0)
    lines = ["| Часть | " + " | ".join(f"m{i}" for i in range(count)) + " | ансамбль |",
             "|---|" + "---|" * (count + 1)]
    for part, title in PART_TITLES.items():
        if part not in members:
            continue
        ensemble = evaluation["parts"][part]["all_water/main"]["overall"]["micro"]["iou"]
        cells = " | ".join(_fmt(m["iou"]) for m in members[part])
        lines.append(f"| {title} | {cells} | {_fmt(ensemble)} |")
    return lines


def postprocessing_table(evaluation: dict) -> list[str]:
    lines = ["| Мин. размер пятна, пикс. | IoU | Precision | Recall |", "|---|---|---|---|"]
    for row in evaluation.get("postprocessing_valid", []):
        lines.append(f"| {row['min_component_px']} | {_fmt(row['iou'])} | {_fmt(row['precision'])} | "
                     f"{_fmt(row['recall'])} |")
    return lines


def render(evaluation_dir: Path) -> Path:
    evaluation = json.loads((evaluation_dir / "evaluation.json").read_text(encoding="utf-8"))
    platt = evaluation["platt"]
    lines = [
        "# Сводка оценки",
        "",
        f"Порог основного метода: p >= {evaluation['threshold']:.2f}. "
        f"Калибровка Platt: a = {platt['a']:.4f}, b = {platt['b']:.4f}. Оба выбраны на validation.",
        "",
        "## Вся вода (метка 1)",
        "",
        *methods_table(evaluation, "all_water"),
        "",
        "## Без постоянной воды JRC",
        "",
        *methods_table(evaluation, "no_jrc_permanent_water"),
        "",
        "## По событиям, test целиком",
        "",
        *events_table(evaluation, "test_all"),
        "",
        "## Вероятности и неопределенность",
        "",
        *probability_table(evaluation),
        "",
        "## Отдельные модели против ансамбля",
        "",
        "Каждая модель сама по себе при p = 0,5 без калибровки; ансамбль при выбранном пороге.",
        "",
        *members_table(evaluation),
        "",
        "## Постобработка на validation (в выдаваемую маску не применяется)",
        "",
        *postprocessing_table(evaluation),
        "",
    ]
    path = evaluation_dir / "summary.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
