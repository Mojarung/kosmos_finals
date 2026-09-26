# Архив ML-экспериментов: L2A, TerraMind, Kuro Siwo

Исследовательские checkpoints и метаданные; существующие `next_optical` и
`context_model` не заменены. Этот каталог не является production-рецептом `infer`.

- 25 фолдовых моделей: 15 HGB/LightGBM/CatBoost, 5 TerraMind tiny, 5 TerraMind + Kuro.
- Backbone после внешнего предобучения на Kuro; 3 модели прежнего отрицательного
  эксперимента `spatial_boost` также сохранены.
- Составы train/outer, inner-selection, подписи экспериментов и исходный STAC-план.
- Все бинарные веса — Git LFS. `manifest.json` содержит SHA256 каждого файла.

После клонирования, из корня репозитория:

```bash
git lfs pull
cd ml
uv sync --group research --group dev
uv run python scripts/package_service_research.py verify
uv run python scripts/package_service_research.py restore
```

`restore` копирует файлы в исходные `runs/` и план в `data/`, не перезаписывая
отличающиеся существующие файлы. Данные снимков, OOF-растры, окружения, промежуточные
optimizer states и общие публичные pretrained-веса не архивируются: команды
получения и повторения описаны в [runbook](../../research/SERVICE_RUNBOOK.md).

[Результаты](../../research/SERVICE_ITERATION_RESULTS.md),
[выбранный кандидат](../../research/SERVICE_CANDIDATE.json).
Лучший development OOF: 0,786124 при p=0,5; 0,788729 после OOF-fitted калибровки/порога.
Схема: бустинг при доступной оптике, TerraMind при отсутствующей. Kuro не вошёл в неё.
