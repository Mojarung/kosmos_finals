"""Freeze validation winner, compare on test, then package exact selected weights."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from floodvalue.refine import package_recipe


def main():
    root = Path("runs/refine")
    waiting = [root/"final_recipe.json"] + [root/"recipes"/f"v1_weighted_{tta}.json" for tta in ["none", "rot4", "d4"]]
    while not all(p.exists() for p in waiting):
        time.sleep(10)
    ranking = json.loads(waiting[0].read_text())["selection"]["validation_ranking"]
    for path in waiting[1:]:
        r = json.loads(path.read_text())
        ranking.append({"path": str(path), "iou": r["validation"]["overall"]["micro"]["iou"],
                        "brier": r["validation"]["probability"]["brier"]})
    ranking.sort(key=lambda r: (-r["iou"], r["brier"]))
    recipe = json.loads(Path(ranking[0]["path"]).read_text())
    recipe["selection"] = {"validation_ranking": ranking, "frozen_at_unix": time.time(),
                           "test_seen_for_selection": False}
    destination = root/"release_recipe.json"
    destination.write_text(json.dumps(recipe, indent=2, allow_nan=False))
    audit = root/"release_audit.json"
    audit.write_text(json.dumps({"recipe_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                                "validation_ranking": ranking}, indent=2))
    print(json.dumps({"frozen_candidate": ranking[0]}), flush=True)
    subprocess.run([sys.executable, "scripts/evaluate_refinement.py", "--recipe", str(destination),
                    "--audit", str(audit)], check=True)
    packaged = package_recipe(destination, Path("artifacts/refined_model"))
    print(json.dumps({"packaged": str(packaged)}), flush=True)


if __name__ == "__main__":
    main()
