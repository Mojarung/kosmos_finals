"""Render the completed service-input experiments from recorded metrics."""
import json
from pathlib import Path

from floodvalue import paths


def main():
    root = paths.PIPELINE_ROOT / "research"
    load = lambda name: json.loads((root / name).read_text())
    primary = load("SERVICE_OOF_RESULTS.json")["results"]
    transfer = load("KURO_TRANSFER_RESULTS.json")["results"]
    routing = load("MODAL_ROUTING_RESULTS.json")["results"]
    results = {**primary, **transfer, **routing}
    if len(results) != 15 or any(r["raw"]["pooled"]["n_pixels"] != 75410539 for r in results.values()):
        raise ValueError("expected all 15 recipes evaluated on the same 75,410,539 pixels")
    context = load("fresh/recipe.json")["tracks"]["optical"]
    pool = load("KURO_POOL_MANIFEST.json")
    cpu = load("TERRAMIND_CPU_PROBE.json")
    manifest = load("SERVICE_EXPERIMENT_MANIFEST.json")
    assert len(manifest["models"]) == 25
    best_raw = max(results, key=lambda n: results[n]["raw"]["pooled"]["iou"])
    best_fitted = max(results, key=lambda n: results[n]["oof_fitted"]["pooled"]["iou"])
    reference_raw = context["candidates"][0]["iou_p05"]
    chosen = results[best_fitted]
    candidate = {"schema": "research-candidate-v1; not a floodvalue infer production recipe",
                 "selected_by": "maximum development OOF-fitted IoU among 15 disclosed recipes",
                 "name": best_fitted, "members": chosen["members"],
                 "missing_optics_members": chosen.get("missing_optics_members"),
                 "platt": chosen["platt"], "threshold": chosen["oof_fitted"]["threshold"],
                 "raw_iou_p05": chosen["raw"]["pooled"]["iou"],
                 "oof_fitted_iou": chosen["oof_fitted"]["pooled"]["iou"],
                 "model_manifest": "SERVICE_EXPERIMENT_MANIFEST.json",
                 "input_manifest": "L2A_ACQUISITION_MANIFEST.json",
                 "folds": "fresh/folds.json", "new_test_evaluation": False,
                 "future_inference_rule": "mean probabilities of five fold models per member, then recipe mixture/routing, then Platt"}
    (root / "SERVICE_CANDIDATE.json").write_text(json.dumps(candidate, indent=2))
    lines = ["# L2A, TerraMind и Kuro Siwo: завершённая итерация", "",
        "25 внешних фолдовых моделей: 15 бустингов, 5 TerraMind tiny и 5 TerraMind tiny после Kuro. "
        "Те же 332 development-чипа, пять пространственных фолдов и 75 410 539 валидных пикселей, что у context_model. "
        "Входы новых моделей — S1 + сервисные S2 L2A, без DEM/JRC/WorldCover/истории радара.", "",
        f"Лучший результат при фиксированном пороге 0,5: **{best_raw} — {results[best_raw]['raw']['pooled']['iou']:.6f}**. "
        f"Опубликованный context reference при 0,5: {reference_raw:.6f}.", "",
        f"Лучший результат с OOF-fitted Platt/порогом: **{best_fitted} — "
        f"{results[best_fitted]['oof_fitted']['pooled']['iou']:.6f}**, reference "
        f"{context['oof_iou_at_threshold']:.6f}. Это development selection, включая выбор рецепта, "
        "не независимый test и не оценка калибровки на новых данных.", "",
        "## Все зафиксированные варианты", "",
        "| Вариант | IoU p=0,5 | Raw Brier ↓ | IoU OOF-fitted | Порог после Platt | Mean event IoU, raw |",
        "|---|---:|---:|---:|---:|---:|"]
    for name, r in results.items():
        raw, fitted = r["raw"], r["oof_fitted"]
        lines.append(f"| {name} | {raw['pooled']['iou']:.6f} | {raw['brier']:.6f} | "
                     f"{fitted['pooled']['iou']:.6f} | {fitted['threshold']:.2f} | {raw['event_mean_iou']:.6f} |")
    lines += ["", "`boost_mean` — равные доли HGB/LightGBM/CatBoost. `terramind_0.xx` — указанная "
              "доля нейросети, остаток — равная смесь бустингов. `terramind_pair` — среднее двух "
              "вариантов TerraMind; `kuro_when_optics_missing` выбирает Kuro-версию только там, где "
              "нет допустимой оптики. Routing использует SCL/validity, не метки и не уверенность.", "",
              "Четыре последних варианта routing — вторая development-итерация после просмотра "
              "первых OOF-срезов: clear-ветвь — boost_mean или равная смесь с TerraMind, "
              "missing-ветвь — базовый или Kuro-TerraMind. Всего проверено 15 рецептов; "
              "максимум среди них включает оптимизм выбора на development.", "",
              "## Внешнее предобучение и отсутствие оптики", "",
              f"Проверен {pool['sampled']} тайл Kuro, допущено {pool['admitted']} из "
              f"{len(pool['admitted_events'])} событий. Загружено {pool['download_bytes']/1024**2:.0f} MiB "
              "HTTP-диапазонов. Фильтр: дата >=2020, отсутствие географического пересечения с любым "
              "из 446 benchmark-чипов плюс буфер 0,02°. Использовалась геометрия тестовых чипов, "
              "их разметка не читалась. Это retrospective transfer, данные появились после событий benchmark.", "",
              "Kuro: 1000 фиксированных шагов, event-balanced sampler, только S1 события. "
              "S2 embeddings заморожены на внешнем этапе; перенесён только backbone, decoder "
              "инициализирован как в контроле. Затем те же 25 эпох/фолд и последний checkpoint.", "",
              "| Вариант | Raw IoU с оптикой | Raw IoU без оптики |", "|---|---:|---:|"]
    for name in dict.fromkeys(["terramind", "terramind_kuro", "terramind_pair", "kuro_when_optics_missing", best_raw]):
        slices = results[name]["raw"]["slices"]
        lines.append(f"| {name} | {slices['usable_optics']['iou']:.6f} | {slices['missing_optics']['iou']:.6f} |")
    kuro_delta = results["terramind_kuro"]["raw"]["pooled"]["iou"] - results["terramind"]["raw"]["pooled"]["iou"]
    lines += ["", f"Разность Kuro и контроля при пороге 0,5: **{kuro_delta:+.6f} IoU**. "
              "Это результат конкретного малого пула и режима адаптации; он не оценивает весь Kuro Siwo.", "",
              "## Время, проверка и ограничения", "",
              f"TerraMind tiny с decoder: {cpu['parameter_count']:,} параметров; один checkpoint "
              f"{cpu['model_bytes']/1024**2:.2f} MiB. CPU, четыре потока, один fold, чип 512×512: "
              f"{min(cpu['seconds_warm']):.3f}–{max(cpu['seconds_warm']):.3f} с после прогрева. "
              "Это без загрузки данных и без пятифолдового ансамбля. "
              f"На первом по имени outer-чипе fold0 различий CPU/MPS по p>=0,5: {cpu['different_labels_p05']}.", "",
              "Проверка этой итерации: 95 тестов прошли; train/outer-составы и контрольные суммы "
              "всех 25 моделей проверены; `git diff --check` прошёл.", "",
              "- Context reference прочитан из опубликованного recipe; его auxiliary-кэши и OOF "
              "локально отсутствуют. Сравниваются системы на одинаковых фолдах, не чистый эффект "
              "архитектуры при одинаковых признаках. Парная статистическая значимость относительно context не оценивалась.",
              "- У 79/332 чипов допустимая оптика полностью отсутствует. Они не исключались. "
              "У остальных облака/недоступные пиксели также учитываются в missing-optics срезе.",
              "- Sentinel-2 связан с источником разметки Sen1Floods11. Результат S1+L2A нельзя "
              "выдавать за S1-only; он также не сопоставим напрямую с прежним S2Hand L1C test-результатом.",
              "- Общее предобучение TerraMind использует TerraMesh; географическое пересечение "
              "TerraMesh с benchmark не проверено. Не использованы Sen1Floods11-finetuned чужие веса.",
              "- Порог/Platt и выбор лучших смесей сделаны на OOF. Метки outer-фолда не выбирали "
              "эпохи сети или число деревьев: у бустингов для этого отдельный inner-fold.",
              "- Это пространственный CV внутри знакомых событий, а не leave-event-out проверка "
              "переноса на новое наводнение. Bolivia в этой итерации не переоценивалась.",
              "- Нового test-прогона нет. Старые сервисные артефакты и опубликованный test_core "
              "0,849383 для `next_optical` не изменены. Рабочие checkpoints находятся в `runs/`; "
              "их архив для Git LFS — `artifacts/service_research/`.", "",
              "## Файлы и воспроизведение", "",
              "[Команды и окружения](SERVICE_RUNBOOK.md), [основной протокол](L2A_BOOST_PROTOCOL.md), "
              "[TerraMind](TERRAMIND_PROTOCOL.md), [смеси](SERVICE_BLEND_PROTOCOL.md), "
              "[Kuro transfer](KURO_TRANSFER_PROTOCOL.md).", "",
              "[Все основные метрики по событиям/фолдам/чипам](SERVICE_OOF_RESULTS.json), "
              "[все Kuro-результаты](KURO_TRANSFER_RESULTS.json), "
              "[routing](MODAL_ROUTING_RESULTS.json) и [его post-OOF протокол](MODAL_ROUTING_PROTOCOL.md), "
              "[проверенные membership/checksums 25 моделей](SERVICE_EXPERIMENT_MANIFEST.json), "
              "[источники L2A](L2A_ACQUISITION_MANIFEST.json), [Kuro-пул](KURO_POOL_MANIFEST.json).", "",
              "[Выбранный исследовательский кандидат](SERVICE_CANDIDATE.json) фиксирует рецепт "
              "до следующего test-прогона. Это не production-схема CLI; для поставки ещё нужна "
              "интеграция inference и отдельная проверка. При будущем inference фиксируем среднее "
              "вероятностей пяти фолдов участника, затем смесь/routing, затем Platt.", "",
              "Внешние источники: [TerraMind IBM](https://github.com/IBM/terramind), "
              "[общие tiny-веса](https://huggingface.co/ibm-esa-geospatial/TerraMind-1.0-tiny), "
              "[Kuro Siwo, CC BY 4.0](https://huggingface.co/datasets/orion-ai-lab/Kuro-Siwo-Webdataset).", ""]
    (root / "SERVICE_ITERATION_RESULTS.md").write_text("\n".join(lines))
    print(json.dumps({"best_raw": best_raw, "best_raw_iou": results[best_raw]["raw"]["pooled"]["iou"],
                      "best_fitted": best_fitted, "best_fitted_iou": results[best_fitted]["oof_fitted"]["pooled"]["iou"],
                      "kuro_raw_delta": kuro_delta}))


if __name__ == "__main__":
    main()
