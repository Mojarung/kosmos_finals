"""S1-only local-texture classifier, inspired by pixel boosting in STAC Overflow."""
import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
from scipy.ndimage import uniform_filter
from scipy.special import logit
from sklearn.ensemble import HistGradientBoostingClassifier

from floodvalue import paths
from floodvalue.chips import Chip, read_label, read_s1, s1_valid
from floodvalue.split import load_split


def texture_features(s1):
    valid = s1_valid(s1)
    x = np.clip(np.nan_to_num(s1, nan=0., posinf=0., neginf=0.), -50, 5).astype(np.float32)
    x[:, ~valid] = 0
    features = [x[0], x[1], x[0]-x[1]]
    count = valid.astype(np.float32)
    for size in [3, 9, 31]:
        n = np.maximum(uniform_filter(count, size, mode="reflect"), 1e-6)
        for channel in x:
            mean = uniform_filter(channel, size, mode="reflect")/n
            variance = np.maximum(uniform_filter(channel**2, size, mode="reflect")/n-mean**2, 0)
            features.extend([mean, np.sqrt(variance), channel-mean])
    for channel in x:
        values = channel[valid]
        median = float(np.median(values)) if len(values) else 0.
        features.append(channel-median)
    return np.stack(features, axis=-1).astype(np.float32)


def predict(model, s1):
    features = texture_features(s1)
    flat = features.reshape(-1, features.shape[-1])
    p = np.concatenate([model.predict_proba(flat[i:i+65536])[:, 1]
                        for i in range(0, len(flat), 65536)])
    return logit(np.clip(p, 1e-6, 1-1e-6)).reshape(features.shape[:2]).astype(np.float32)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("runs/goal072/tabular"))
    args = parser.parse_args()
    split = load_split(paths.ARTIFACTS_DIR/"split.json")
    args.out.mkdir(parents=True, exist_ok=True)
    checkpoint = args.out/"model.joblib"
    if not checkpoint.exists():
        rng = np.random.default_rng(72)
        xs, ys = [], []
        for i,name in enumerate(split["train"]):
            chip=Chip.from_name(name)
            s1, label=read_s1(paths.SEN1FLOODS11_DIR,chip),read_label(paths.SEN1FLOODS11_DIR,chip)
            valid = s1_valid(s1) & (label != -1)
            indices=np.flatnonzero(valid)
            indices=rng.choice(indices,size=min(2500,len(indices)),replace=False)
            f=texture_features(s1)
            xs.append(f.reshape(-1,f.shape[-1])[indices]);ys.append((label.ravel()[indices]==1))
            if i%40==0:print("feature chips",i,flush=True)
        x,y=np.concatenate(xs),np.concatenate(ys)
        print("fit",x.shape,"water",float(y.mean()),flush=True)
        model=HistGradientBoostingClassifier(max_iter=400,learning_rate=.06,max_leaf_nodes=31,
                                             min_samples_leaf=40,l2_regularization=5.,random_state=72,
                                             early_stopping=True,validation_fraction=.1,n_iter_no_change=30)
        model.fit(x,y)
        joblib.dump(model,checkpoint,compress=3)
        (args.out/"config.json").write_text(json.dumps({"kind":"hist_gradient_boosting","seed":72,"pixels_per_chip":2500,
                                                       "features":"VV,VH,difference,mean/std/contrast at 3,9,31 px,median contrast",
                                                       "n_iter":int(model.n_iter_)},indent=2))
        (args.out/"train_summary.json").write_text(json.dumps({"train_chips":split["train"],"valid_chips":split["valid"],
                                                               "sampled_training_pixels":len(y)},indent=2))
    else:
        model=joblib.load(checkpoint)
    logits,targets,masks=[],[],[]
    for i,name in enumerate(split["valid"]):
        chip=Chip.from_name(name)
        s1,label=read_s1(paths.SEN1FLOODS11_DIR,chip),read_label(paths.SEN1FLOODS11_DIR,chip)
        logits.append(predict(model,s1));targets.append(label==1);masks.append(s1_valid(s1)&(label!=-1))
        if i%10==0:print("validation chips",i,flush=True)
    archive=args.out/"valid_predictions.npz"
    np.savez_compressed(archive,names=split["valid"],logits=np.stack(logits),target=np.stack(targets),valid=np.stack(masks))
    archive.with_suffix(".json").write_text(json.dumps({"member":str(args.out),"weights_sha256":hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
                                                       "weights_file":"model.joblib","kind":"tabular","part":"valid",
                                                       "tta":"none","names":split["valid"]},indent=2))
    from floodvalue.refine import select_recipe
    recipe=select_recipe([archive],[1.],"isotonic")
    (args.out/"recipe.json").write_text(json.dumps(recipe,indent=2,allow_nan=False))
    print(json.dumps({"validation_iou":recipe["validation"]["overall"]["micro"]["iou"],
                      "brier":recipe["validation"]["probability"]["brier"]}),flush=True)


if __name__ == "__main__":
    main()
