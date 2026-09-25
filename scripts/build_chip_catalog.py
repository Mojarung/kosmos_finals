"""Build data/catalog/sen1floods11_handlabeled.json: every official hand-labeled chip.

Inputs: ml/artifacts/split.json (the committed split) and the official
Sen1Floods11_Metadata.geojson downloaded by `uv run --project ml floodvalue download`.
The output is small and committed, so the interface works offline.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPLIT = ROOT / "ml" / "artifacts" / "split.json"
METADATA = ROOT / "ml" / "data" / "sen1floods11" / "Sen1Floods11_Metadata.geojson"
OUTPUT = ROOT / "data" / "catalog" / "sen1floods11_handlabeled.json"
LABELS = {
    "Bolivia": "Боливия", "Ghana": "Гана", "India": "Индия", "Mekong": "Меконг · Камбоджа",
    "Nigeria": "Нигерия", "Pakistan": "Пакистан", "Paraguay": "Парагвай", "Somalia": "Сомали",
    "Spain": "Испания", "Sri-Lanka": "Шри-Ланка", "USA": "США",
}
LOCATIONS = {"Mekong": "Cambodia"}


def event_key(chip_id: str) -> str:
    return chip_id.rsplit("_", 1)[0]


def main() -> None:
    split = json.loads(SPLIT.read_text(encoding="utf-8"))
    metadata = json.loads(METADATA.read_text(encoding="utf-8"))
    dates = {feature["properties"]["location"]: feature["properties"]["s1_date"].replace("/", "-")
             for feature in metadata["features"]}
    chips = []
    for part, names in split["parts"].items():
        for chip_id in names:
            key = event_key(chip_id)
            chips.append({"chip_id": chip_id, "event_key": key, "event": LABELS[key],
                          "observed_at": dates[LOCATIONS.get(key, key)], "split_part": part})
    chips.sort(key=lambda chip: (chip["event_key"], chip["chip_id"]))
    counts = Counter(chip["event_key"] for chip in chips)
    events = [{"key": key, "label": LABELS[key], "location": LOCATIONS.get(key, key),
               "observed_at": dates[LOCATIONS.get(key, key)], "chips": counts[key]} for key in sorted(counts)]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "source": "https://github.com/cloudtostreet/Sen1Floods11",
        "version": "v1.1",
        "bucket_prefix": "gs://sen1floods11/v1.1/data/flood_events/HandLabeled",
        "split_source": "ml/artifacts/split.json",
        "date_source": "v1.1/Sen1Floods11_Metadata.geojson s1_date per event",
        "events": events, "chips": chips,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(chips)} chips in {len(events)} events -> {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
