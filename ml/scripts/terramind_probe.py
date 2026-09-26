"""Measure a real forward/backward step of pinned TerraMind tiny on this machine.

Run in the isolated runs/terramind_env, not the production ML environment.
Synthetic inputs test execution and memory only; they do not measure segmentation quality.
"""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time

import torch
from torch import nn
import torch.nn.functional as F


def main():
    from terratorch import BACKBONE_REGISTRY

    root = Path("runs/terramind")
    checkpoint = root/"pretrained/TerraMind_v1_tiny.pt"
    model = BACKBONE_REGISTRY.build("terramind_v1_tiny", pretrained=False, modalities=["S2L2A", "S1GRD"])
    pretrained = torch.load(checkpoint, map_location="cpu", weights_only=True)
    selected = {}
    for name, value in model.state_dict().items():
        if name not in pretrained or pretrained[name].shape != value.shape:
            raise ValueError(f"pretrained coverage is incomplete for {name}")
        selected[name] = pretrained[name]
    model.load_state_dict(selected, strict=True)
    del selected, pretrained
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    torch.set_num_threads(4)
    torch.manual_seed(2026)
    model = model.to(device).train()
    dim = model.out_channels[-1]
    head = nn.Sequential(nn.Conv2d(dim, 64, 3, padding=1), nn.GroupNorm(8, 64), nn.GELU(),
                         nn.Conv2d(64, 1, 1)).to(device)
    parameters = list(model.parameters())+list(head.parameters())
    optimizer = torch.optim.AdamW(parameters, lr=2e-5)
    inputs = {"S2L2A": torch.randn(2, 12, 224, 224, device=device),
              "S1GRD": torch.randn(2, 2, 224, 224, device=device)}
    target = torch.zeros((2, 1, 224, 224), device=device)
    target[:, :, 50:100, 50:120] = 1
    times, losses = [], []
    for _ in range(4):
        start = time.monotonic()
        tokens = model(inputs)[-1]
        image = tokens.transpose(1, 2).reshape(2, dim, 14, 14)
        logits = F.interpolate(head(image), size=(224, 224), mode="bilinear", align_corners=False)
        loss = F.binary_cross_entropy_with_logits(logits, target)
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        if not torch.isfinite(loss) or not all(p.grad is None or torch.isfinite(p.grad).all() for p in parameters):
            raise FloatingPointError("nonfinite loss or gradients")
        optimizer.step()
        if device == "mps":
            torch.mps.synchronize()
        times.append(time.monotonic()-start); losses.append(float(loss.detach().cpu()))
    result = {"purpose": "synthetic execution/memory probe, not a quality experiment", "device": device,
              "torch": torch.__version__, "terratorch": importlib.metadata.version("terratorch"),
              "pretrained_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
              "pretrained_hf_revision": json.loads((root/"model_card.json").read_text())["sha"],
              "pretrained_parameter_coverage": "all encoder state tensors, strict", "backbone_parameters": sum(p.numel() for p in model.parameters()),
              "batch": 2, "crop": 224, "modalities": ["S2L2A", "S1GRD"], "step_seconds": times, "losses": losses,
              "mps_allocated_bytes_after_step": torch.mps.current_allocated_memory() if device == "mps" else None,
              "mps_driver_bytes_after_step": torch.mps.driver_allocated_memory() if device == "mps" else None}
    out = Path("research/TERRAMIND_PROBE.json")
    out.write_text(json.dumps(result, indent=2, allow_nan=False))
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
