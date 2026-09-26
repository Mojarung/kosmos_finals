# L2A, TerraMind и Kuro Siwo: завершённая итерация

25 внешних фолдовых моделей: 15 бустингов, 5 TerraMind tiny и 5 TerraMind tiny после Kuro. Те же 332 development-чипа, пять пространственных фолдов и 75 410 539 валидных пикселей, что у context_model. Входы новых моделей — S1 + сервисные S2 L2A, без DEM/JRC/WorldCover/истории радара.

Лучший результат при фиксированном пороге 0,5: **boost_clear_tm_missing — 0.786124**. Опубликованный context reference при 0,5: 0.759775.

Лучший результат с OOF-fitted Platt/порогом: **boost_clear_tm_missing — 0.788729**, reference 0.760235. Это development selection, включая выбор рецепта, не независимый test и не оценка калибровки на новых данных.

## Все зафиксированные варианты

| Вариант | IoU p=0,5 | Raw Brier ↓ | IoU OOF-fitted | Порог после Platt | Mean event IoU, raw |
|---|---:|---:|---:|---:|---:|
| terramind | 0.771075 | 0.019784 | 0.772780 | 0.36 | 0.744260 |
| hgb | 0.734671 | 0.021349 | 0.737442 | 0.43 | 0.685824 |
| lightgbm | 0.736278 | 0.021218 | 0.739198 | 0.43 | 0.687072 |
| catboost | 0.740799 | 0.020957 | 0.744983 | 0.42 | 0.689736 |
| boost_mean | 0.738413 | 0.020985 | 0.742643 | 0.42 | 0.688933 |
| terramind_0.25 | 0.759155 | 0.019200 | 0.764373 | 0.42 | 0.716187 |
| terramind_0.50 | 0.774113 | 0.018405 | 0.779356 | 0.42 | 0.740443 |
| terramind_0.75 | 0.774120 | 0.018599 | 0.776729 | 0.38 | 0.745736 |
| terramind_kuro | 0.765751 | 0.020348 | 0.767111 | 0.36 | 0.738309 |
| terramind_pair | 0.769096 | 0.019836 | 0.770960 | 0.36 | 0.742355 |
| kuro_when_optics_missing | 0.764410 | 0.020472 | 0.765850 | 0.36 | 0.737579 |
| boost_clear_tm_missing | 0.786124 | 0.018073 | 0.788729 | 0.39 | 0.754400 |
| blend_clear_tm_missing | 0.782964 | 0.018272 | 0.784323 | 0.40 | 0.753421 |
| boost_clear_kuro_missing | 0.779284 | 0.018761 | 0.781643 | 0.39 | 0.747648 |
| blend_clear_kuro_missing | 0.776176 | 0.018959 | 0.777329 | 0.40 | 0.746708 |

`boost_mean` — равные доли HGB/LightGBM/CatBoost. `terramind_0.xx` — указанная доля нейросети, остаток — равная смесь бустингов. `terramind_pair` — среднее двух вариантов TerraMind; `kuro_when_optics_missing` выбирает Kuro-версию только там, где нет допустимой оптики. Routing использует SCL/validity, не метки и не уверенность.

Четыре последних варианта routing — вторая development-итерация после просмотра первых OOF-срезов: clear-ветвь — boost_mean или равная смесь с TerraMind, missing-ветвь — базовый или Kuro-TerraMind. Всего проверено 15 рецептов; максимум среди них включает оптимизм выбора на development.

## Внешнее предобучение и отсутствие оптики

Проверен 361 тайл Kuro, допущено 160 из 12 событий. Загружено 800 MiB HTTP-диапазонов. Фильтр: дата >=2020, отсутствие географического пересечения с любым из 446 benchmark-чипов плюс буфер 0,02°. Использовалась геометрия тестовых чипов, их разметка не читалась. Это retrospective transfer, данные появились после событий benchmark.

Kuro: 1000 фиксированных шагов, event-balanced sampler, только S1 события. S2 embeddings заморожены на внешнем этапе; перенесён только backbone, decoder инициализирован как в контроле. Затем те же 25 эпох/фолд и последний checkpoint.

