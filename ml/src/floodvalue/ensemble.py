"""Load trained members and turn S1 chips into ensemble logits, probabilities and spread."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from safetensors.torch import load_file
from scipy.special import expit

from floodvalue.calibration import Platt
from floodvalue.features import NormStats, normalize
from floodvalue.train import DEVICE, TrainConfig, build_model, predict_logits

MEMBER_FILES = ("config.json", "norm_stats.json", "model.safetensors")


@dataclass(frozen=True)
class Member:
    source: Path
    config: TrainConfig
    stats: NormStats
    model: torch.nn.Module


@dataclass(frozen=True)
class EnsembleOutput:
    """Arrays [N, H, W]: mean logit, calibrated probability, std of member probabilities."""

    mean_logit: np.ndarray
    prob: np.ndarray
    member_std: np.ndarray
    member_logits: np.ndarray  # [M, N, H, W]


def load_member(member_dir: Path) -> Member:
    config = TrainConfig(**json.loads((member_dir / "config.json").read_text(encoding="utf-8")))
    stats = NormStats.from_dict(json.loads((member_dir / "norm_stats.json").read_text(encoding="utf-8")))
    model = build_model(config, pretrained=False)
    model.load_state_dict({k: v.float() for k, v in load_file(str(member_dir / "model.safetensors")).items()})
    return Member(member_dir, config, stats, model.to(DEVICE).eval())


def load_members(member_dirs: Sequence[Path]) -> list[Member]:
    return [load_member(path) for path in member_dirs]


def member_logits(member: Member, s1_chips: Sequence[np.ndarray]) -> np.ndarray:
    x = np.stack([normalize(s1, member.stats)[0] for s1 in s1_chips])
    return predict_logits(member.model, x).cpu().numpy()


def run_ensemble(members: Sequence[Member], s1_chips: Sequence[np.ndarray], platt: Platt | None) -> EnsembleOutput:
    logits = np.stack([member_logits(member, s1_chips) for member in members])
    mean_logit = logits.mean(axis=0)
    prob = platt.apply(mean_logit) if platt else expit(mean_logit).astype(np.float32)
    return EnsembleOutput(mean_logit, prob, expit(logits).std(axis=0).astype(np.float32), logits)
