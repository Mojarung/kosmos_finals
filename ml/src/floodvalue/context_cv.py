"""Grouped 5-fold cross-fitting of the context model on the development chips.

Development = the authors' train + valid chips. Valid chips whose footprint touches a
``test_core`` chip are dropped, so no training chip touches ``test_core`` and the part
stays spatially independent. Chips that touch each other (same event) form one group,
and each group goes to one fold; every event is spread over all folds. A model trained
on four folds predicts the fifth, which gives out-of-fold (OOF) probabilities for every
development chip under the same conditions as ``test_core``: same events, other places.

All choices (inputs, network, threshold, calibration, ensemble) are made on OOF only.
The five fold models together form the final ensemble; test parts are predicted once.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
import segmentation_models_pytorch as smp
import torch
import torch.nn.functional as F
from safetensors.torch import save_file

from floodvalue import paths
from floodvalue.chips import Chip, s1_bounds
from floodvalue.context import S2_USED_INDEX, channel_index, group_channels, load_chip, load_s2, n_channels, target_of
from floodvalue.split import boxes_touch
from floodvalue.train import AMP_DTYPE, DEVICE, masked_lovasz, seed_everything

FOLDS_PATH = paths.PIPELINE_ROOT / "research" / "fresh" / "folds.json"
RUNS = paths.RUNS_DIR / "context"
N_FOLDS = 5


@dataclass(frozen=True)
class ContextConfig:
    name: str
    groups: tuple[str, ...] = ("s1", "pre", "terrain", "jrc", "worldcover")
    architecture: str = "unet"
    encoder: str = "resnet34"
    encoder_weights: str | None = "imagenet"
    epochs: int = 40
    crop: int = 320
    batch_size: int = 12
    crops_per_chip: int = 4
    lr: float = 5e-4
    weight_decay: float = 1e-4
    lovasz_weight: float = 0.5
    group_dropout: float = 0.0
    s2_dropout: float = 0.0
    seed: int = 0
    tta: int = 8

    @classmethod
    def from_json(cls, path: Path) -> ContextConfig:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["groups"] = tuple(data["groups"])
        return cls(**data)


# ------------------------------------------------------------------ folds

def make_folds() -> dict:
    split = json.loads((paths.ARTIFACTS_DIR / "split.json").read_text(encoding="utf-8"))["parts"]
    root = paths.SEN1FLOODS11_DIR
    names = split["train"] + split["valid"]
    bounds = {n: s1_bounds(root, Chip.from_name(n)) for n in names + split["test_core"]}
    dropped = sorted(n for n in split["valid"]
                     if any(Chip.from_name(t).event == Chip.from_name(n).event and boxes_touch(bounds[n], bounds[t])
                            for t in split["test_core"]))
    dev = sorted(n for n in names if n not in dropped)
    # Connected components of touching chips inside each event.
    parent = {n: n for n in dev}

    def find(n: str) -> str:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for i, a in enumerate(dev):
        for b in dev[i + 1:]:
            if Chip.from_name(a).event == Chip.from_name(b).event and boxes_touch(bounds[a], bounds[b]):
                parent[find(a)] = find(b)
    groups: dict[str, list[str]] = {}
    for n in dev:
        groups.setdefault(find(n), []).append(n)
    by_event: dict[str, list[list[str]]] = {}
    for members in groups.values():
        by_event.setdefault(Chip.from_name(members[0]).event, []).append(sorted(members))
    fold_of: dict[str, int] = {}
    totals = [0] * N_FOLDS
    for event in sorted(by_event):
        per_event = [0] * N_FOLDS
        for members in sorted(by_event[event], key=lambda m: (-len(m), m[0])):
            k = min(range(N_FOLDS), key=lambda f: (per_event[f], totals[f], f))
            for n in members:
                fold_of[n] = k
            per_event[k] += len(members)
            totals[k] += len(members)
    plan = {
        "rule": "train+valid minus valid chips touching test_core; touching chips of one event form a group; "
                "groups of each event go, largest first, to the fold with fewest chips of that event "
                "(ties: fewest chips overall, then lowest index). No randomness.",
        "dropped_touching_test_core": dropped,
        "n_groups": len(groups),
        "fold_sizes": totals,
        "folds": {n: fold_of[n] for n in dev},
    }
    FOLDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    FOLDS_PATH.write_text(json.dumps(plan, indent=1), encoding="utf-8")
    return plan


def read_folds() -> dict[str, int]:
    return json.loads(FOLDS_PATH.read_text(encoding="utf-8"))["folds"]


# ------------------------------------------------------------------ model

def build_model(config: ContextConfig, pretrained: bool = True) -> torch.nn.Module:
    return smp.create_model(config.architecture, encoder_name=config.encoder,
                            encoder_weights=config.encoder_weights if pretrained else None,
                            in_channels=n_channels(config.groups), classes=1)


def masked_loss(logits: torch.Tensor, target: torch.Tensor, valid: torch.Tensor, lovasz_weight: float) -> torch.Tensor:
    logits = logits.float()
    weight = valid.float()
    bce = F.binary_cross_entropy_with_logits(logits, target, reduction="none")
    bce = (bce * weight).sum() / weight.sum().clamp_min(1.0)
    if not lovasz_weight:
        return bce
    return bce + lovasz_weight * masked_lovasz(logits, target, valid)


def load_part(names: list[str], config: ContextConfig, device: str = DEVICE) -> dict[str, torch.Tensor]:
    """Stacked chips; training keeps them in pinned host memory and ships only batches to the GPU."""
    idx = channel_index(config.groups)
    xs, ts, vs = [], [], []
    for n in names:
        stack = load_chip(n)
        x = stack.x[idx]
        if "s2" in config.groups:
            x = np.concatenate([x, load_s2(n)[S2_USED_INDEX]])
        xs.append(torch.from_numpy(x))
        ts.append(torch.from_numpy(target_of(stack)).to(torch.uint8))
        vs.append(torch.from_numpy(stack.valid))
    part = {"x": torch.stack(xs), "target": torch.stack(ts), "valid": torch.stack(vs)}
    if device == "cpu":
        return {k: v.pin_memory() if torch.cuda.is_available() else v for k, v in part.items()}
    return {k: v.to(device) for k, v in part.items()}


def group_slices(config: ContextConfig) -> dict[str, list[int]]:
    """Positions of each non-S1 group inside the model input, for group dropout."""
    order = [c for g in config.groups if g != "s2" for c in group_channels(g)]
    order += [c for g in config.groups if g == "s2" for c in group_channels(g)]
    return {g: [order.index(c) for c in group_channels(g)] for g in config.groups if g != "s1"}


class BatchStream:
    """Random crops cut from host memory by a background thread; rotations run on the GPU.

    The random draws keep the order of the in-GPU sampler: per sample the crop corner,
    the rotation, the flip, then one draw per group for group dropout.
    """

    def __init__(self, part: dict[str, torch.Tensor], config: ContextConfig, gen: torch.Generator,
                 drops: dict, steps: int, depth: int = 4):
        import queue
        import threading

        self.part, self.config, self.gen, self.drops = part, config, gen, drops
        self.queue: queue.Queue = queue.Queue(maxsize=depth)
        self.thread = threading.Thread(target=self._run, args=(steps,), daemon=True)
        self.thread.start()

    def _draw(self):
        config, part = self.config, self.part
        n, size, c = part["x"].shape[0], part["x"].shape[-1], config.crop
        index = torch.randint(0, n, (config.batch_size,), generator=self.gen).tolist()
        xs, ts, vs, params = [], [], [], []
        for i in index:
            top, left = torch.randint(0, size - c + 1, (2,), generator=self.gen).tolist()
            k = torch.randint(0, 4, (1,), generator=self.gen).item()
            flip = torch.randint(0, 2, (1,), generator=self.gen).item()
            dropped = []
            for group, positions in self.drops.items():
                rate = config.s2_dropout if group == "s2" else config.group_dropout
                if rate and torch.rand((), generator=self.gen).item() < rate:
                    dropped.extend(positions)
            xs.append(part["x"][i, :, top:top + c, left:left + c])
            ts.append(part["target"][i, top:top + c, left:left + c])
            vs.append(part["valid"][i, top:top + c, left:left + c])
            params.append((k, flip, dropped))
        batch = [torch.stack(a) for a in (xs, ts, vs)]
        if torch.cuda.is_available():
            batch = [b.pin_memory() for b in batch]
        return [b.to(DEVICE, non_blocking=True) for b in batch], params

    def _run(self, steps: int) -> None:
        for _ in range(steps):
            self.queue.put(self._draw())

    def next(self):
        (x, t, v), params = self.queue.get()
        xs, ts, vs = [], [], []
        for j, (k, flip, dropped) in enumerate(params):
            tiles = [torch.rot90(a[j], k, dims=(-2, -1)) for a in (x, t, v)]
            if flip:
                tiles = [torch.flip(a, dims=(-1,)) for a in tiles]
            xj = tiles[0].float()
            if dropped:
                xj[dropped] = 0.0
            xs.append(xj), ts.append(tiles[1].float()), vs.append(tiles[2])
        return torch.stack(xs), torch.stack(ts), torch.stack(vs)


DIHEDRAL = [(0, False), (1, False), (2, False), (3, False), (0, True), (1, True), (2, True), (3, True)]


@torch.inference_mode()
def predict(model: torch.nn.Module, x: torch.Tensor, tta: int, batch: int = 4) -> torch.Tensor:
    """Mean logit over ``tta`` dihedral transforms, [N, H, W] float32 on the device."""
    model.eval()
    out = torch.zeros(x.shape[0], x.shape[-2], x.shape[-1], device=DEVICE)
    for k, flip in DIHEDRAL[:tta]:
        for start in range(0, x.shape[0], batch):
            chunk = x[start:start + batch].float()
            chunk = torch.rot90(chunk, k, dims=(-2, -1))
            if flip:
                chunk = torch.flip(chunk, dims=(-1,))
            with torch.autocast(device_type="cuda", dtype=AMP_DTYPE, enabled=DEVICE == "cuda"):
                z = model(chunk).float()[:, 0]
            if flip:
                z = torch.flip(z, dims=(-1,))
            out[start:start + batch] += torch.rot90(z, -k, dims=(-2, -1))
    return out / tta


def train_model(config: ContextConfig, train_names: list[str], run_dir: Path) -> torch.nn.Module:
    seed_everything(config.seed)
    part = load_part(train_names, config, device="cpu")
    model = build_model(config).to(DEVICE)
    steps = config.epochs * math.ceil(len(train_names) * config.crops_per_chip / config.batch_size)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.lr, weight_decay=config.weight_decay)
    scheduler = torch.optim.lr_scheduler.OneCycleLR(optimizer, max_lr=config.lr, total_steps=steps, pct_start=0.05)
    scaler = torch.amp.GradScaler("cuda", enabled=DEVICE == "cuda" and AMP_DTYPE == torch.float16)
    gen = torch.Generator().manual_seed(config.seed)
    drops = group_slices(config) if config.group_dropout or config.s2_dropout else {}
    started, losses, history = time.perf_counter(), [], []
    per_epoch = steps // config.epochs
    model.train()
    stream = BatchStream(part, config, gen, drops, steps)
    for step in range(1, steps + 1):
        x, target, valid = stream.next()
        with torch.autocast(device_type="cuda", dtype=AMP_DTYPE, enabled=DEVICE == "cuda"):
            logits = model(x)[:, 0]
        loss = masked_loss(logits, target, valid, config.lovasz_weight)
        if not torch.isfinite(loss):
            raise FloatingPointError(f"non-finite loss at step {step}")
        optimizer.zero_grad(set_to_none=True)
        scaler.scale(loss).backward()
        scaler.step(optimizer)
        scaler.update()
        scheduler.step()
        losses.append(loss.item())
        if step % per_epoch == 0:
            history.append({"epoch": step // per_epoch, "loss": float(np.mean(losses)),
                            "seconds": round(time.perf_counter() - started, 1)})
            losses = []
    run_dir.mkdir(parents=True, exist_ok=True)
    save_file({k: v.detach().cpu().contiguous() for k, v in model.state_dict().items()},
              str(run_dir / "model.safetensors"))
    (run_dir / "history.json").write_text(json.dumps(history, indent=1), encoding="utf-8")
    del part
    torch.cuda.empty_cache()
    return model


def predict_names(model: torch.nn.Module, config: ContextConfig, names: list[str], out_dir: Path,
                  drop_s2: bool = False) -> None:
    """Logits of every chip; ``drop_s2`` zeroes the optical channels to score the radar fallback."""
    out_dir.mkdir(parents=True, exist_ok=True)
    s2_positions = group_slices(config).get("s2", [])
    for start in range(0, len(names), 16):
        chunk = names[start:start + 16]
        part = load_part(chunk, config)
        if drop_s2:
            part["x"][:, s2_positions] = 0
        logits = predict(model, part["x"], config.tta).cpu().numpy()
        for name, z in zip(chunk, logits):
            np.save(out_dir / f"{name}.npy", z.astype(np.float16))
        del part


def run_cv(config: ContextConfig, folds: list[int] | None = None) -> None:
    fold_of = read_folds()
    root = RUNS / config.name
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_text(json.dumps(asdict(config), indent=1), encoding="utf-8")
    for k in folds if folds is not None else range(N_FOLDS):
        fold_dir = root / f"fold{k}"
        held = sorted(n for n, f in fold_of.items() if f == k)
        if (fold_dir / "model.safetensors").exists() and all((root / "oof" / f"{n}.npy").exists() for n in held):
            continue
        train = sorted(n for n, f in fold_of.items() if f != k)
        t0 = time.perf_counter()
        model = train_model(config, train, fold_dir)
        predict_names(model, config, held, root / "oof")
        if "s2" in config.groups:
            predict_names(model, config, held, root / "oof_nos2", drop_s2=True)
        print(json.dumps({"config": config.name, "fold": k, "train": len(train), "held": len(held),
                          "seconds": round(time.perf_counter() - t0, 1)}), flush=True)
        del model
        torch.cuda.empty_cache()


# ------------------------------------------------------------------ evaluation

def confusion_at(prob: np.ndarray, truth: np.ndarray, t: float) -> tuple[int, int, int]:
    pred = prob >= t
    return int((pred & truth).sum()), int((pred & ~truth).sum()), int((~pred & truth).sum())


def iou(tp: int, fp: int, fn: int) -> float:
    return tp / (tp + fp + fn) if tp + fp + fn else float("nan")


def oof_arrays(name: str, chips: list[str], sub: str = "oof") -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Pooled probability, truth and event index over valid pixels of the OOF chips."""
    probs, truths, events = [], [], []
    for n in chips:
        stack = load_chip(n)
        z = np.load(RUNS / name / sub / f"{n}.npy").astype(np.float32)
        v = stack.valid
        probs.append(1 / (1 + np.exp(-z[v])))
        truths.append(target_of(stack)[v] > 0.5)
        events.append(np.full(int(v.sum()), hash(Chip.from_name(n).event) % 10_000, np.int32))
    return np.concatenate(probs), np.concatenate(truths), np.concatenate(events)


