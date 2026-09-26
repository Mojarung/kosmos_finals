"""Full-budget architecture trials on the original train/valid split; no test reads."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from floodvalue.train import TrainConfig
from floodvalue.split import load_split
from floodvalue import paths

ROOT = Path("runs/goal072")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait-geometry",action="store_true")
    parser.add_argument("--names",nargs="+",default=["unet_effb4","unet_mitb2","segformer_mitb2"])
    parser.add_argument("--seed",type=int,default=0)
    parser.add_argument("--wait-for",type=Path)
    args = parser.parse_args()
    ROOT.mkdir(exist_ok=True)
    if args.wait_for:
        while not args.wait_for.exists():
            time.sleep(10)
    if args.wait_geometry:
        while not (ROOT/"geometry_results.json").exists():
            time.sleep(10)
    rows=[]
    split=load_split(paths.ARTIFACTS_DIR/"split.json")
    for name in args.names:
        config_path=Path("configs/goal072")/(name+".json")
        config=TrainConfig.from_json(config_path,seed=args.seed)
        member=paths.RUNS_DIR/config.name/f"seed{args.seed}"
        done=member/"train_summary.json"
        if done.exists():
            r=json.loads(done.read_text())
            if TrainConfig(**r["config"]) != config or r["train_chips"] != split["train"] or r["valid_chips"] != split["valid"]:
                raise ValueError("resume differs from the intended experiment")
        else:
            print("Train",name,"seed",args.seed,flush=True)
            with (ROOT/f"{name}_seed{args.seed}.log").open("a") as log:
                subprocess.run([sys.executable,"-m","floodvalue.cli","train","--config",str(config_path),
                                "--seed",str(args.seed)],stdout=log,stderr=subprocess.STDOUT,check=True)
        for tta in ["none","rot4"]:
            archive=ROOT/"predictions"/f"{name}_seed{args.seed}_{tta}_valid.npz"
            subprocess.run([sys.executable,"-m","floodvalue.refine","predict","--member",str(member),
                            "--tta",tta,"--out",str(archive)],check=True)
            recipe_path=ROOT/"recipes"/f"{name}_seed{args.seed}_{tta}.json"
            subprocess.run([sys.executable,"-m","floodvalue.refine","select","--archives",str(archive),
                            "--out",str(recipe_path)],check=True)
            recipe=json.loads(recipe_path.read_text())
            rows.append({"model":name,"seed":args.seed,"tta":tta,"recipe":str(recipe_path),
                         "iou":recipe["validation"]["overall"]["micro"]["iou"]})
            (ROOT/f"architecture_results_seed{args.seed}.json").write_text(json.dumps(rows,indent=2))
            print(json.dumps(rows[-1]),flush=True)


if __name__ == "__main__":
    main()
