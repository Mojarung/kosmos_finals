"""Reproduce original-validation refinement and one frozen test comparison."""
from pathlib import Path
import subprocess
import sys


def main():
    # A completed test must not silently start a new adaptive search under the
    # same audit. Move the old runs/refine directory explicitly for a new study.
    if Path("runs/refine/test_comparison.json").exists():
        raise SystemExit("Comparison already exists. Preserve its audit; use export_refinement_results.py to regenerate the report.")
    for name in ["refine_baseline", "refine_v1", "refine_frozen_bn", "refine_weights",
                 "finalize_refinement", "export_refinement_results"]:
        subprocess.run([sys.executable, f"scripts/{name}.py"], check=True)


if __name__ == "__main__":
    main()
