# Refinement of the shipped ensemble

This experiment follows the event-held-out CV research but measures improvement
against the colleague's actual shipped model on its original split. The earlier
CV score (~0.609) is not comparable with the original test_core IoU (~0.697).

- Preserve the original 252 training chips, 89 validation chips, 55 test_core
  chips, 35 test_adjacent chips and 15 Bolivia chips.
- Initialize from each of the five shipped U-Net/ResNet34 checkpoints. Reuse
  their training-only normalization. Do not train on original validation/test.
- Screen three 12-epoch, low-learning-rate continuations on members m0/m1:
  BCE+Dice at crop 256, BCE+Lovasz at crop 256, BCE+Lovasz at crop 384.
  Original epoch zero remains eligible for checkpoint selection.
- Choose the continuation by the two-member original-validation pooled IoU,
  then extend it to members m2/m3/m4.
- On original validation compare the original five, updated five, and their
  equal-weight combination with no TTA, four rotations, and eight D4 transforms.
  Isotonic calibration and probability thresholds use original validation only.
- Select by pooled validation IoU, break ties with lower Brier. Freeze the full
  recipe and its SHA256 before generating new test predictions. The published
  original test score was known beforehand; this is not a previously unseen
  benchmark. No new test results participate in candidate selection.
- After observing validation regression during ordinary continuation, add an
  eight-epoch Lovasz continuation with frozen BatchNorm running statistics and
  lr=1e-5. Screen m0/m1 against the original m0/m1 pair; extend to five only if
  the pair improves. Compare the same three TTA settings and original+updated
  blends. This is an adaptive validation-only probe, not a preregistered CV result.
  Its final freeze is `runs/refine/final_recipe.json` and `final_audit.json`.
- Independently fit positive ensemble coefficients on 200,000 original-validation
  pixels (fixed seed 13), with BCE plus L2 penalty 0.05 toward equal coefficients.
  Check none/rot4/D4 TTA. Compare these three recipes with the other finalists;
  freeze the overall winner in `release_recipe.json` and `release_audit.json`
  before scoring any new test prediction. This is also adaptive validation search.
- Evaluate only the selected candidate and the shipped original on identical
  test masks. For the original, preserve its stored Platt parameters and 0.41
  threshold. Report reproduced baseline and candidate on the same hardware,
  alongside the previously published baseline.

Implementation: `scripts/refine_v1.py`, `scripts/refine_frozen_bn.py`,
`scripts/refine_weights.py`, `scripts/finalize_refinement.py`,
`scripts/evaluate_refinement.py`.
Training logs, prediction caches, selection audit and comparison are in
`runs/refine/`. The original `artifacts/model/` is unchanged. Optional packaged
recipes are supported by `floodvalue infer --recipe PATH/recipe.json`.

To reproduce from original weights and downloaded data, run from `ml/`:

```bash
FLOODVALUE_DEVICE=mps FLOODVALUE_EVAL_BATCH=4 uv run python scripts/run_refinement.py
```

Use `FLOODVALUE_DEVICE=cuda` for CUDA; omit the variable for CPU or auto-selected
CUDA. MPS is opt-in. The wrapper refuses to overwrite a completed test comparison.
The original study ran independent CPU weighting alongside GPU fine-tuning;
the wrapper serializes these steps with the same selection rules and seeds.
