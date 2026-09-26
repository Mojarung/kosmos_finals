"""Validation-only regularized positive weighting of the colleague's five models."""
import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

from floodvalue.calibration import subsample
from floodvalue.refine import select_recipe
from floodvalue.split import load_split
from floodvalue import paths


def main():
    root = Path("runs/refine")
    split = load_split(paths.ARTIFACTS_DIR/"split.json")
    for tta in ["none", "rot4", "d4"]:
        archives = [root/"predictions"/f"v1_m{i}_{tta}_valid.npz" for i in range(5)]
        columns = []
        for path in archives:
            metadata = json.loads(path.with_suffix(".json").read_text())
            if metadata["part"] != "valid" or metadata["names"] != split["valid"]:
                raise ValueError("weight fitting requires original validation only")
            with np.load(path) as a:
                mask = a["valid"]
                if not columns:
                    idx = subsample(int(mask.sum()), 200_000, 13)
                    target = a["target"][mask][idx].astype(np.float64)
                columns.append(a["logits"][mask][idx].astype(np.float64))
        x = np.stack(columns, axis=1)

        def objective(parameters):
            coefficients, intercept = parameters[:-1], parameters[-1]
            z = x @ coefficients + intercept
            error = expit(z)-target
            deviation = coefficients-coefficients.mean()
            loss = np.mean(np.logaddexp(0, z)-target*z) + .05*np.sum(deviation**2)
            gradient = np.r_[x.T @ error/len(target) + .1*deviation, error.mean()]
            return loss, gradient

        solution = minimize(objective, np.r_[np.full(5, .15), -.18], jac=True,
                            method="L-BFGS-B", bounds=[(.04, .4)]*5+[(-3, 3)])
        if not solution.success:
            raise RuntimeError(solution.message)
        weights = (solution.x[:-1]/solution.x[:-1].sum()).tolist()
        recipe = select_recipe(archives, weights, "isotonic")
        recipe["weight_selection"] = {"rule": "positive logistic stacking, BCE plus 0.05 L2 toward equal coefficients",
                                      "sample_seed": 13, "sample_pixels": len(target), "regularization": .05,
                                      "fit_part": "original valid", "parameters": solution.x.tolist()}
        destination = root/"recipes"/f"v1_weighted_{tta}.json"
        destination.write_text(json.dumps(recipe, indent=2, allow_nan=False))
        print(json.dumps({"recipe": str(destination), "weights": weights,
                          "iou": recipe["validation"]["overall"]["micro"]["iou"]}), flush=True)


if __name__ == "__main__":
    main()
