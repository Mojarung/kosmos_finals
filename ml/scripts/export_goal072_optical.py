"""Export the frozen multimodal comparison with its separate S1-only control."""
import hashlib
import json
from pathlib import Path
import platform
import os

os.environ.setdefault("MPLCONFIGDIR", str(Path("runs/.matplotlib").resolve()))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import sklearn
import torch


def main():
    root = Path("runs/goal072")
    result = json.loads((root/"optical_test_comparison.json").read_text())
    recipe = json.loads((root/"optical_release_recipe.json").read_text())
    if hashlib.sha256((root/"optical_release_recipe.json").read_bytes()).hexdigest() != result["recipe_sha256"]:
        raise ValueError("results and frozen optical recipe differ")
    result["audit"] = json.loads((root/"optical_release_audit.json").read_text())
    result["cv"] = json.loads((root/"optical_cv_summary.json").read_text())
    result["validation"] = recipe["validation"]
    result["environment"] = {"python": platform.python_version(), "torch": torch.__version__,
                               "sklearn": sklearn.__version__, "platform": platform.platform(),
                               "radar_prediction_device": "MPS FP32", "optical_prediction_device": "CPU"}
    checks = root/"optical_inference_checks.json"
    if checks.exists():
        result["inference_checks"] = json.loads(checks.read_text())
    Path("research/goal072_optical_results.json").write_text(json.dumps(result, indent=2, allow_nan=False))
    winner = next(r for r in result["cv"] if r["alpha"] == recipe["optical_weight"])
    control = next(r for r in result["cv"] if r["alpha"] == 0)
    lines = ["# S1+S2: отдельное расширение модели воды", "",
             "Метрика — pooled IoU воды, больше лучше. Оценка использует исходные test_core (55 чипов), "
             "test_adjacent (35) и Bolivia (15), на прежних finite(VV/VH) & label-valid пикселях.", "",
             "**Входы расширены оптикой Sentinel-2. Эти числа нельзя выдавать за улучшение S1-only.** "
             "Радарный v1 и ранее выпущенный S1-only рецепт 0,704383 сохранены отдельно.", "",
             "| Часть | Исходный S1 v1 | S1 контроль, новый способ усреднения | S1+S2 | Brier S1+S2 |",
             "|---|---:|---:|---:|---:|"]
    for part, scores in result["parts"].items():
        values = [scores[k]["overall"]["micro"]["iou"] for k in ["original", "radar_control", "s1_s2"]]
        lines.append(f"| {part} | {values[0]:.6f} | {values[1]:.6f} | {values[2]:.6f} | "
                     f"{scores['s1_s2']['probability']['brier']:.6f} |")
    ci = result["parts"]["test_core"]["paired_bootstrap"]["delta_iou_percentile_95"]
    lines += ["", "Исходник повторно оценён на MPS/FP32; опубликованный результат коллеги округлялся "
              "до 0,697. Разницу устройств/точности не считаем улучшением.", "",
              f"Исходная validation: {recipe['validation']['overall']['micro']['iou']:.6f}. "
              f"Event CV, среднее по 10 отложенным событиям: {winner['cv_event_mean_iou']:.6f} "
              f"против {control['cv_event_mean_iou']:.6f} у S1-контроля. "
              "Это другая проверка и другое число; оно не подменяет test_core.", "",
              "При полном отключении S2 тот же расширенный рецепт (с уже зафиксированной общей "
              "калибровкой) даёт: "+", ".join(f"{part} {scores['s1_fallback']['overall']['micro']['iou']:.6f}"
                                                       for part, scores in result["parts"].items())+". "
              "Это проверка деградации входов, а не новый подбор S1-модели по test.", "",
              f"Выбран вес оптики {recipe['optical_weight']:.2f}, вес S1 {1-recipe['optical_weight']:.2f}; "
              f"изотоническая калибровка и порог {recipe['threshold']:.2f} взяты только с validation. "
              "Среди почти одинаковых validation-вариантов выбран лучший по event CV. "
              "Рецепт и контрольные суммы зафиксированы до новых test-прогнозов.", "",
              f"Парный bootstrap по test_core-чипам: 95% интервал прироста относительно v1 "
              f"[{ci[0]:+.6f}, {ci[1]:+.6f}]. Чипы внутри событий зависимы; это описательная оценка.", "",
              "## Данные и ограничения", "",
              "Бустинг обучается на исходных train-чипах, по 3500 случайных пикселей с каждого допустимого "
              "чипа. Признаки: 13 полос S2, спектральные индексы и местные средние плюс VV/VH-текстуры. "
              "Координаты, ID события и тестовые метки в признаки не входят.", "",
              "S2 допускается только в тот же календарный день, что S1, или на 1–2 дня раньше. "
              "В Ghana и Spain оптика снята позже, поэтому используется радарный прогноз. "
              "Время суток неизвестно: доступность на точный момент съёмки не доказана. "
              "S2Hand не содержит QA/cloud-mask; облачность может портить результат. "
              "Пиксели с облаками или без допустимой S2 не исключаются из оценки. "
              "Без S2 и при отсутствии дат инференс использует S1 fallback.", "",
              "**Происхождение разметки.** Метки Sen1Floods11 получены ручным исправлением "
              "классификации по Sentinel-2 с просмотром S2-композитов и S1 VH. "
              "Поэтому S2 на инференсе предоставляет информацию из источника построения меток. "
              "Оценка 0,848688 характеризует режим с доступной оптикой; отдельные S1-результаты "
              "и проверка без S2 приведены выше. "
              "[Первичная статья, раздел Hand Labeling](https://www.grss-ieee.org/wp-content/uploads/earthvision2020/july_stuff/webpage/papers/Bonafilia_Sen1Floods11_A_Georeferenced_Dataset_to_Train_and_Test_Deep_Learning_CVPRW_2020_paper.pdf).", "",
              "Вероятность и маска сохраняют исходный GeoTIFF-контракт. В расширенном режиме "
              "uncertainty = половина абсолютного расхождения вероятностей S1 и S2, а на fallback-пикселях — "
              "разброс радарного ансамбля. Это индикатор несогласия моделей, не доверительный интервал. "
              "AUROC этого индикатора для ошибок отдельно сохранён по всем частям в JSON.", "",
              "Validation и CV использовались для разработки, а прежний test-score уже был известен. "
              "Финальный test не следует называть впервые открытым слепым тестом. Новые тестовые метки "
              "не участвовали в обучении, выборе смеси, калибровке или пороге.", "",
              "## Запуск без переобучения", "", "```bash", "cd ml", "uv sync",
              "uv run floodvalue download",
              "uv run python scripts/goal072_download_s2.py --parts train valid test_core test_adjacent bolivia",
              "uv run floodvalue infer --chip Bolivia_103757 \\",
              "  --recipe artifacts/goal072_optical/recipe.json --out outputs/optical_Bolivia_103757",
              "uv run floodvalue infer --chip Bolivia_103757 --no-optical \\",
              "  --recipe artifacts/goal072_optical/recipe.json --out outputs/optical_fallback_Bolivia_103757",
              "```", "", "S2 загружается отдельно (~1,02 ГБ на все 446 чипов), с проверкой MD5. "
              "Для своего GeoTIFF передаются --s1, --s2, --s1-date и --s2-date. "
              "S2 должен содержать официальные 13 полос, reflectance×10000, точно на сетке S1. "
              "Обычная команда без --recipe по-прежнему запускает исходный S1 v1.", "",
              "## Повтор обучения и проверки", "", "```bash",
              "uv run python scripts/goal072_prepare_optical.py",
              "OMP_NUM_THREADS=4 uv run python -m floodvalue.optical",
              "uv run python scripts/run_cv_sweep.py --configs cv_r34_control --seeds 0 1",
              "OMP_NUM_THREADS=4 uv run python scripts/goal072_optical_cv.py",
              "OMP_NUM_THREADS=4 uv run python scripts/goal072_optical_evaluate.py",
              "uv run python scripts/export_goal072_optical.py", "```", "",
              "Для обучения/инференса сетей на Apple GPU задаётся FLOODVALUE_DEVICE=mps; CPU поддерживается. "
              "Численная точность CPU/MPS может немного отличаться.", "",
              "Источники: [официальные данные и полосы Sen1Floods11](https://github.com/cloudtostreet/Sen1Floods11), "
              "[решения STAC Overflow с пиксельным бустингом](https://github.com/drivendataorg/stac-overflow). "
              "Это собственное обучение; готовые flood-веса сторонних команд не использованы.", "",
              "[Полные метрики и аудит](goal072_optical_results.json), "
              "[журнал исследований](GOAL072_RESEARCH.md).", "",
              "![Сравнение и калибровка](figures/goal072_optical.png)"]
    Path("research/GOAL072_OPTICAL_RESULTS.md").write_text("\n".join(lines)+"\n")
    package = Path("artifacts/goal072_optical")
    packaged = package/"recipe.json"
    card = {"input_mode": "S1+S2 with date-gated S1 fallback; not an S1-only score",
            "recipe_sha256": hashlib.sha256(packaged.read_bytes()).hexdigest(),
            "comparison_recipe_sha256": result["recipe_sha256"], "date_policy": recipe["date_policy"],
            "original_validation_iou": recipe["validation"]["overall"]["micro"]["iou"],
            "event_cv_mean_iou": winner["cv_event_mean_iou"],
            "test_iou": {part: scores["s1_s2"]["overall"]["micro"]["iou"] for part, scores in result["parts"].items()},
            "without_optical_test_iou": {part: scores["s1_fallback"]["overall"]["micro"]["iou"] for part, scores in result["parts"].items()},
            "inference_checks": result.get("inference_checks"), "selection": recipe["selection"]}
    (package/"model_card.json").write_text(json.dumps(card, indent=2, allow_nan=False))
    figure_dir = Path("research/figures")
    figure_dir.mkdir(exist_ok=True)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), constrained_layout=True)
    x = np.arange(3)
    for j, (key, label) in enumerate([("original", "S1 v1"), ("radar_control", "S1 control"), ("s1_s2", "S1 + S2")]):
        values = [p[key]["overall"]["micro"]["iou"] for p in result["parts"].values()]
        axes[0].bar(x+(j-1)*.24, values, width=.23, label=label)
    axes[0].axhline(.72, color="black", linestyle="--", linewidth=1, label="Target 0.72")
    axes[0].set(xticks=x, xticklabels=["test_core", "adjacent", "Bolivia"], ylim=(0, 1), ylabel="Water IoU")
    axes[0].legend(fontsize=8)
    for key, label in [("original", "S1 v1"), ("s1_s2", "S1 + S2")]:
        bins = result["parts"]["test_core"][key]["probability"]["bins"]
        bins = [b for b in bins if b["count"]]
        axes[1].plot([b["mean_p"] for b in bins], [b["water_fraction"] for b in bins], "o-", label=label)
    axes[1].plot([0, 1], [0, 1], "k--", linewidth=1)
    axes[1].set(xlabel="Predicted water probability", ylabel="Observed water fraction", title="test_core calibration")
    axes[1].legend()
    fig.savefig(figure_dir/"goal072_optical.png", dpi=180)
    fig.savefig(figure_dir/"goal072_optical.pdf")
    plt.close(fig)
    print("Wrote optical report, JSON, model card and figures", flush=True)


if __name__ == "__main__":
    main()
