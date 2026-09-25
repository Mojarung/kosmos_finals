"""Summarise training experiments and package the chosen members as the shipped model."""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from safetensors.torch import load_file, save_file

from floodvalue.infer import sha256


def summarize_experiments(runs_dir: Path, configs: Sequence[str], out_path: Path) -> dict:
    """Best validation IoU (p = 0.5, single member) per config and seed; selection uses validation only."""
    table: dict[str, dict] = {}
    for config in configs:
        seeds = {}
        for summary_path in sorted((runs_dir / config).glob("seed*/train_summary.json")):
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            seeds[summary_path.parent.name] = {
                "valid_iou": summary["best"]["iou"],
                "best_epoch": summary["best"]["epoch"],
                "valid": summary["best"]["valid"],
            }
        ious = [s["valid_iou"] for s in seeds.values()]
        table[config] = {
            "seeds": seeds,
            "valid_iou_mean": float(np.mean(ious)) if ious else None,
            "valid_iou_std": float(np.std(ious)) if len(ious) > 1 else None,
        }
    ranked = sorted((c for c in table if table[c]["valid_iou_mean"] is not None),
                    key=lambda c: table[c]["valid_iou_mean"], reverse=True)
    result = {"metric": "pooled validation IoU at p=0.5, best checkpoint", "ranking": ranked, "configs": table}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def package_members(run_dirs: Sequence[Path], model_dir: Path) -> dict:
    """Copy config and stats, store weights as float16 safetensors, and record checksums."""
    members_dir = model_dir / "members"
    if members_dir.exists():
        shutil.rmtree(members_dir)
    card = {"format": "safetensors float16, loaded as float32", "members": []}
    for index, run_dir in enumerate(run_dirs):
        target = members_dir / f"m{index}"
        target.mkdir(parents=True)
        for name in ("config.json", "norm_stats.json", "train_summary.json"):
            shutil.copy2(run_dir / name, target / name)
        weights = {k: v.half() if v.is_floating_point() else v for k, v in load_file(str(run_dir / "model.safetensors")).items()}
        save_file(weights, str(target / "model.safetensors"))
        card["members"].append({
            "dir": f"members/m{index}",
            "source_run": run_dir.as_posix(),
            "sha256": sha256(target / "model.safetensors"),
            "bytes": (target / "model.safetensors").stat().st_size,
        })
    (model_dir / "model_card.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    return card


def member_dirs(model_dir: Path) -> list[Path]:
    return sorted(p for p in (model_dir / "members").iterdir() if (p / "model.safetensors").exists())
