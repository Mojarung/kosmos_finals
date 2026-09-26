"""Check the Sentinel-1 history rule for permanent water against JRC on the demo chips.

Usage (needs torch and network, e.g. inside the backend container):
    python scripts/check_s1_permanent.py [data/demo]

For each demo chip the rule of app/permanent.py runs on Sentinel-1 RTC passes from
the year before the event, on the chip's own 512x512 grid, and is compared with
the JRCWaterHand layer the service subtracts. Writes permanent_check.json next to
the scenes; the Quality tab shows the result.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.permanent import RULE, history_permanent  # noqa: E402


def compare(predicted: np.ndarray, reference: np.ndarray) -> dict:
    valid = (predicted != 255) & (reference != 255)
    p, r = predicted[valid] == 1, reference[valid] == 1
    tp, fp, fn = int((p & r).sum()), int((p & ~r).sum()), int((~p & r).sum())
    return {"tp": tp, "fp": fp, "fn": fn, "valid_pixels": int(valid.sum()),
            "iou": round(tp / (tp + fp + fn), 4) if tp + fp + fn else None,
            "precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None}


def main() -> None:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "demo"
    scenes = json.loads((folder / "scenes.json").read_text(encoding="utf-8"))["scenes"]
    rows = []
    for scene in scenes:
        chip = scene["chip_id"]
        meta = json.loads((folder / chip / "metadata.json").read_text(encoding="utf-8"))
        reference = np.frombuffer((folder / chip / "permanent_mask.bin").read_bytes(), dtype=np.uint8).reshape(512, 512)
        result = history_permanent(meta["bbox"], meta["observed_at"] + "T12:00:00Z")
        row = {"chip_id": chip, "event_at": meta["observed_at"], "items": result["items"],
               "jrc_permanent_pixels": int((reference == 1).sum()),
               "s1_permanent_pixels": int((result["mask"] == 1).sum()),
               **compare(result["mask"], reference)}
        rows.append(row)
        print(json.dumps({key: row[key] for key in row if key != "items"}, ensure_ascii=False))
    tp, fp, fn = (sum(row[key] for row in rows) for key in ("tp", "fp", "fn"))
    summary = {"rule": RULE, "reference": "JRCWaterHand, Sen1Floods11 v1.1", "chips": rows,
               "pooled": {"iou": round(tp / (tp + fp + fn), 4) if tp + fp + fn else None,
                          "precision": round(tp / (tp + fp), 4) if tp + fp else None,
                          "recall": round(tp / (tp + fn), 4) if tp + fn else None}}
    (folder / "permanent_check.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary["pooled"]))


if __name__ == "__main__":
    main()
