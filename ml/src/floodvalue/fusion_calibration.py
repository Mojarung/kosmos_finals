"""Calibration conditional on input availability, fitted on inner validation only."""
import numpy as np

from floodvalue.cv_calibration import fit_calibrator
from floodvalue.refine import load_recipe_calibration


def fit_branch_calibration(logits, target, valid, available):
    result = {}
    for name, mask in [("optical", valid & available), ("fallback", valid & ~available)]:
        if not mask.any():
            raise ValueError("each calibration branch requires inner-validation pixels")
        result[name] = fit_calibrator(logits[mask], target[mask], "isotonic").as_dict()
    return result


class BranchCalibration:
    def __init__(self, parameters, available):
        self.available = np.asarray(available, bool)
        self.models = {k: load_recipe_calibration({"calibration_method": "isotonic", "calibration": v})
                       for k,v in parameters.items()}

    def apply(self, logits):
        if logits.shape != self.available.shape:
            raise ValueError("branch calibration mask shape differs")
        result = np.empty_like(logits, dtype=np.float32)
        for name, mask in [("optical", self.available), ("fallback", ~self.available)]:
            if mask.any():
                result[mask] = self.models[name].apply(logits[mask])
        return result


def fusion_calibration(recipe, available):
    if recipe["calibration_method"] == "branch_isotonic":
        return BranchCalibration(recipe["calibration"], available)
    return load_recipe_calibration(recipe)
