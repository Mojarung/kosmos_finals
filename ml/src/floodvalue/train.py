"""Train one U-Net member of the main method on the stored split.

The whole training part lives on the GPU; each step samples random 256 px crops
with flips and 90 degree rotations. Loss and metrics ignore pixels without a valid
label or valid S1. Checkpoint selection defaults to validation IoU at p = 0.5;
experimental configs can select the threshold jointly and compare raw/EMA weights.
"""

from __future__ import annotations

import json
import hashlib
import math
import os
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

DEVICE = os.environ.get("FLOODVALUE_DEVICE", "cuda" if torch.cuda.is_available() else "cpu")
AMP_DTYPE = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float16
EVAL_BATCH = int(os.environ.get("FLOODVALUE_EVAL_BATCH", "8"))


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
    sampler: str = "uniform"
    loss: str = "bce_dice"
    large_crop: int = 0
    large_crop_epoch: int = 0
    large_batch_size: int = 8
    freeze_bn: bool = False
    architecture: str = "unet"
    decoder_attention: str | None = None
    checkpoint_metric: str = "p05"
    encoder_lr_scale: float = 1.0
    ema_decay: float = 0.0
    grad_clip: float = 0.0
    encoder_checkpoint: str | None = None
    encoder_checkpoint_sha256: str | None = None
    normalization: str = "train"
    resume_training: bool = False

    def __post_init__(self):
        if self.architecture not in {"unet", "unetplusplus", "segformer", "deeplabv3plus", "fpn"}:
            raise ValueError("unsupported architecture")
        if self.checkpoint_metric not in {"p05", "best_threshold"}:
            raise ValueError("unsupported checkpoint metric")
        if not 0 < self.encoder_lr_scale <= 1 or not 0 <= self.ema_decay < 1 or self.grad_clip < 0:
            raise ValueError("invalid optimizer/EMA setting")
        if bool(self.encoder_checkpoint) != bool(self.encoder_checkpoint_sha256):
            raise ValueError("external encoder initialization requires a checksum")
        if self.normalization not in {"train", "ssl4eo_s1"}:
            raise ValueError("unknown normalization")
        if self.normalization == "ssl4eo_s1" and self.channel_set != "vv_vh":
            raise ValueError("SSL4EO-S1 normalization requires VV/VH")
        if self.sampler not in {"uniform", "balanced"}:
            raise ValueError(f"unknown sampler: {self.sampler}")
        if self.loss not in {"bce_dice", "bce_lovasz"}:
            raise ValueError(f"unknown loss: {self.loss}")
        if self.epochs < 1 or self.batch_size < 1 or self.crops_per_chip < 1 or self.eval_every < 1:
            raise ValueError("epochs, batches, crops_per_chip and eval_every must be positive")
        if self.crop < 32 or self.crop > 512 or self.crop % 32:
            raise ValueError("crop must be a multiple of 32 in [32, 512]")
        if self.large_crop and (self.large_crop < self.crop or self.large_crop > 512 or self.large_crop % 32
                                or not 1 <= self.large_crop_epoch <= self.epochs or self.large_batch_size < 1):
            raise ValueError("invalid large-crop stage")

    @classmethod
    def from_json(cls, path: Path, **overrides) -> TrainConfig:
        data = json.loads(path.read_text(encoding="utf-8"))
        return replace(cls(**data), **{k: v for k, v in overrides.items() if v is not None})