| Вариант | Raw IoU с оптикой | Raw IoU без оптики |
|---|---:|---:|
| terramind | 0.823988 | 0.674291 |
| terramind_kuro | 0.826080 | 0.656715 |
| terramind_pair | 0.825313 | 0.666703 |
| kuro_when_optics_missing | 0.823988 | 0.656715 |
| boost_clear_tm_missing | 0.848848 | 0.674291 |

Разность Kuro и контроля при пороге 0,5: **-0.005325 IoU**. Это результат конкретного малого пула и режима адаптации; он не оценивает весь Kuro Siwo.

## Время, проверка и ограничения

TerraMind tiny с decoder: 6,141,057 параметров; один checkpoint 23.75 MiB. CPU, четыре потока, один fold, чип 512×512: 0.158–0.170 с после прогрева. Это без загрузки данных и без пятифолдового ансамбля. На первом по имени outer-чипе fold0 различий CPU/MPS по p>=0,5: 0.

Проверка этой итерации: 95 тестов прошли; train/outer-составы и контрольные суммы всех 25 моделей проверены; `git diff --check` прошёл.

- Context reference прочитан из опубликованного recipe; его auxiliary-кэши и OOF локально отсутствуют. Сравниваются системы на одинаковых фолдах, не чистый эффект архитектуры при одинаковых признаках. Парная статистическая значимость относительно context не оценивалась.
- У 79/332 чипов допустимая оптика полностью отсутствует. Они не исключались. У остальных облака/недоступные пиксели также учитываются в missing-optics срезе.
- Sentinel-2 связан с источником разметки Sen1Floods11. Результат S1+L2A нельзя выдавать за S1-only; он также не сопоставим напрямую с прежним S2Hand L1C test-результатом.
- Общее предобучение TerraMind использует TerraMesh; географическое пересечение TerraMesh с benchmark не проверено. Не использованы Sen1Floods11-finetuned чужие веса.
- Порог/Platt и выбор лучших смесей сделаны на OOF. Метки outer-фолда не выбирали эпохи сети или число деревьев: у бустингов для этого отдельный inner-fold.
- Это пространственный CV внутри знакомых событий, а не leave-event-out проверка переноса на новое наводнение. Bolivia в этой итерации не переоценивалась.
- Нового test-прогона нет. Старые сервисные артефакты и опубликованный test_core 0,849383 для `next_optical` не изменены. Рабочие checkpoints находятся в `runs/`; их архив для Git LFS — `artifacts/service_research/`.

## Файлы и воспроизведение

[Команды и окружения](SERVICE_RUNBOOK.md), [основной протокол](L2A_BOOST_PROTOCOL.md), [TerraMind](TERRAMIND_PROTOCOL.md), [смеси](SERVICE_BLEND_PROTOCOL.md), [Kuro transfer](KURO_TRANSFER_PROTOCOL.md).

[Все основные метрики по событиям/фолдам/чипам](SERVICE_OOF_RESULTS.json), [все Kuro-результаты](KURO_TRANSFER_RESULTS.json), [routing](MODAL_ROUTING_RESULTS.json) и [его post-OOF протокол](MODAL_ROUTING_PROTOCOL.md), [проверенные membership/checksums 25 моделей](SERVICE_EXPERIMENT_MANIFEST.json), [источники L2A](L2A_ACQUISITION_MANIFEST.json), [Kuro-пул](KURO_POOL_MANIFEST.json).

[Выбранный исследовательский кандидат](SERVICE_CANDIDATE.json) фиксирует рецепт до следующего test-прогона. Это не production-схема CLI; для поставки ещё нужна интеграция inference и отдельная проверка. При будущем inference фиксируем среднее вероятностей пяти фолдов участника, затем смесь/routing, затем Platt.

Внешние источники: [TerraMind IBM](https://github.com/IBM/terramind), [общие tiny-веса](https://huggingface.co/ibm-esa-geospatial/TerraMind-1.0-tiny), [Kuro Siwo, CC BY 4.0](https://huggingface.co/datasets/orion-ai-lab/Kuro-Siwo-Webdataset).
