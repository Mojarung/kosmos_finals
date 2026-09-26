"""Fixed Kuro adaptation followed by the unchanged five-fold TerraMind recipe."""
from __future__ import annotations

import gc
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import torch
from safetensors.torch import load_file, save_file

from floodvalue import paths, terramind_cv as base
from floodvalue.l2a_data import development

ROOT = paths.RUNS_DIR / "terramind_kuro"
MANIFEST = paths.PIPELINE_ROOT / "research/KURO_POOL_MANIFEST.json"
TRANSFER = ROOT / "pretrained_backbone.safetensors"
CONFIG = {"steps": 1000, "batch": 4, "seed": 2026901,
          "backbone_lr": 1e-5, "head_lr": 1e-4, "weight_decay": .01}
OriginalSegmenter = base.Segmenter


def balanced_indices(events, batch_size, rng):
    groups = sorted(events)
    return [int(rng.choice(events[groups[int(i)]])) for i in rng.integers(len(groups), size=batch_size)]


def pool():
    manifest = json.loads(MANIFEST.read_text())
    rows = sorted((r for r in manifest["rows"] if r["admitted_to_training_pool"]), key=lambda r: r["id"])
    xs, ys, vs, events, hashes = [], [], [], {}, {}
    for i, row in enumerate(rows):
        if row["overlaps"] or not row["after_2019"]:
            raise ValueError("external training admission is inconsistent")
        path = paths.DATA_DIR / "kuro_pilot/samples" / (row["id"] + ".npz")
        hashes[row["id"]] = hashlib.sha256(path.read_bytes()).hexdigest()
        with np.load(path) as z:
            h, w = z["target"].shape
            if (h, w) != (224, 224):
                raise ValueError("expected native 224 Kuro grid")
            xs.append(base.normalized(z["s1"], np.zeros((12,h,w), np.float32), np.zeros((h,w), bool)))
            ys.append(z["target"]); vs.append(z["valid"])
        events.setdefault(str(row["event"]), []).append(i)
    return np.stack(xs), np.stack(ys), np.stack(vs), events, hashes


def pretrain(device):
    x, y, v, events, hashes = pool()
    source = {"config": CONFIG, "pool_manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
              "sample_sha256": hashes, "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "baseline_source_sha256": hashlib.sha256(Path(base.__file__).read_bytes()).hexdigest(),
              "initial_pretrained_sha256": base.PRETRAINED_SHA}
    signature = ROOT / "pretraining_signature.json"
    if signature.exists() and json.loads(signature.read_text()) != source:
        raise ValueError("Kuro pretraining signature changed")
    signature.write_text(json.dumps(source, indent=2))
    if TRANSFER.exists():
        return
    torch.manual_seed(CONFIG["seed"])
    rng = np.random.default_rng(CONFIG["seed"])
    model = OriginalSegmenter(pretrained=True).to(device).train()
    frozen = []
    for name, parameter in model.backbone.named_parameters():
        if "sen2l2a" in name:
            parameter.requires_grad_(False)
            frozen.append(name)
    if not frozen:
        raise ValueError("S2 embedding names changed")
    optimizer = torch.optim.AdamW([
        {"params": [p for p in model.backbone.parameters() if p.requires_grad], "lr": CONFIG["backbone_lr"]},
        {"params": [p for n,p in model.named_parameters() if not n.startswith("backbone.")], "lr": CONFIG["head_lr"]},
    ], weight_decay=CONFIG["weight_decay"])
    history = []; started = time.monotonic()
    for step in range(CONFIG["steps"]):
        idx = balanced_indices(events, CONFIG["batch"], rng)
        xx, yy, vv = x[idx].astype(np.float32), y[idx], v[idx]
        k, flip = int(rng.integers(4)), bool(rng.integers(2))
        arrays = [np.rot90(a, k, axes=(-2,-1)) for a in (xx,yy,vv)]
        if flip:
            arrays = [np.flip(a, axis=-1) for a in arrays]
        xx, yy, vv = [torch.from_numpy(a.astype(np.float32).copy()).to(device) for a in arrays]
        optimizer.zero_grad(set_to_none=True)
        loss = base.loss_fn(model(xx), yy, vv)
        if not torch.isfinite(loss):
            raise FloatingPointError("nonfinite Kuro loss")
        loss.backward(); torch.nn.utils.clip_grad_norm_(model.parameters(), 1.); optimizer.step()
        history.append(float(loss.detach().cpu()))
        if (step+1) % 100 == 0:
            print(json.dumps({"kuro_step": step+1, "mean_loss_last100": float(np.mean(history[-100:])),
                              "elapsed_seconds": time.monotonic()-started}), flush=True)
    save_file({n:p.detach().cpu().contiguous() for n,p in model.backbone.state_dict().items()}, str(TRANSFER.with_suffix(".tmp")))
    TRANSFER.with_suffix(".tmp").replace(TRANSFER)
    (ROOT / "pretraining.json").write_text(json.dumps({"trained": True, "samples": len(x),
        "events": {k:len(rows) for k,rows in events.items()}, "frozen_s2_parameters": frozen,
        "history": history, "seconds": time.monotonic()-started,
        "weights_sha256": hashlib.sha256(TRANSFER.read_bytes()).hexdigest()}, indent=2))
    del model, optimizer
    gc.collect()
    if device == "mps":
        torch.mps.empty_cache()


class TransferSegmenter(OriginalSegmenter):
    def __init__(self, pretrained=True):
        super().__init__(pretrained=pretrained)
        if pretrained:
            self.backbone.load_state_dict(load_file(str(TRANSFER)), strict=True)


def main():
    torch.set_num_threads(4)
    ROOT.mkdir(parents=True, exist_ok=True)
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    pretrain(device)
    fold_of = development(); names = sorted(fold_of)
    signature = {"config": base.CONFIG, "folds": fold_of,
                 "backbone_sha256": hashlib.sha256(TRANSFER.read_bytes()).hexdigest(),
                 "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                 "baseline_signature": json.loads((base.ROOT / "signature.json").read_text())}
    path = ROOT / "signature.json"
    if path.exists() and json.loads(path.read_text()) != signature:
        raise ValueError("Kuro CV signature changed")
    path.write_text(json.dumps(signature, indent=2))
    base.prepare_cache(names)
    arrays = {k:np.load(base.CACHE/(k+".npy"), mmap_mode="r") for k in ("x", "target", "valid")}
    # Reuse the exact training implementation; only initialization and output root differ.
    base.ROOT = ROOT
    base.Segmenter = TransferSegmenter
    for k in range(5):
        base.run_fold(k, names, fold_of, arrays, device)


if __name__ == "__main__":
    main()
