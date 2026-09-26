"""Publish complete event-CV screening results, including negative variants."""
import hashlib
import json
from pathlib import Path

import numpy as np

from floodvalue import paths


def main():
    root = paths.RUNS_DIR/"spatial_boost"
    rows, provenance = [], {}
    for weight in (0., .25, .5, 1.):
        scores = []
        for fold in range(3):
            path = root/f"fold{fold}/result_w{weight:.2f}.json"
            result = json.loads(path.read_text())
            scores.append(result["score"])
            provenance[str(path.relative_to(paths.PIPELINE_ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
        events = {e: v["micro"]["iou"] for score in scores for e, v in score["events"].items()}
        assert len(events) == 10
        counts = {k: sum(s["overall"]["micro"][k] for s in scores) for k in ("tp", "fp", "fn", "tn")}
        n = sum(s["probability"]["n_pixels"] for s in scores)
        rows.append({"new_optical_weight": weight, "event_mean_iou": float(np.mean(list(events.values()))),
                     "pooled_iou": counts["tp"]/(counts["tp"]+counts["fp"]+counts["fn"]),
                     "brier": sum(s["probability"]["brier"]*s["probability"]["n_pixels"] for s in scores)/n,
                     "events": events, "counts": counts, "n_pixels": n,
                     "fold_thresholds": [json.loads((root/f"fold{k}/result_w{weight:.2f}.json").read_text())["threshold"] for k in range(3)]})
    old = []
    for k in range(3):
        r = json.loads((paths.RUNS_DIR/f"next/boost/lightgbm/fold{k}/result_alpha0.75.json").read_text())
        old.extend(v["micro"]["iou"] for v in r["score"]["events"].values())
        control = json.loads((paths.RUNS_DIR/f"next/fusion/boost_learned/fold{k}/result.json").read_text())
        replay = json.loads((root/f"fold{k}/result_w0.00.json").read_text())
        assert control["score"]["per_chip"] == replay["score"]["per_chip"], "control reproduction differs"
    report = {"source_commit": "cae7833", "protocol": "research/CONTEXT_FOLLOWUP_PROTOCOL.md",
              "development_only": True, "new_test_evaluation": False,
              "decision": "keep existing boost_learned; expanded optical context rejected on event CV",
              "control_reproduced_exact_counts": True, "previous_single_lightgbm_event_iou": float(np.mean(old)),
              "rows": rows, "signature": json.loads((root/"signature.json").read_text()),
              "files_sha256": provenance}
    out = paths.PIPELINE_ROOT/"research/SPATIAL_BOOST_RESULTS.json"
    out.write_text(json.dumps(report, indent=2, allow_nan=False))
    lines = ["# Пространственный контекст бустинга: завершённый скрининг", "",
             "На трёх фолдах с исключением целых событий расширение оптических окрестностей не улучшило контроль.",
             "Сохраняем `boost_learned`. Нового test-прогона не было, поставляемые модели не изменены.", "",
             "[Протокол](CONTEXT_FOLLOWUP_PROTOCOL.md). Контроль полностью воспроизвёл confusion counts каждого чипа.", "",
             "| Доля нового бустинга в оптической ветке | Event IoU | Pooled IoU | Brier |", "|---:|---:|---:|---:|"]
    for row in rows:
        lines.append(f"| {row['new_optical_weight']:.2f} | {row['event_mean_iou']:.6f} | {row['pooled_iou']:.6f} | {row['brier']:.6f} |")
    lines += ["", f"Старый отдельный LightGBM: event IoU {np.mean(old):.6f}; новый отдельный: {rows[-1]['event_mean_iou']:.6f}.",
              "Оба используют вес оптики 0,75, один радарный контроль и одинаковые пиксели.", "",
              "На первых двух фолдах доля 0,25 давала небольшой плюс, на третьем — минус.",
              "Это показывает, почему решение нельзя принимать по одному удачному фолду.", "",
              "| Событие | Контроль | Доля нового 0,25 | Разность |", "|---|---:|---:|---:|"]
    for event, old_iou in sorted(rows[0]["events"].items()):
        new_iou = rows[1]["events"][event]
        lines.append(f"| {event} | {old_iou:.6f} | {new_iou:.6f} | {new_iou-old_iou:+.6f} |")
    lines += ["", "Признаки 47+18: прежние спектральные/радарные признаки плюс среднее, стандартное отклонение и",
              "контраст NDWI/MNDWI/NDVI в окрестностях 27/81. LightGBM, одинаковые seed, параметры и пиксели.",
              "Три модели выбрали early stopping на 230/456/397 итерациях. Калибровка и пороги — inner valid.",
              "Среднее event IoU является development-метрикой; оно не сравнивается напрямую с test_core 0,84938.", "",
              "```bash", "uv sync --frozen --group research --group dev",
              'export DYLD_LIBRARY_PATH="$PWD/.venv/lib/python3.13/site-packages/torch/lib${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"',
              "OMP_NUM_THREADS=4 uv run python -m floodvalue.spatial_boost", "uv run python scripts/export_spatial_boost.py", "```", "",
              "Нужны прежние CV-прогнозы и обучающие кэши из NEXT_RESULTS.md. Прогнозы и веса этого скрининга",
              "хранятся отдельно в `runs/spatial_boost/`; подписи кода и CV-плана защищают повторное использование кэшей."]
    (out.with_suffix(".md")).write_text("\n".join(lines)+"\n")
    print(json.dumps({"report": str(out), "control": rows[0]["event_mean_iou"], "best_new": max(r["event_mean_iou"] for r in rows[1:])}))


if __name__ == "__main__":
    main()
