"""Train one U-Net member of the main method on the stored split.

The whole training part lives on the GPU; each step samples random 256 px crops
with flips and 90 degree rotations. Loss and metrics ignore pixels without a valid
label or valid S1. The checkpoint with the best pooled validation IoU at p = 0.5 is kept.
"""

from __future__ import annotations

import json
import math
import random
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
import segmentation_models_pytorch as smp
import torch
import torch.nn.functional as F
from safetensors.torch import save_file

from floodvalue.dataset import TensorPart, load_raw, to_tensors
from floodvalue.features import CHANNEL_SETS, fit_norm_stats
from floodvalue.metrics import Confusion

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
AMP_DTYPE = torch.bfloat16
EVAL_BATCH = 8


@dataclass(frozen=True)
class TrainConfig:
    name: str
    channel_set: str = "vv_vh"
    encoder: str = "resnet34"
    encoder_weights: str | None = "imagenet"
    epochs: int = 60
    batch_size: int = 16
    crop: int = 256
    crops_per_chip: int = 4
    lr: float = 5e-4
    weight_decay: float = 1e-4
    dice_weight: float = 0.5
    seed: int = 0
    eval_every: int = 2
    augment: bool = True

    @classmethod
    def from_json(cls, path: Path, **overrides) -> TrainConfig:
        data = json.loads(path.read_text(encoding="utf-8"))
        return replace(cls(**data), **{k: v for k, v in overrides.items() if v is not None})


def build_model(config: TrainConfig, pretrained: bool = True) -> torch.nn.Module:
    return smp.Unet(
        encoder_name=config.encoder,
        encoder_weights=config.encoder_weights if pretrained else None,
        in_channels=len(CHANNEL_SETS[config.channel_set]),
        classes=1,
    )


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def masked_loss(logits: torch.Tensor, target: torch.Tensor, valid: torch.Tensor, dice_weight: float) -> torch.Tensor:
    logits, weight = logits.float(), valid.float()
    bce = F.binary_cross_entropy_with_logits(logits, target, reduction="none")
    bce = (bce * weight).sum() / weight.sum().clamp_min(1.0)
    prob = torch.sigmoid(logits) * weight
    intersection = (prob * target).sum()
    dice = 1.0 - (2.0 * intersection + 1.0) / (prob.sum() + (target * weight).sum() + 1.0)
    return bce + dice_weight * dice


def sample_batch(part: dict[str, torch.Tensor], index: torch.Tensor, crop: int, gen: torch.Generator,
                 augment: bool = True):
    """Random crops with a random dihedral transform per sample, all on the GPU."""
    size = part["x"].shape[-1]
    xs, ts, vs = [], [], []
    for i in index.tolist():
        top, left = torch.randint(0, size - crop + 1, (2,), generator=gen).tolist()
        k, flip = torch.randint(0, 4, (1,), generator=gen).item(), torch.randint(0, 2, (1,), generator=gen).item()
        window = (slice(top, top + crop), slice(left, left + crop))
        tiles = [part["x"][i][(slice(None), *window)], part["target"][i][window], part["valid"][i][window]]
        if augment:
            tiles = [torch.rot90(t, k, dims=(-2, -1)) for t in tiles]
        if augment and flip:
            tiles = [torch.flip(t, dims=(-1,)) for t in tiles]
        xs.append(tiles[0]), ts.append(tiles[1]), vs.append(tiles[2])
    return torch.stack(xs), torch.stack(ts), torch.stack(vs)


@torch.inference_mode()
def predict_logits(model: torch.nn.Module, x: np.ndarray | torch.Tensor) -> torch.Tensor:
    """Logits [N, H, W] on the device for inputs [N, C, H, W]."""
    model.eval()
    x = torch.as_tensor(x, device=DEVICE)
    outputs = []
    for start in range(0, x.shape[0], EVAL_BATCH):
        with torch.autocast(device_type="cuda", dtype=AMP_DTYPE, enabled=DEVICE == "cuda"):
            outputs.append(model(x[start : start + EVAL_BATCH]).float()[:, 0])
    return torch.cat(outputs)


