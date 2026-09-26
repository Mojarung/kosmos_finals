"""Run CV jobs sequentially, resume completed folds, and retain per-job logs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configs", nargs="+", required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=[0])
    parser.add_argument("--folds", type=int, nargs="+", default=[0, 1, 2])
    parser.add_argument("--plan", default="research/cv_plan.json")
    parser.add_argument("--out", default="runs/cv")
    args = parser.parse_args()
    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)
    log_dir = root / "logs"
    log_dir.mkdir(exist_ok=True)
    state = {"started_at": time.time(), "jobs": []}
    for name in args.configs:
        for seed in args.seeds:
            for fold in args.folds:
                log = log_dir / f"{name}_seed{seed}_fold{fold}.log"
                command = [sys.executable, "-m", "floodvalue.cv", "run", "--plan", args.plan,
                           "--config", f"configs/cv/{name}.json", "--seed", str(seed),
                           "--fold", str(fold), "--out", args.out]
                job = {"config": name, "seed": seed, "fold": fold, "log": str(log),
                       "started_at": time.time(), "status": "running"}
                state["jobs"].append(job)
                (root / "sweep_state.json").write_text(json.dumps(state, indent=2))
                print(json.dumps(job), flush=True)
                with log.open("a") as stream:
                    proc = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT)
                job.update(status="complete" if proc.returncode == 0 else "failed",
                           returncode=proc.returncode, seconds=time.time() - job["started_at"])
                (root / "sweep_state.json").write_text(json.dumps(state, indent=2))
                print(json.dumps(job), flush=True)
                if proc.returncode:
                    raise SystemExit(proc.returncode)
                subprocess.run([sys.executable, "-m", "floodvalue.cv", "summarize",
                                "--root", args.out, "--out", str(root / "summary.json")],
                               stdout=subprocess.DEVNULL, check=True)


if __name__ == "__main__":
    main()
