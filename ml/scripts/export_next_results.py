"""Export the completed five-direction development study and frozen test comparison."""
import json
import os
from pathlib import Path

import numpy as np
from sklearn.metrics import roc_auc_score
from floodvalue.fusion_calibration import fusion_calibration

os.environ.setdefault("MPLCONFIGDIR", str(Path("runs/.matplotlib").resolve()))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path("runs/next")


def main():
    selection = json.loads((ROOT/"selection_summary.json").read_text())
    if selection["pending"]:
        raise ValueError("cannot publish final results while experiments are incomplete")
    test = json.loads((ROOT/"test_comparison.json").read_text())
    radar = [json.loads(p.read_text()) for p in sorted((ROOT/"radar_screen").glob("*.json"))]
    audit = json.loads((ROOT/"release_audit.json").read_text())
    benchmark = json.loads((ROOT/"benchmark.json").read_text()) if (ROOT/"benchmark.json").exists() else None
    radar_decision = json.loads((ROOT/"radar_cv/decision.json").read_text())
    uncertainty = {}
    verification_path = ROOT/"verification.json"
    verification = json.loads(verification_path.read_text()) if verification_path.exists() else None
    if "parts" in test:
        recipe = json.loads(Path("artifacts/next_optical/recipe.json").read_text())
        previous = json.loads(Path("runs/goal072/optical_test_comparison.json").read_text())
        for part in test["parts"]:
            with np.load(ROOT/f"{part}_predictions.npz") as a:
                available = []
                for name in a["names"]:
                    with np.load(ROOT/"optical_test_cache"/part/(str(name)+".npz")) as cached:
                        available.append(cached["available"])
                probability = fusion_calibration(recipe, np.stack(available)).apply(a["logits"])
                valid = a["valid"]
                errors = (probability[valid] >= recipe["threshold"]) != a["target"][valid]
                spread = a["uncertainty"][valid]
                indices = np.random.default_rng(72).choice(len(errors), min(1_000_000, len(errors)), replace=False)
                uncertainty[part] = {"candidate_error_auroc": float(roc_auc_score(errors[indices], spread[indices])),
                                     "control_error_auroc": previous["parts"][part]["uncertainty_error_auc"]["auroc"],
                                     "sampled_pixels": len(indices), "seed": 72}
    report = {"selection": selection, "test": test, "radar_screen": radar, "radar_cv_decision": radar_decision,
              "freeze_audit": audit, "cpu_benchmark": benchmark, "uncertainty_error_auroc": uncertainty,
              "package_verification": verification,
              "modality": "S1+S2 with date-gated S1 fallback", "old_test_core_iou": .8486884685405294,
              "limits": ["Ten events in development CV; results used for model selection",
                         "Original test already inspected in the previous study",
                         "Sen1Floods11 hand labels were corrected S2 classifications",
                         "Same acquisition date does not establish exact-time operational availability"]}
    Path("research/NEXT_RESULTS.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    rows = selection["candidates"]
    winner = next(r for r in rows if r["name"] == selection["winner"])
    control = next(r for r in rows if r["name"] == "control")
    lines = ["# Следующая серия ML-экспериментов", "",
             f"Выбор по development: **{winner['name']}**. Входы — **S1+S2**, с S1 fallback по прежним правилам дат.", "",
             "Контроль до этой серии: test_core IoU **0,848688**, Bolivia **0,836838**. Чем выше IoU, тем лучше.",
             "Это другой режим входов, чем исходный S1-only результат коллеги около 0,697.", "",
             "## Что проверено", "",
             "1. HGB с 10 000 train-пикселей на чип и до 1200 итераций; отдельно hard negatives из train-only pilot.",
             "2. LightGBM и CatBoost; общий набор признаков и одинаковые event-фолды.",
             "3. SAR-pretrained ResNet50 и U-Net/MiT-B2, полный бюджет и смеси с исходным радарным ансамблем.",
             "4. Пространственная S2 U-Net/ResNet18 по шести полосам; train-only нормализация, EMA, отдельные CV-модели.",
             "5. Простые и обученные ансамбли, смешивание по качеству изображения, отдельная калибровка S2/fallback.", "",
             "## Development", "",
             "Исходные train252/valid89 и три event-фолда не менялись. В каждом CV-фолде внешние события исключены из обучения базовых моделей,",
             "метамодели и калибровки. Метамодель полной модели обучена на OOF-прогнозах исходных train-чипов;",
              "контрольные valid-чипы выбирают калибровку и порог. Обе итоговые модели оцениваются на одинаковых пикселях S1.", "",
             "В event CV радарный контроль — два ResNet34 без TTA; полный радарный контроль — пять ResNet34 с rot4.",
             "Это ограничение переноса выбора из CV в финальную модель. У оптических экспериментов один радарный контроль;",
             "строки `radar_*` дополнительно проверяют MiT в его составе.", "",
             "В финальный выбор допускается validation IoU ≥ 0,852746. Далее максимизируется средний IoU десяти отложенных",
             "событий; Brier разрешает равенство. Контроль тоже участвует. Правило записано до новых test-прогнозов.", "",
             "| Вариант | Validation IoU | Event-CV IoU | Pooled CV IoU | CV Brier | Допущен |",
             "|---|---:|---:|---:|---:|:---:|"]
    for r in rows:
        lines.append(f"| {r['name']} | {r['valid_iou']:.6f} | {r['cv_event_iou']:.6f} | {r['cv_pooled_iou']:.6f} | {r['cv_brier']:.6f} | {'да' if r['eligible'] else 'нет'} |")
    lines += ["", "Среднее по событиям не следует сравнивать с pooled test IoU: это разные выборки и способы усреднения.",
              "95% bootstrap-интервалы по десяти событиям сохранены в JSON. Они описательные: CV использовалась для отбора моделей.", "",
              "![Сравнение на отложенных событиях](figures/next_event_cv.png)", "",
              "## Радарные архитектуры", "",
              "Ниже лучший validation-вариант каждой архитектуры в смеси с прежним S1-ансамблем. Оптика в этих строках не используется.", "",
              "| Архитектура | TTA | Вес новой модели | S1 validation IoU |",
              "|---|---|---:|---:|"]
    for name in ["unet_sar50", "unet_mitb2"]:
        r = max((r for r in radar if r["model"] == name and r["mode"] == "radar"), key=lambda r: r["score"]["overall"]["micro"]["iou"])
        lines.append(f"| {name} | {r['tta']} | {r['new_weight']:.2f} | {r['score']['overall']['micro']['iou']:.6f} |")
    lines += ["", "Прежний S1-ансамбль на этой validation: 0,717470. Все отдельные и гибридные результаты сохранены в JSON.", "", "## Финальная проверка", ""]
    if test.get("new_test_evaluation") is False:
        lines += ["Development-отбор сохранил прежний контроль. Нового test-прогона не было; рабочий результат остаётся 0,848688."]
    else:
        lines += ["Параметры и контрольные суммы зафиксированы до получения новых test-прогнозов. Проверен ровно один выбранный вариант.", "",
                  "| Часть | Прежний S1+S2 | Новый S1+S2 | Новый без S2 |", "|---|---:|---:|---:|"]
        for part, r in test["parts"].items():
            lines.append(f"| {part} | {r['control']['overall']['micro']['iou']:.6f} | {r['candidate']['overall']['micro']['iou']:.6f} | {r['s1_fallback']['overall']['micro']['iou']:.6f} |")
        interval = test["parts"]["test_core"]["paired_bootstrap_vs_control"]["delta_iou_percentile_95"]
        lines += ["", f"95% описательный парный bootstrap разности test_core IoU: [{interval[0]:+.6f}, {interval[1]:+.6f}].",
                  "Чипы одного события могут коррелировать; это не независимый новый набор событий."]
        if interval[0] <= 0 <= interval[1]:
            lines += ["Интервал включает ноль: наблюдаемый выигрыш небольшой и не подтверждает устойчивое превосходство на test_core."]
        lines += ["", "Чистый S1 с собственной заранее выбранной калибровкой/порогом радарного рецепта:", ""]
        for part, r in test["parts"].items():
            lines.append(f"- {part}: {r['radar_only_own_calibration']['overall']['micro']['iou']:.6f}.")
        lines += ["", "Исходный пакет `artifacts/goal072_optical` сохранён. Новый пакет: `artifacts/next_optical/recipe.json`."]
        lines += ["", "Связь неопределённости с ошибками (AUROC, та же выборка до 1 млн пикселей, seed72):", "",
                  "| Часть | Прежняя неопределённость | Новая неопределённость |", "|---|---:|---:|"]
        for part, r in uncertainty.items():
            lines.append(f"| {part} | {r['control_error_auroc']:.6f} | {r['candidate_error_auroc']:.6f} |")
    if benchmark:
        lines += ["", "## Время оптической части на CPU", "",
                  f"Чип `{benchmark['chip']}` — первый допустимый по датам в исходном списке validation.",
                  "Медиана трёх прогонов после прогрева; включает признаки, исключает загрузку файлов/весов и радарный ансамбль.",
                  "На машине параллельно шли исследования, поэтому это ориентир стоимости, не изолированный benchmark.", "",
                  "| Модель | Секунд/чип |", "|---|---:|"]
        for name, r in benchmark["runs"].items():
            lines.append(f"| {name} | {r['median_seconds']:.3f} |")
    if verification:
        lines += ["", "## Проверка поставляемого пакета", "",
                  "CLI-инференс выполнен на CPU; контрольные суммы моделей, автономность путей, CRS, сетка, dtype, nodata и связь маски с порогом проверены.",
                  f"Среднее абсолютное отличие CPU-вероятностей от исследовательского прогона: {verification['probability_mean_abs_error_cpu_vs_evaluation']:.3g}; доля отличающихся бинарных пикселей: {verification['mask_disagreement_fraction_cpu_vs_evaluation']:.3g}.",
                  "Исходные пять радарных checkpoint не изменились."]
    lines += ["", "## Воспроизведение", "",
              "Из папки `ml/`, после подготовки данных и контрольных CV-кэшей из [предыдущего отчёта](GOAL072_OPTICAL_RESULTS.md):", "", "```bash",
              "uv sync --group research --group dev",
              "# На этом Mac LightGBM использует уже установленную библиотеку OpenMP от torch:",
              'export DYLD_LIBRARY_PATH="$PWD/.venv/lib/python3.13/site-packages/torch/lib${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"',
              "export OMP_NUM_THREADS=4",
              "uv run python -m floodvalue.next_boost --stage full",
              "uv run python -m floodvalue.next_boost --stage cv",
              "# FLOODVALUE_DEVICE=mps можно задать на Mac; без него используется CPU.",
              "uv run python scripts/goal072_architectures.py --names unet_sar50 unet_mitb2",
              "uv run python scripts/next_radar_screen.py",
              "uv run python -m floodvalue.optical_cnn --stage all",
              "uv run python scripts/next_fusion.py",
              "uv run python scripts/next_branchcal.py",
              "uv run python scripts/next_radar_cv.py",
              "uv run python scripts/next_radar_weights.py",
              "uv run python scripts/next_select.py --freeze",
              "uv run python scripts/next_evaluate.py",
              "FLOODVALUE_DEVICE=cpu uv run floodvalue infer --chip Bolivia_103757 --recipe artifacts/next_optical/recipe.json --out outputs/next_Bolivia_103757",
              "uv run python scripts/next_verify.py",
              "uv run python scripts/export_next_results.py", "```", "",
              "Модели и предсказания возобновляются из `runs/next/`. Нейросети сохраняют состояние обучения каждую эпоху.",
              "Для SAR-предобучения нужен исходный checkpoint, описанный в [исследовательском журнале](GOAL072_RESEARCH.md).", "",
              "## Ограничения", "",
              "Sen1Floods11 hand labels создавались исправлением классификаций S2: доступ к S2 связан с источником аннотаций.",
              "Это преимущество входных данных следует учитывать при сравнении с S1-only. Исходный test уже был известен до этой серии;",
              "новый результат не называется первым слепым тестом. Дата S2 не позже S1 и максимум на два дня раньше, но время суток",
              "в метаданных отсутствует. Ни пропуски оптики, ни облака не удаляют S1-пиксели из оценки.", "",
              "Неопределённость — разброс моделей, не доверительный интервал.", "",
              "Источник разметки: [Sen1Floods11, Hand Labeling](https://www.grss-ieee.org/wp-content/uploads/earthvision2020/july_stuff/webpage/papers/Bonafilia_Sen1Floods11_A_Georeferenced_Dataset_to_Train_and_Test_Deep_Learning_CVPRW_2020_paper.pdf).", ""]
    Path("research/NEXT_RESULTS.md").write_text("\n".join(lines))
    events = sorted(control["events"])
    figure, ax = plt.subplots(figsize=(10, 4))
    x = np.arange(len(events))
    ax.bar(x-.18, [control["events"][e] for e in events], .36, label="Frozen control")
    ax.bar(x+.18, [winner["events"][e] for e in events], .36, label=winner["name"])
    ax.set_xticks(x, events, rotation=25, ha="right")
    ax.set_ylabel("Held-out event IoU")
    ax.set_ylim(0, 1)
    ax.legend()
    figure.tight_layout()
    folder = Path("research/figures")
    folder.mkdir(exist_ok=True)
    figure.savefig(folder/"next_event_cv.png", dpi=160)
    plt.close(figure)
    print("Exported research/NEXT_RESULTS.md, JSON and event-CV figure")


if __name__ == "__main__":
    main()
