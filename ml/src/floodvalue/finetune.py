"""Continue a shipped member on exactly its original training/validation chips."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from floodvalue import paths
from floodvalue.dataset import load_raw, to_tensors
from floodvalue.ensemble import load_member
from floodvalue.split import load_split
from floodvalue.train import DEVICE, TrainConfig, fit


def validate_source(summary, split):
    for field, part in [("train_chips", "train"), ("valid_chips", "valid")]:
        if len(summary[field]) != len(split[part]) or set(summary[field]) != set(split[part]):
            raise ValueError(f"warm-start {field} differs from the original split")


def run(source, config, out):
    split = load_split(paths.ARTIFACTS_DIR / "split.json")
    summary = json.loads((source/"train_summary.json").read_text())
    validate_source(summary, split)
    fingerprint = hashlib.sha256((source/"model.safetensors").read_bytes()).hexdigest()
    provenance = {"member": str(source), "weights_sha256": fingerprint, "previous_config": summary["config"]}
    done = out/"train_summary.json"
    if done.exists():
        saved = json.loads(done.read_text())
        if asdict(TrainConfig(**saved["config"])) != asdict(config) or saved["initialization"] != provenance:
            raise ValueError("different fine-tuning configuration or source checkpoint")
        return saved
    member = load_member(source)
    if any(getattr(config,k) != getattr(member.config,k) for k in
           ["encoder", "channel_set", "architecture", "decoder_attention"]):
        raise ValueError("fine-tuning must preserve the architecture and input channels")
    train = load_raw(paths.SEN1FLOODS11_DIR, split["train"])
    valid = load_raw(paths.SEN1FLOODS11_DIR, split["valid"])
    out.mkdir(parents=True, exist_ok=True)
    (out/"config.json").write_text(json.dumps(asdict(config), indent=2))
    (out/"norm_stats.json").write_text(json.dumps(member.stats.as_dict(), indent=2))
    result = fit(config, to_tensors(train, member.stats), to_tensors(valid, member.stats), out,
                 initial_model=member.model)
    result = {**result, "config": asdict(config), "initialization": provenance,
              "train_chips": list(train.names), "valid_chips": list(valid.names), "device": DEVICE,
              "normalization": "inherited unchanged from the source member", "checkpoint_rule": "include epoch 0"}
    done.write_text(json.dumps(result, indent=2, allow_nan=False))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    result = run(args.source, TrainConfig.from_json(args.config, seed=args.seed), args.out)
    print(json.dumps({"out": str(args.out), "best": result["best"]}), flush=True)


if __name__ == "__main__":
    main()
