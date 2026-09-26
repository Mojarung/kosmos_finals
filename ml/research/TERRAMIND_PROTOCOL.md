# TerraMind tiny: локальный development CV

После проверки выполнения на MPS, до первого обучения/OOF. Общие pretrained-веса
IBM ESA TerraMind 1.0 tiny, HF revision `2b5ac0a3ed7dd7e922ccfd595b56607f342df343`,
SHA256 `e56ea9ebcd4451078b9ca4893d5cd8a89bbee376ae16829c3e7fbbbc76de0eba`.
Это не готовая Sen1Floods11-модель. Пространственные пересечения self-supervised
TerraMesh с benchmark здесь не проверены; это отдельное ограничение pretrained.

- Те же 332 development-чипа и пять фолдов `fresh/folds.json`, как в L2A boosting.
- Только S1 GRD и скачанные L2A; без DEM, JRC, WorldCover и прежнего радара.
  Нормировка по опубликованным средним/std TerraMind для нужных модальностей.
  Недоступные пиксели L2A зануляются после нормировки, отдельная маска доступности
  поступает в decoder; случайное отключение S2 с вероятностью 0,3 при обучении.
- Backbone tiny (6 022 464 параметра) полностью разморожен. Decoder: конкатенация
  четырёх уровней [2,5,8,11], FCN + локальная ветвь входных каналов для границ.
  GroupNorm, без BatchNorm; это наш лёгкий decoder, не точная реплика чужой статьи.
- 25 фиксированных эпох, 2 случайных crop224 на чип/эпоху, batch4, D4 augmentation,
  AdamW backbone2e-5/head2e-4, cosine decay, BCE+Dice. Seed2026+fold.
  Берётся последняя эпоха, внешние метки не выбирают checkpoint. Resume каждую эпоху.
- OOF inference по полному 512×512, TTA=1. Пooled IoU при0,5 и raw Brier — основные
  неизменяемые показатели; дополнительно Platt и порог по всем OOF, как у контекста,
  с пометкой о подгонке на development. Test не запускается, сервис не меняется.