def summarize(name: str) -> dict:
    """Pooled OOF metrics; ``name:oof_nos2`` scores another prediction folder of the same run."""
    name, _, sub = name.partition(":")
    sub = sub or "oof"
    fold_of = read_folds()
    chips = sorted(fold_of)
    prob, truth, _ = oof_arrays(name, chips, sub)
    grid = np.round(np.arange(0.2, 0.81, 0.02), 2)
    scores = {float(t): iou(*confusion_at(prob, truth, t)) for t in grid}
    best_t = max(scores, key=scores.get)
    per_event: dict[str, list[int]] = {}
    for n in chips:
        stack = load_chip(n)
        z = np.load(RUNS / name / sub / f"{n}.npy").astype(np.float32)
        v = stack.valid
        c = confusion_at(1 / (1 + np.exp(-z[v])), target_of(stack)[v] > 0.5, 0.5)
        acc = per_event.setdefault(Chip.from_name(n).event, [0, 0, 0])
        for i in range(3):
            acc[i] += c[i]
    event_iou = {e: iou(*c) for e, c in sorted(per_event.items())}
    brier = float(np.mean((prob - truth) ** 2))
    result = {"name": name, "pooled_iou_p05": scores[0.5], "best_threshold": best_t, "pooled_iou_best": scores[best_t],
              "event_mean_iou_p05": float(np.nanmean(list(event_iou.values()))), "brier": brier,
              "event_iou_p05": event_iou, "pixels": int(truth.size)}
    result["name"] = f"{name}:{sub}" if sub != "oof" else name
    (RUNS / name / f"{sub}_summary.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("folds")
    p = sub.add_parser("cv")
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--folds", type=int, nargs="*")
    p.add_argument("--set", nargs="*", default=[], help="override key=value (json values)")
    s = sub.add_parser("summary")
    s.add_argument("names", nargs="+")
    args = parser.parse_args()
    if args.cmd == "folds":
        plan = make_folds()
        print(json.dumps({k: v for k, v in plan.items() if k != "folds"}, indent=1))
    elif args.cmd == "cv":
        config = ContextConfig.from_json(args.config)
        overrides = {}
        for item in args.set:
            key, value = item.split("=", 1)
            overrides[key] = json.loads(value)
        if "groups" in overrides:
            overrides["groups"] = tuple(overrides["groups"])
        config = replace(config, **overrides)
        run_cv(config, args.folds)
        print(json.dumps(summarize(config.name), indent=1))
    else:
        for name in args.names:
            r = summarize(name)
            print(json.dumps({k: v for k, v in r.items() if k != "event_iou_p05"}))


if __name__ == "__main__":
    main()