def pooled_confusion(logits: torch.Tensor, target: torch.Tensor, valid: torch.Tensor) -> Confusion:
    pred, truth = (logits > 0) & valid, (target > 0.5) & valid
    tp = int((pred & truth).sum())
    fp = int((pred & ~truth).sum())
    fn = int((~pred & truth).sum())
    return Confusion(tp, fp, fn, int(valid.sum()) - tp - fp - fn)


def _to_device(part: TensorPart) -> dict[str, torch.Tensor]:
    return {
        "x": torch.as_tensor(part.x, device=DEVICE),
        "target": torch.as_tensor(part.target, device=DEVICE),
        "valid": torch.as_tensor(part.valid, device=DEVICE),
    }


def fit(config: TrainConfig, train_part: TensorPart, valid_part: TensorPart, run_dir: Path) -> dict:
    seed_everything(config.seed)
    model = build_model(config).to(DEVICE)
    train_gpu, valid_gpu = _to_device(train_part), _to_device(valid_part)
    n_train = train_gpu["x"].shape[0]
    steps_per_epoch = math.ceil(n_train * config.crops_per_chip / config.batch_size)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.lr, weight_decay=config.weight_decay)
    scheduler = torch.optim.lr_scheduler.OneCycleLR(
        optimizer, max_lr=config.lr, total_steps=config.epochs * steps_per_epoch, pct_start=0.05
    )
    gen = torch.Generator().manual_seed(config.seed)
    history, best = [], {"iou": -1.0}
    started = time.perf_counter()
    for epoch in range(1, config.epochs + 1):
        model.train()
        losses = []
        for _ in range(steps_per_epoch):
            index = torch.randint(0, n_train, (config.batch_size,), generator=gen)
            x, target, valid = sample_batch(train_gpu, index, config.crop, gen, config.augment)
            with torch.autocast(device_type="cuda", dtype=AMP_DTYPE, enabled=DEVICE == "cuda"):
                logits = model(x)[:, 0]
            loss = masked_loss(logits, target, valid, config.dice_weight)
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
            scheduler.step()
            losses.append(loss.item())
        record = {"epoch": epoch, "loss": float(np.mean(losses)), "seconds": round(time.perf_counter() - started, 1)}
        if epoch % config.eval_every == 0 or epoch == config.epochs:
            conf = pooled_confusion(predict_logits(model, valid_gpu["x"]), valid_gpu["target"], valid_gpu["valid"])
            record["valid_iou"] = conf.iou
            if (conf.iou or 0.0) > best["iou"]:
                best = {"iou": conf.iou, "epoch": epoch, "valid": conf.as_dict()}
                save_file({k: v.detach().cpu().contiguous() for k, v in model.state_dict().items()},
                          str(run_dir / "model.safetensors"))
        history.append(record)
        print(json.dumps(record), flush=True)
    return {"best": best, "history": history, "steps_per_epoch": steps_per_epoch}


def run(config: TrainConfig, data_root: Path, split: dict[str, list[str]], run_dir: Path,
        train_names: list[str] | None = None, valid_names: list[str] | None = None) -> dict:
    """Train one member and write config, normalisation stats, weights and history to ``run_dir``."""
    run_dir.mkdir(parents=True, exist_ok=True)
    train_raw = load_raw(data_root, train_names or split["train"])
    valid_raw = load_raw(data_root, valid_names or split["valid"])
    stats = fit_norm_stats(train_raw.s1, config.channel_set)
    (run_dir / "config.json").write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")
    (run_dir / "norm_stats.json").write_text(json.dumps(stats.as_dict(), indent=2), encoding="utf-8")
    result = fit(config, to_tensors(train_raw, stats), to_tensors(valid_raw, stats), run_dir)
    summary = {
        "config": asdict(config),
        "train_chips": list(train_raw.names),
        "valid_chips": list(valid_raw.names),
        "device": torch.cuda.get_device_name(0) if DEVICE == "cuda" else "cpu",
        "torch": torch.__version__,
        **result,
    }
    (run_dir / "train_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
