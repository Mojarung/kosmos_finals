"""Screen geometry on validation only, starting with the colleague's checkpoints."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path("runs/goal072")


def predict(i, spatial, tta="none"):
    if spatial == "full":
        return Path(f"runs/refine/predictions/v1_m{i}_{tta}_valid.npz")
    out = ROOT/"predictions"/f"v1_m{i}_{spatial}_{tta}_valid.npz"
    subprocess.run([sys.executable, "-m", "floodvalue.refine", "predict", "--member",
                    f"artifacts/model/members/m{i}", "--tta", tta, "--spatial", spatial,
                    "--out", str(out)], check=True)
    return out


def select(name, archives, weights=None):
    out = ROOT/"recipes"/(name+".json")
    args = [sys.executable, "-m", "floodvalue.refine", "select", "--archives", *map(str,archives), "--out", str(out)]
    if weights is not None:
        args += ["--weights", *map(str,weights)]
    subprocess.run(args, check=True)
    recipe = json.loads(out.read_text())
    return {"path": str(out), "iou": recipe["validation"]["overall"]["micro"]["iou"],
            "brier": recipe["validation"]["probability"]["brier"]}


def main():
    ROOT.mkdir(exist_ok=True)
    baseline = select("v1_full_pair", [predict(i,"full") for i in [0,1]])
    rows = []
    for spatial in ["scale050", "scale075", "scale125", "tiles256", "tiles384"]:
        row = select("v1_"+spatial+"_pair", [predict(i,spatial) for i in [0,1]])
        rows.append({**row,"spatial":spatial})
    rows.sort(key=lambda r:-r["iou"])
    (ROOT/"geometry_screen.json").write_text(json.dumps({"baseline":baseline,"ranking":rows},indent=2))
    finalists = []
    for row in rows[:2]:
        if row["iou"] <= baseline["iou"]:
            continue
        spatial = row["spatial"]
        for tta in ["none", "rot4"]:
            archives = [predict(i,spatial,tta) for i in range(5)]
            finalists.append(select(f"v1_{spatial}_{tta}",archives))
            old = [predict(i,"full","rot4") for i in range(5)]
            finalists.append(select(f"v1_full_rot4_plus_{spatial}_{tta}",old+archives))
    (ROOT/"geometry_results.json").write_text(json.dumps(finalists,indent=2))
    print(json.dumps({"geometry_finalists":finalists}),flush=True)


if __name__ == "__main__":
    main()
