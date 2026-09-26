"""Rebuild the S1 validation control needed to reproduce optical training."""
import json
from pathlib import Path
import subprocess
import sys

from floodvalue import paths
from floodvalue.refine import cache_predictions, select_recipe


def main():
    subprocess.run([sys.executable, "scripts/goal072_download_s2.py"], check=True)
    shipped = json.loads((paths.ARTIFACTS_DIR/"refined_model/recipe.json").read_text())
    archives = []
    for i in range(5):
        archive = Path(f"runs/refine/predictions/v1_m{i}_rot4_valid.npz")
        cache_predictions(Path(f"artifacts/model/members/m{i}"), "valid", "rot4", archive)
        archives.append(archive)
    recipe = select_recipe(archives, shipped["weights"], "isotonic", "probabilities")
    destination = Path("runs/goal072/recipes/v1_weighted_probability_mean.json")
    if destination.exists():
        previous = json.loads(destination.read_text())
        if previous != recipe:
            raise ValueError("existing control differs; preserve the frozen experiment before rebuilding")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(recipe, indent=2, allow_nan=False))
    print("ready", str(destination), flush=True)


if __name__ == "__main__":
    main()