def build_model(config: TrainConfig, pretrained: bool = True) -> torch.nn.Module:
    kwargs = {}
    if config.architecture in {"unet", "unetplusplus"}:
        kwargs["decoder_attention_type"] = config.decoder_attention
    model = smp.create_model(
        config.architecture,
        encoder_name=config.encoder,
        encoder_weights=config.encoder_weights if pretrained and not config.encoder_checkpoint else None,
        in_channels=len(CHANNEL_SETS[config.channel_set]),
        classes=1,
        **kwargs,
    )
    if pretrained and config.encoder_checkpoint:
        from floodvalue import paths
        from safetensors.torch import load_file
        checkpoint = paths.PIPELINE_ROOT/config.encoder_checkpoint
        if hashlib.sha256(checkpoint.read_bytes()).hexdigest() != config.encoder_checkpoint_sha256:
            raise ValueError("encoder initialization checksum mismatch")
        model.encoder.load_state_dict(load_file(str(checkpoint)))
    return model


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def masked_lovasz(logits: torch.Tensor, target: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
    """Binary Lovasz hinge, pooled over valid pixels; nodata has zero gradient."""
    scores, labels = logits[valid].float(), target[valid].float()
    if scores.numel() == 0:
        return logits.sum() * 0.0
    errors = 1.0 - scores * (2.0 * labels - 1.0)
    errors, order = torch.sort(errors, descending=True)
    labels = labels[order]
    positives = labels.sum()
    intersection = positives - labels.cumsum(0)
    union = positives + (1.0 - labels).cumsum(0)
    jaccard = 1.0 - intersection / union
    gradient = torch.cat((jaccard[:1], jaccard[1:] - jaccard[:-1]))
    return torch.dot(F.relu(errors), gradient)


def masked_loss(logits: torch.Tensor, target: torch.Tensor, valid: torch.Tensor, dice_weight: float,
                loss: str = "bce_dice") -> torch.Tensor:
    logits, weight = logits.float(), valid.float()
    bce = F.binary_cross_entropy_with_logits(logits, target, reduction="none")
    bce = (bce * weight).sum() / weight.sum().clamp_min(1.0)
    if loss == "bce_lovasz":
        return bce + dice_weight * masked_lovasz(logits, target, valid)
    prob = torch.sigmoid(logits) * weight
    intersection = (prob * target).sum()
    dice = 1.0 - (2.0 * intersection + 1.0) / (prob.sum() + (target * weight).sum() + 1.0)
    return bce + dice_weight * dice


def crop_pools(part: TensorPart, crop: int, stride: int = 32) -> list[np.ndarray]:
    """Training-only candidates stratified by valid water share, including dry crops.

    Five bins: zero, (0, .01], (.01, .1], (.1, .5], (.5, 1].
    At least half the window must be labelled and have finite S1.
    """
    pools: list[list[tuple[int, int, int]]] = [[] for _ in range(5)]
    h, w = part.valid.shape[-2:]
    if crop > min(h, w):
        raise ValueError("crop exceeds training image")
    ys = sorted(set(range(0, h - crop + 1, stride)) | {h - crop})
    xs = sorted(set(range(0, w - crop + 1, stride)) | {w - crop})
    for i in range(len(part.names)):
        integrals = [np.pad(a.astype(np.int64), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
                     for a in (part.valid[i], (part.target[i] > .5) & part.valid[i])]
        for y in ys:
            for x in xs:
                n, water = [a[y+crop, x+crop] - a[y, x+crop] - a[y+crop, x] + a[y, x]
                            for a in integrals]
                if n < crop * crop / 2:
                    continue
                b = 0 if water == 0 else 1 + int(np.searchsorted([.01, .1, .5], water/n, side="left"))
                pools[b].append((i, y, x))
    return [np.asarray(pool, dtype=np.int64).reshape(-1, 3) for pool in pools]


def sample_batch(part: dict[str, torch.Tensor], index: torch.Tensor, crop: int, gen: torch.Generator,
                 augment: bool = True, pools: list[np.ndarray] | None = None):
    """Random crops with a random dihedral transform per sample, all on the GPU."""
    size = part["x"].shape[-1]
    xs, ts, vs = [], [], []
    nonempty = [p for p in (pools or []) if len(p)]
    for i in index.tolist():
        top, left = torch.randint(0, size - crop + 1, (2,), generator=gen).tolist()
        if nonempty and torch.rand((), generator=gen).item() < .5:
            pool = nonempty[torch.randint(len(nonempty), (), generator=gen).item()]
            i, top, left = pool[torch.randint(len(pool), (), generator=gen).item()].tolist()
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


def checkpoint_confusion(logits, target, valid, metric):
    """Select with the same pooled IoU objective used by the final calibrator.

    Threshold search uses validation only. It is especially important when a
    new loss changes the scale/offset of logits. No test data is accepted here.
    """
    if metric == "p05":
        return pooled_confusion(logits, target, valid), .5
    from floodvalue.calibration import best_prob_threshold
    probability = logits.sigmoid()[valid].detach().cpu().numpy()
    truth = target[valid].detach().cpu().numpy() > .5
    threshold, conf = best_prob_threshold(probability, truth)
    return conf, threshold


def fit(config: TrainConfig, train_part: TensorPart, valid_part: TensorPart, run_dir: Path,
        initial_model: torch.nn.Module | None = None) -> dict:
    seed_everything(config.seed)
    model = (build_model(config) if initial_model is None else initial_model).to(DEVICE)
    train_gpu, valid_gpu = _to_device(train_part), _to_device(valid_part)
    n_train = train_gpu["x"].shape[0]
    def epoch_batch(epoch):
        large = config.large_crop and epoch >= config.large_crop_epoch
        return (config.large_crop, config.large_batch_size) if large else (config.crop, config.batch_size)

    step_counts = [math.ceil(n_train * config.crops_per_chip / epoch_batch(e)[1])
                   for e in range(1, config.epochs + 1)]
    pools = {crop: crop_pools(train_part, crop) for crop in {config.crop, config.large_crop} if crop}
    parameters, max_lr = model.parameters(), config.lr
    if config.encoder_lr_scale != 1:
        encoder = list(model.encoder.parameters())
        ids = {id(p) for p in encoder}
        parameters = [{"params": encoder, "lr": config.lr*config.encoder_lr_scale},
                      {"params": [p for p in model.parameters() if id(p) not in ids], "lr": config.lr}]
        max_lr = [config.lr*config.encoder_lr_scale, config.lr]
    optimizer = torch.optim.AdamW(parameters, lr=config.lr, weight_decay=config.weight_decay)
    scheduler = torch.optim.lr_scheduler.OneCycleLR(
        optimizer, max_lr=max_lr, total_steps=sum(step_counts), pct_start=0.05
    )
    ema = None
    if config.ema_decay:
        from torch.optim.swa_utils import AveragedModel, get_ema_multi_avg_fn
        ema = AveragedModel(model, device=DEVICE,
                            multi_avg_fn=get_ema_multi_avg_fn(config.ema_decay), use_buffers=True)
    scaler = torch.amp.GradScaler("cuda", enabled=DEVICE == "cuda" and AMP_DTYPE == torch.float16)
    gen = torch.Generator().manual_seed(config.seed)
    history, best = [], {"iou": -1.0}
    best_state = None
    resume_path = run_dir / "training_state.pt"
    signature = {"config": asdict(config), "train": list(train_part.names), "valid": list(valid_part.names)}
    start_epoch, elapsed = 1, 0.
    started = time.perf_counter()
    if config.resume_training and resume_path.exists():
        state = torch.load(resume_path, map_location="cpu", weights_only=True)
        if state["signature"] != signature:
            raise ValueError("training resume configuration or split differs")
        model.load_state_dict(state["model"])
        optimizer.load_state_dict(state["optimizer"])
        scheduler.load_state_dict(state["scheduler"])
        scaler.load_state_dict(state["scaler"])
        if ema is not None:
            ema.load_state_dict(state["ema"])
        history, best, best_state = state["history"], state["best"], state["best_state"]
        if best_state is not None:
            save_file(best_state, str(run_dir / "model.safetensors"))
        start_epoch, elapsed = state["epoch"]+1, state["elapsed"]
        gen.set_state(state["crop_rng"])
        torch.set_rng_state(state["torch_rng"])
        random.setstate(state["python_rng"])
        n = state["numpy_rng"]
        np.random.set_state((n[0], n[1].numpy(), n[2], n[3], n[4]))
        if DEVICE == "mps":
            torch.mps.set_rng_state(state["device_rng"])
        elif DEVICE == "cuda":
            torch.cuda.set_rng_state_all(state["device_rng"])
        del state
        print(json.dumps({"resumed_after_epoch": start_epoch-1, "best_iou": best["iou"]}), flush=True)
    elif initial_model is not None:
        conf, threshold = checkpoint_confusion(predict_logits(model, valid_gpu["x"]), valid_gpu["target"],
                                               valid_gpu["valid"], config.checkpoint_metric)
        best = {"iou": conf.iou or 0., "epoch": 0, "valid": conf.as_dict(),
                "variant": "raw", "raw_threshold": threshold}
        best_state = {k: v.detach().cpu().contiguous().clone() for k, v in model.state_dict().items()}
        save_file(best_state, str(run_dir / "model.safetensors"))
    for epoch in range(start_epoch, config.epochs + 1):
        model.train()
        if config.freeze_bn:
            # Preserve population statistics during warm-start adaptation; affine
            # BN weights still receive gradients along with the other weights.
            for module in model.modules():
                if isinstance(module, torch.nn.modules.batchnorm._BatchNorm):
                    module.eval()
        losses = []
        crop, batch_size = epoch_batch(epoch)
        steps_per_epoch = step_counts[epoch - 1]
        for _ in range(steps_per_epoch):
            index = torch.randint(0, n_train, (batch_size,), generator=gen)
            x, target, valid = sample_batch(train_gpu, index, crop, gen, config.augment,
                                           pools[crop] if config.sampler == "balanced" else None)
            with torch.autocast(device_type="cuda", dtype=AMP_DTYPE, enabled=DEVICE == "cuda"):
                logits = model(x)[:, 0]
            loss = masked_loss(logits, target, valid, config.dice_weight, config.loss)
            if not torch.isfinite(loss):
                raise FloatingPointError(f"non-finite training loss at epoch {epoch}")
            optimizer.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            if config.grad_clip:
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), config.grad_clip)
            scaler.step(optimizer)
            scaler.update()
            scheduler.step()
            if ema is not None:
                ema.update_parameters(model)
            losses.append(loss.item())
        record = {"epoch": epoch, "loss": float(np.mean(losses)), "crop": crop, "batch_size": batch_size,
                  "seconds": round(elapsed + time.perf_counter() - started, 1)}
        if epoch % config.eval_every == 0 or epoch == config.epochs:
            candidates = [("raw", model)] + ([("ema", ema.module)] if ema is not None else [])
            for variant, candidate in candidates:
                z = predict_logits(candidate, valid_gpu["x"])
                conf, threshold = checkpoint_confusion(z, valid_gpu["target"], valid_gpu["valid"], config.checkpoint_metric)
                record["valid_iou" if variant == "raw" else "ema_valid_iou"] = conf.iou
                record[variant+"_threshold"] = threshold
                if (conf.iou or 0.0) > best["iou"]:
                    best = {"iou": conf.iou, "epoch": epoch, "valid": conf.as_dict(),
                            "variant": variant, "raw_threshold": threshold}
                    best_state = {k: v.detach().cpu().contiguous().clone() for k, v in candidate.state_dict().items()}
                    save_file(best_state, str(run_dir / "model.safetensors"))
        history.append(record)
        (run_dir / "progress.json").write_text(json.dumps({"best": best, "history": history}, indent=2))
        if config.resume_training:
            n = np.random.get_state()
            state = {"signature": signature, "epoch": epoch, "elapsed": record["seconds"],
                     "model": model.state_dict(), "optimizer": optimizer.state_dict(),
                     "scheduler": scheduler.state_dict(), "scaler": scaler.state_dict(),
                     "ema": ema.state_dict() if ema is not None else None,
                     "history": history, "best": best, "best_state": best_state,
                     "crop_rng": gen.get_state(), "torch_rng": torch.get_rng_state(),
                     "python_rng": random.getstate(),
                     "numpy_rng": (n[0], torch.from_numpy(n[1].copy()), n[2], n[3], n[4]),
                     "device_rng": (torch.mps.get_rng_state() if DEVICE == "mps" else
                                    torch.cuda.get_rng_state_all() if DEVICE == "cuda" else None)}
            temporary = resume_path.with_suffix(".tmp")
            torch.save(state, temporary)
            temporary.replace(resume_path)
            del state
        print(json.dumps(record), flush=True)
    return {"best": best, "history": history, "steps_per_epoch": step_counts,
            "crop_pool_counts": {str(c): [len(p) for p in ps] for c, ps in pools.items()}}


