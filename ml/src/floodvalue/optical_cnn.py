"""Small spatial S2 model with training-only normalization and the existing SAR fallback."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from scipy.special import expit
from safetensors.torch import load_file

from floodvalue import paths
from floodvalue.chips import Chip, read_s1, read_label, s1_valid
from floodvalue.dataset import TensorPart
from floodvalue.optical import event_dates, optical_date_allowed, read_optical
from floodvalue.next_boost import ROOT, check_development, permitted, evaluate
from floodvalue.split import load_split
from floodvalue.train import TrainConfig, build_model, fit, predict_logits, DEVICE

BANDS = [1, 2, 3, 7, 11, 12]


def normalize_s2(s1, s2, stats):
    available = np.isfinite(s2).all(axis=0) & (np.nan_to_num(s2).max(axis=0) > 0)
    x = (np.clip(s2[BANDS], 0, 1)-np.asarray(stats["mean"])[:, None, None])/np.asarray(stats["std"])[:, None, None]
    x[:, ~(available & s1_valid(s1))] = 0
    return x.astype(np.float32), available


def fit_stats(names):
    values = []
    for name in names:
        chip = Chip.from_name(name)
        s1, s2 = read_s1(paths.SEN1FLOODS11_DIR, chip), read_optical(paths.SEN1FLOODS11_DIR, chip)
        valid = s1_valid(s1) & np.isfinite(s2).all(axis=0) & (np.nan_to_num(s2).max(axis=0) > 0)
        idx = np.flatnonzero(valid)
        seed = int.from_bytes(hashlib.sha256(name.encode()).digest()[:4], "little")
        idx = np.random.default_rng(seed).choice(idx, min(10000, len(idx)), replace=False)
        values.append(np.clip(s2[BANDS].reshape(6, -1)[:, idx], 0, 1))
    x = np.concatenate(values, axis=1)
    return {"bands": BANDS, "clip": [0, 1], "mean": x.mean(axis=1).tolist(),
            "std": np.maximum(x.std(axis=1), 1e-4).tolist(), "fitted_on": names}


def tensors(names, stats):
    xs, ys, masks = [], [], []
    for name in names:
        chip = Chip.from_name(name)
        s1 = read_s1(paths.SEN1FLOODS11_DIR, chip)
        label = read_label(paths.SEN1FLOODS11_DIR, chip)
        x, available = normalize_s2(s1, read_optical(paths.SEN1FLOODS11_DIR, chip), stats)
        xs.append(x); ys.append((label == 1).astype(np.float32))
        masks.append(s1_valid(s1) & available & (label != -1))
    return TensorPart(tuple(names), np.stack(xs), np.stack(ys), np.stack(masks))


def train(parts, directory, fold):
    check_development(parts)
    cfg = TrainConfig(name=str(directory.relative_to(paths.RUNS_DIR)) if directory.is_absolute() else str(directory)[5:],
                      channel_set="s2_six", encoder="resnet18", epochs=50 if fold is None else 30,
                      batch_size=16, crop=256, crops_per_chip=4 if fold is None else 2,
                      lr=.0005, encoder_lr_scale=.2, ema_decay=.995, grad_clip=1., seed=76,
                      checkpoint_metric="best_threshold", resume_training=True)
    names = permitted(parts["train"])
    valid = permitted(parts["valid"])
    done = directory/"train_summary.json"
    if done.exists():
        r = json.loads(done.read_text())
        if TrainConfig(**r["config"]) != cfg or r["train_chips"] != names or r["valid_chips"] != valid:
            raise ValueError("optical CNN resume differs")
        return
    directory.mkdir(parents=True, exist_ok=True)
    stats_path = directory/"optical_norm.json"
    stats = json.loads(stats_path.read_text()) if stats_path.exists() else fit_stats(names)
    if stats["fitted_on"] != names:
        raise ValueError("normalization fitted on another split")
    stats_path.write_text(json.dumps(stats, indent=2))
    (directory/"config.json").write_text(json.dumps(asdict(cfg), indent=2))
    result = fit(cfg, tensors(names, stats), tensors(valid, stats), directory)
    done.write_text(json.dumps({**result, "config": asdict(cfg), "train_chips": names,
                               "valid_chips": valid, "device": DEVICE}, indent=2))


def load_cnn(directory):
    cfg = TrainConfig(**json.loads((directory/"config.json").read_text()))
    model = build_model(cfg, pretrained=False)
    model.load_state_dict({k: v.float() for k, v in load_file(str(directory/"model.safetensors")).items()})
    return model.to(DEVICE).eval(), json.loads((directory/"optical_norm.json").read_text())


def predict_cnn(model, stats, s1s, s2s, tta="none"):
    transformed = [normalize_s2(s1, s2, stats) for s1, s2 in zip(s1s, s2s)]
    x = np.stack([a[0] for a in transformed])
    total = np.zeros((len(x), *x.shape[-2:]), np.float32)
    rotations = range(4) if tta == "rot4" else [0]
    for k in rotations:
        a = np.ascontiguousarray(np.rot90(x, k, axes=(-2, -1)))
        z = predict_logits(model, a).cpu().numpy()
        total += np.rot90(z, -k, axes=(-2, -1))
    return expit(total/len(rotations)).astype(np.float32), np.stack([a[1] for a in transformed])


def cache(directory, names, part, tta="none"):
    destination = directory if tta == "none" else directory/tta
    destination.mkdir(exist_ok=True)
    path = destination/(part+"_optical.npz")
    metadata = {"names": names, "tta": tta, "weights_sha256": hashlib.sha256((directory/"model.safetensors").read_bytes()).hexdigest()}
    if path.exists():
        if json.loads(path.with_suffix(".json").read_text()) != metadata:
            raise ValueError("CNN cache provenance differs")
        return destination
    model, stats = load_cnn(directory)
    dates = event_dates(paths.SEN1FLOODS11_DIR)
    ps, masks = [], []
    for i, name in enumerate(names):
        chip = Chip.from_name(name)
        s1 = read_s1(paths.SEN1FLOODS11_DIR, chip)
        if optical_date_allowed(**dates[chip.event]):
            p, available = predict_cnn(model, stats, [s1], [read_optical(paths.SEN1FLOODS11_DIR, chip)], tta)
            p, available = p[0], available[0]
        else:
            p, available = np.zeros(s1.shape[1:], np.float32), np.zeros(s1.shape[1:], bool)
        ps.append(p); masks.append(available)
        if i % 20 == 0:
            print("CNN predict", part, tta, i, flush=True)
    np.savez_compressed(path, names=names, probability=np.stack(ps), available=np.stack(masks))
    path.with_suffix(".json").write_text(json.dumps(metadata, indent=2))
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait-radar", action="store_true")
    parser.add_argument("--stage", choices=["full", "cv", "all"], default="all")
    args = parser.parse_args()
    if args.wait_radar:
        while not Path("runs/goal072/recipes/unet_mitb2_seed0_rot4.json").exists():
            time.sleep(10)
    split = load_split(paths.ARTIFACTS_DIR/"split.json")
    plan = json.loads(Path("research/cv_plan.json").read_text())
    specs = []
    if args.stage in ["full", "all"]:
        specs.append((None, {"train": split["train"], "valid": split["valid"]}))
    if args.stage in ["cv", "all"]:
        specs.extend((f["fold"], {k: f[k] for k in ["train", "valid", "score"]}) for f in plan["folds"])
    for fold, parts in specs:
        directory = ROOT/"cnn"/("full" if fold is None else f"fold{fold}")
        train(parts, directory, fold)
        for tta in ["none", "rot4"] if fold is None else ["none"]:
            for part in ["valid"] if fold is None else ["valid", "score"]:
                destination = cache(directory, parts[part], part, tta)
            evaluate(destination, fold)


if __name__ == "__main__":
    main()
