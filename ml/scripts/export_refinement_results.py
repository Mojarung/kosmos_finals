"""Publish the frozen same-split comparison, including paired chip uncertainty."""
import hashlib
import json
from pathlib import Path
import platform

import numpy as np
import torch


def bootstrap_delta(original, candidate, seed=42, draws=5000):
    names = sorted(original["per_chip"])
    if names != sorted(candidate["per_chip"]):
        raise ValueError("comparison chip sets differ")
    arrays = []
    for score in [original, candidate]:
        arrays.append(np.array([[score["per_chip"][n][k] for k in ["tp", "fp", "fn"]]
                                for n in names], dtype=np.float64))
    idx = np.random.default_rng(seed).integers(len(names), size=(draws, len(names)))
    values = [a[idx].sum(axis=1) for a in arrays]
    ious = [v[:, 0]/np.maximum(v.sum(axis=1), 1) for v in values]
    delta = ious[1]-ious[0]
    return {"unit": "chip", "draws": draws, "seed": seed,
            "delta_iou_percentile_95": np.quantile(delta, [.025, .975]).tolist(),
            "note": "Descriptive paired chip bootstrap; chips in an event can remain correlated."}


def main():
    root = Path("runs/refine")
    result = json.loads((root/"test_comparison.json").read_text())
    recipe = json.loads((root/"release_recipe.json").read_text())
    audit = json.loads((root/"release_audit.json").read_text())
    if hashlib.sha256((root/"release_recipe.json").read_bytes()).hexdigest() != result["candidate_recipe_sha256"]:
        raise ValueError("comparison no longer matches recipe")
    result["selection"] = audit
    published = json.loads(Path("artifacts/evaluation/evaluation.json").read_text())
    result["published_original_iou"] = {part: published["parts"][part]["all_water/main"]["overall"]["micro"]["iou"]
                                        for part in result["parts"]}
    result["candidate"] = {k: recipe[k] for k in ["weights", "threshold", "space", "calibration_method", "validation"]}
    result["candidate"]["members"] = [{k: m[k] for k in ["member", "weights_sha256", "tta"]} for m in recipe["members"]]
    result["training"] = []
    for path in sorted((root/"members").glob("*/*/train_summary.json")):
        r = json.loads(path.read_text())
        result["training"].append({"path": str(path.parent), "config": r["config"], "best": r["best"],
                                   "training_seconds": r["history"][-1]["seconds"]})
    result["environment"] = {"platform": platform.platform(), "python": platform.python_version(),
                               "torch": torch.__version__, "evaluation_device": "mps", "precision": "float32"}
    result["inference_checks"] = {p.stem: json.loads(p.read_text()) for p in
                                  [root/"cpu_parity.json", root/"cpu_smoke.json", root/"release_checks.json"] if p.exists()}
    sources = list(Path("src/floodvalue").glob("*.py")) + list(Path("scripts").glob("*refin*.py"))
    result["source_sha256"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sources)}
    lines = ["# Развитие ансамбля коллеги: сравнение на исходном split", "",
             "Основная метрика — pooled IoU воды; больше лучше. Оба варианта оценены на одних чипах, "
             "одних валидных пикселях и одном устройстве. Ранее опубликованные 0,697 — округлённый "
             "результат исходной модели. Ниже её повторный прогон рядом с новым рецептом.", "",
             "| Часть | Исходный IoU | Новый IoU | Δ IoU | Исходный Brier | Новый Brier |",
             "|---|---:|---:|---:|---:|---:|"]
    for part, scores in result["parts"].items():
        a, b = scores["original"], scores["candidate"]
        old, new = a["overall"]["micro"]["iou"], b["overall"]["micro"]["iou"]
        scores["paired_bootstrap"] = bootstrap_delta(a, b)
        lines.append(f"| {part} | {old:.6f} | {new:.6f} | {new-old:+.6f} | "
                     f"{a['probability']['brier']:.6f} | {b['probability']['brier']:.6f} |")
    lines += ["", "На test_core есть положительный сдвиг, на Bolivia — регрессия. "
              "Рецепт остаётся отдельным экспериментальным вариантом; исходный v1 сохранён по умолчанию.", "",
              "Опубликованные исходные IoU: "+", ".join(f"{part} {value:.6f}" for part,value in result["published_original_iou"].items())+". "
              "Таблица выше использует повторный прогон исходных весов на MPS/FP32; исходная оценка выполнялась "
              "на CUDA с AMP. Разницу между опубликованным и повторным результатами не приписываем улучшению модели."]
    passes = sum({"none": 1, "rot4": 4, "d4": 8}[m["tta"]] for m in recipe["members"])
    ci = result["parts"]["test_core"]["paired_bootstrap"]["delta_iou_percentile_95"]
    lines += ["", "## Выбранный рецепт", "",
              f"- Рецепт: `{recipe['selection']['validation_ranking'][0]['path']}`.",
              f"- Validation IoU: {recipe['validation']['overall']['micro']['iou']:.6f}.",
              f"- Членов ансамбля: {len(recipe['members'])}; проходов сети на чип: {passes} (исходно 5).",
              f"- Калибровка: {recipe['calibration_method']}; порог: {recipe['threshold']:.2f}.",
              "- Коэффициенты: "+", ".join(f"{w/sum(recipe['weights']):.6f}" for w in recipe["weights"])+".",
              "- Test не использовался для выбора дообучения, TTA, коэффициентов, калибровки или порога.",
              "", "Проверены обычное продолжение BCE+Dice, переход на BCE+Lovasz, кроп 384, "
              "заморозка статистики BatchNorm, TTA и положительные коэффициенты ансамбля. "
              "Полный журнал выбора и результаты отдельных обучений сохранены в JSON рядом.", "",
              "## Ограничения", "",
              f"Парный bootstrap по чипам test_core: 95% интервал Δ IoU [{ci[0]:+.6f}, {ci[1]:+.6f}]. "
              "Это описательная оценка: чипы одного события могут быть зависимы. Малый положительный "
              "сдвиг сам по себе не доказывает устойчивый выигрыш на новых событиях.", "",
              "Validation использовалась адаптивно для нескольких вариантов. Опубликованный исходный "
              "test score был заранее известен, но новые test-предсказания получены только после фиксации "
              "одного рецепта. Окончательный тест не является CV. Старые event-held-out CV результаты "
              "не сравниваются численно с этой таблицей.", "",
              "## Запуск", "", "```bash", "cd ml", "uv run floodvalue infer --chip Bolivia_103757 \\",
              "  --recipe artifacts/refined_model/recipe.json --out outputs/refined_Bolivia_103757", "```", "",
              "Исходные веса и обычный infer сохранены. Новый рецепт упакован отдельно; контрольные "
              "суммы проверяются при загрузке. Вероятность, маска, разброс ансамбля и геопривязка "
              "сохраняют исходный контракт. CPU поддерживается; TTA увеличивает время инференса.", "",
              "Протокол: [REFINEMENT_PROTOCOL.md](REFINEMENT_PROTOCOL.md). "
              "Полные метрики: [refinement_results.json](refinement_results.json)."]
    Path("research/refinement_results.json").write_text(json.dumps(result, indent=2, allow_nan=False))
    Path("research/REFINEMENT_RESULTS.md").write_text("\n".join(lines)+"\n")
    package = Path("artifacts/refined_model")
    if (package/"recipe.json").exists():
        card = {"status": "experimental: test_core improvement, Bolivia regression; not the default",
                "recipe_sha256": hashlib.sha256((package/"recipe.json").read_bytes()).hexdigest(),
                "networks": "five original U-Net/ResNet34 checkpoints; original training-only normalization",
                "changes": "positive ensemble weights, four rotations, isotonic calibration; validation selected",
                "forward_passes_per_chip": passes, "threshold": recipe["threshold"],
                "comparison_recipe_sha256": result["candidate_recipe_sha256"],
                "same_device_iou": {part: {label: scores[label]["overall"]["micro"]["iou"]
                                            for label in ["original", "candidate"]}
                                    for part,scores in result["parts"].items()},
                "test_core_delta_iou_bootstrap_95": ci, "inference_checks": result["inference_checks"]}
        (package/"model_card.json").write_text(json.dumps(card, indent=2, allow_nan=False))
    print("Wrote research/REFINEMENT_RESULTS.md and refinement_results.json", flush=True)


if __name__ == "__main__":
    main()