def run(config: TrainConfig, data_root: Path, split: dict[str, list[str]], run_dir: Path,
        train_names: list[str] | None = None, valid_names: list[str] | None = None) -> dict:
    """Train one member and write config, normalisation stats, weights and history to ``run_dir``."""
    run_dir.mkdir(parents=True, exist_ok=True)
    train_raw = load_raw(data_root, train_names or split["train"])
    valid_raw = load_raw(data_root, valid_names or split["valid"])
    stats = fit_norm_stats(train_raw.s1, config.channel_set)
    if config.normalization == "ssl4eo_s1":
        # Published TorchGeo/SSL4EO-S12 SAR statistics, not fitted on evaluation data.
        stats = replace(stats, mean=(-12.59, -20.26), std=(5.26, 5.91))
    (run_dir / "config.json").write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")
    (run_dir / "norm_stats.json").write_text(json.dumps(stats.as_dict(), indent=2), encoding="utf-8")
    result = fit(config, to_tensors(train_raw, stats), to_tensors(valid_raw, stats), run_dir)
    summary = {
        "config": asdict(config),
        "train_chips": list(train_raw.names),
        "valid_chips": list(valid_raw.names),
        "device": torch.cuda.get_device_name(0) if DEVICE == "cuda" else DEVICE,
        "torch": torch.__version__,
        **result,
    }
    (run_dir / "train_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
