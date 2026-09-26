"""Cache original-validation predictions of the shipped five-member ensemble."""
import json
from pathlib import Path
import subprocess
import sys


def main():
    root = Path("runs/refine")
    for tta in ["none", "rot4", "d4"]:
        archives = []
        for i in range(5):
            path = root/"predictions"/f"v1_m{i}_{tta}_valid.npz"
            subprocess.run([sys.executable, "-m", "floodvalue.refine", "predict", "--member",
                            f"artifacts/model/members/m{i}", "--tta", tta, "--out", str(path)], check=True)
            archives.append(path)
        for calibration in ["platt", "isotonic"]:
            destination = root/"recipes"/f"v1_{tta}_{calibration}.json"
            subprocess.run([sys.executable, "-m", "floodvalue.refine", "select", "--archives",
                            *map(str, archives), "--calibration", calibration, "--out", str(destination)], check=True)
            recipe = json.loads(destination.read_text())
            print(json.dumps({"recipe": str(destination),
                              "iou": recipe["validation"]["overall"]["micro"]["iou"]}), flush=True)


if __name__ == "__main__":
    main()
