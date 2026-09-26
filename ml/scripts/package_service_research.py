"""Build, verify or restore the research checkpoints without changing production artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from floodvalue import paths

ROOT = paths.PIPELINE_ROOT
PACKAGE = ROOT / "artifacts/service_research"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build():
    experiment = json.loads((ROOT / "research/SERVICE_EXPERIMENT_MANIFEST.json").read_text())
    files = set()
    for model in experiment["models"].values():
        path = ROOT / model["path"]
        if sha(path) != model["sha256"]:
            raise ValueError(f"checkpoint differs from experiment manifest: {path}")
        files.add(path)
        files.update(path.parent.glob("*.json"))
    for directory in ("l2a_boost", "terramind_cv", "terramind_kuro", "spatial_boost"):
        files.update((ROOT / "runs" / directory).glob("*.json"))
    files.add(ROOT / "runs/terramind_kuro/pretrained_backbone.safetensors")
    for fold in range(3):
        folder = ROOT / "runs/spatial_boost" / f"fold{fold}"
        files.add(folder / "model.joblib")
        files.update(folder.glob("*.json"))
    files.add(ROOT / "data/l2a_boost/acquisition.json")
    manifest = {"kind": "research archive; not a production inference recipe", "files": {}}
    for source in sorted(files):
        relative = source.relative_to(ROOT)
        target = PACKAGE / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = sha(source)
        if target.exists() and sha(target) != digest:
            raise ValueError(f"refusing to overwrite different packaged file: {target}")
        if not target.exists():
            shutil.copyfile(source, target)
        manifest["files"][str(relative)] = {"sha256": digest, "bytes": source.stat().st_size}
    (PACKAGE / "manifest.json").write_text(json.dumps(manifest, indent=2))


def verify_restore(restore=False):
    manifest = json.loads((PACKAGE / "manifest.json").read_text())
    for relative, expected in manifest["files"].items():
        relative = Path(relative)
        if relative.is_absolute() or ".." in relative.parts or relative.parts[0] not in ("runs", "data"):
            raise ValueError("invalid archive path")
        source = PACKAGE / relative
        if source.stat().st_size != expected["bytes"] or sha(source) != expected["sha256"]:
            raise ValueError(f"archive checksum mismatch; check git lfs pull: {source}")
    if restore:
        for relative, expected in manifest["files"].items():
            target = ROOT / relative
            if target.exists() and sha(target) != expected["sha256"]:
                raise ValueError(f"refusing to overwrite an existing different run: {target}")
        for relative in manifest["files"]:
            target = ROOT / relative
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(PACKAGE / relative, target)
    print(json.dumps({"verified_files": len(manifest["files"]),
                      "bytes": sum(x["bytes"] for x in manifest["files"].values()), "restored": restore}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "verify", "restore"))
    action = parser.parse_args().action
    if action == "build":
        build()
    verify_restore(action == "restore")


if __name__ == "__main__":
    main()
