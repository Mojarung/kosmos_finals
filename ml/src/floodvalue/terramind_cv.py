"""Pinned tiny foundation model development CV; run in runs/terramind_env."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np
import torch
from torch import nn
import torch.nn.functional as F
from safetensors.torch import load_file, save_file

from floodvalue import paths
from floodvalue.chips import Chip, read_s1, read_label, s1_valid
from floodvalue.l2a_data import ROOT as DATA, development

ROOT = paths.RUNS_DIR/"terramind_cv"
CACHE = paths.DATA_DIR/"terramind_cache"
WEIGHTS = paths.RUNS_DIR/"terramind/pretrained/TerraMind_v1_tiny.pt"
PRETRAINED_SHA = "e56ea9ebcd4451078b9ca4893d5cd8a89bbee376ae16829c3e7fbbbc76de0eba"
CONFIG = {"epochs": 25, "crop": 224, "batch": 4, "crops_per_chip": 2,
          "backbone_lr": 2e-5, "head_lr": 2e-4, "weight_decay": .01, "optical_dropout": .3, "seed": 2026}
S1_MEAN = np.asarray([-12.599, -20.293], np.float32)
S1_STD = np.asarray([5.195, 5.890], np.float32)
S2_MEAN = np.asarray([1390.458,1503.317,1718.197,1853.910,2199.100,2779.975,2987.011,3083.234,3132.220,3162.988,2424.884,1857.648], np.float32)
S2_STD = np.asarray([2106.761,2141.107,2038.973,2134.138,2085.321,1889.926,1820.257,1871.918,1753.829,1797.379,1434.261,1334.311], np.float32)


def normalized(s1, reflectance, usable):
    radar = (s1-S1_MEAN[:, None, None])/S1_STD[:, None, None]
    radar[:, ~np.isfinite(s1).all(axis=0)] = 0
    optical = (reflectance*10000.-S2_MEAN[:, None, None])/S2_STD[:, None, None]
    optical[:, ~usable] = 0
    return np.concatenate([np.nan_to_num(radar), np.nan_to_num(optical), usable[None]], axis=0).astype(np.float16)


def prepare_cache(names):
    CACHE.mkdir(parents=True, exist_ok=True)
    signature = {"names": names, "acquisition_sha256": hashlib.sha256((DATA/"acquisition.json").read_bytes()).hexdigest(),
                 "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    ready = CACHE/"ready.json"
    if ready.exists():
        if json.loads(ready.read_text()) != signature:
            raise ValueError("TerraMind input cache signature changed")
        return
    arrays = {
        "x": np.lib.format.open_memmap(CACHE/"x.npy", mode="w+", dtype=np.float16, shape=(len(names),15,512,512)),
        "target": np.lib.format.open_memmap(CACHE/"target.npy", mode="w+", dtype=bool, shape=(len(names),512,512)),
        "valid": np.lib.format.open_memmap(CACHE/"valid.npy", mode="w+", dtype=bool, shape=(len(names),512,512)),
    }
    for i, name in enumerate(names):
        chip = Chip.from_name(name)
        s1, label = read_s1(paths.SEN1FLOODS11_DIR, chip), read_label(paths.SEN1FLOODS11_DIR, chip)
        with np.load(DATA/"chips"/(name+".npz")) as z:
            arrays["x"][i] = normalized(s1, z["reflectance"].astype(np.float32), z["usable"])
        arrays["target"][i] = label == 1
        arrays["valid"][i] = s1_valid(s1) & (label != -1)
    for a in arrays.values():
        a.flush()
    ready.write_text(json.dumps(signature, indent=2))


class Segmenter(nn.Module):
    def __init__(self, pretrained=True):
        super().__init__()
        from terratorch import BACKBONE_REGISTRY

        self.backbone = BACKBONE_REGISTRY.build("terramind_v1_tiny", pretrained=False, modalities=["S2L2A", "S1GRD"])
        if pretrained:
            if hashlib.sha256(WEIGHTS.read_bytes()).hexdigest() != PRETRAINED_SHA:
                raise ValueError("pretrained checksum differs")
            state = torch.load(WEIGHTS, map_location="cpu", weights_only=True)
            selected = {}
            for key, value in self.backbone.state_dict().items():
                if key not in state or state[key].shape != value.shape:
                    raise ValueError(f"missing pretrained tensor {key}")
                selected[key] = state[key]
            self.backbone.load_state_dict(selected, strict=True)
        dim = self.backbone.out_channels[-1]
        self.context = nn.Sequential(nn.Conv2d(dim*4, 64, 1), nn.GroupNorm(8,64), nn.GELU(),
                                     nn.Conv2d(64,64,3,padding=1), nn.GroupNorm(8,64), nn.GELU())
        self.local = nn.Sequential(nn.Conv2d(15,32,3,padding=1),nn.GroupNorm(8,32),nn.GELU())
        self.head = nn.Sequential(nn.Conv2d(96,32,3,padding=1),nn.GroupNorm(8,32),nn.GELU(),nn.Conv2d(32,1,1))

    def forward(self, x):
        outputs = self.backbone({"S1GRD": x[:, :2], "S2L2A": x[:, 2:14]})
        h, w = x.shape[-2:]
        spatial = [outputs[i].transpose(1,2).reshape(x.shape[0], -1, h//16, w//16) for i in [2,5,8,11]]
        context = F.interpolate(self.context(torch.cat(spatial,dim=1)), size=(h,w), mode="bilinear", align_corners=False)
        return self.head(torch.cat([context,self.local(x)],dim=1))[:,0]


def loss_fn(z, target, valid):
    valid = valid.float()
    bce = (F.binary_cross_entropy_with_logits(z, target, reduction="none")*valid).sum()/valid.sum().clamp_min(1)
    p = z.sigmoid()*valid
    target = target*valid
    dice = 1-(2*(p*target).sum()+1)/(p.sum()+target.sum()+1)
    return bce+dice


def batch(arrays, indices, rng, device):
    xs, ys, vs = [], [], []
    c = CONFIG["crop"]
    for idx in rng.choice(indices, CONFIG["batch"]):
        y, x = rng.integers(0, 512-c+1, 2)
        k, flip = int(rng.integers(4)), bool(rng.integers(2))
        xx = arrays["x"][idx,:,y:y+c,x:x+c].astype(np.float32).copy()
        yy, vv = arrays["target"][idx,y:y+c,x:x+c], arrays["valid"][idx,y:y+c,x:x+c]
        if rng.random() < CONFIG["optical_dropout"]:
            xx[2:] = 0
        values = [np.rot90(a,k,axes=(-2,-1)) for a in (xx,yy,vv)]
        if flip:
            values = [np.flip(a,axis=-1) for a in values]
        xs.append(values[0]);ys.append(values[1]);vs.append(values[2])
    return [torch.from_numpy(np.stack(a).astype(np.float32)).to(device) for a in (xs,ys,vs)]


def run_fold(k, names, fold_of, arrays, device):
    folder = ROOT/f"fold{k}"
    folder.mkdir(parents=True, exist_ok=True)
    checkpoint = folder/"model.safetensors"
    training = [i for i,n in enumerate(names) if fold_of[n] != k]
    scoring = [i for i,n in enumerate(names) if fold_of[n] == k]
    torch.manual_seed(CONFIG["seed"]+k)
    rng = np.random.default_rng(CONFIG["seed"]+k)
    model = Segmenter(pretrained=not checkpoint.exists()).to(device)
    if checkpoint.exists():
        model.load_state_dict(load_file(str(checkpoint)))
    else:
        heads = [p for n,p in model.named_parameters() if not n.startswith("backbone.")]
        optimizer = torch.optim.AdamW([{"params": model.backbone.parameters(),"lr": CONFIG["backbone_lr"]},
                                        {"params": heads,"lr": CONFIG["head_lr"]}], weight_decay=CONFIG["weight_decay"])
        start_epoch, history = 0, []
        resume = folder/"resume.pt"
        if resume.exists():
            state = torch.load(resume,map_location=device,weights_only=True)
            model.load_state_dict(state["model"]);optimizer.load_state_dict(state["optimizer"])
            rng.bit_generator.state = state["rng"]
            start_epoch,history = state["epoch"],state["history"]
        steps = math.ceil(len(training)*CONFIG["crops_per_chip"]/CONFIG["batch"])
        model.train()
        for epoch in range(start_epoch,CONFIG["epochs"]):
            start = time.monotonic();losses=[]
            factor = .1+.9*(1+math.cos(math.pi*epoch/CONFIG["epochs"]))/2
            for group,lr in zip(optimizer.param_groups,[CONFIG["backbone_lr"],CONFIG["head_lr"]]):
                group["lr"] = lr*factor
            for _ in range(steps):
                x,y,v = batch(arrays,training,rng,device)
                optimizer.zero_grad(set_to_none=True)
                loss = loss_fn(model(x),y,v)
                if not torch.isfinite(loss):
                    raise FloatingPointError("nonfinite training loss")
                loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1.0);optimizer.step()
                losses.append(float(loss.detach().cpu()))
            row={"fold":k,"epoch":epoch+1,"loss":float(np.mean(losses)),"seconds":time.monotonic()-start}
            history.append(row);print(json.dumps(row),flush=True)
            torch.save({"model":model.state_dict(),"optimizer":optimizer.state_dict(),"rng":rng.bit_generator.state,
                        "epoch":epoch+1,"history":history},resume.with_suffix(".tmp"))
            resume.with_suffix(".tmp").replace(resume)
        save_file({n:p.detach().cpu().contiguous() for n,p in model.state_dict().items()},str(checkpoint))
        (folder/"training.json").write_text(json.dumps({"train":[names[i] for i in training],"outer":[names[i] for i in scoring],"history":history},indent=2))
        del optimizer
    model.eval()
    out = ROOT/"oof";out.mkdir(exist_ok=True)
    for i in scoring:
        destination = out/(names[i]+".npz")
        if destination.exists():continue
        x = torch.from_numpy(arrays["x"][i:i+1].astype(np.float32)).to(device)
        with torch.inference_mode():
            p = model(x).sigmoid()[0].cpu().numpy()
        np.savez_compressed(destination,probability=p,name=names[i],fold=k)
    del model
    gc.collect()
    if device == "mps":torch.mps.empty_cache()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folds",nargs="+",type=int,choices=list(range(5)),default=list(range(5)))
    args=parser.parse_args()
    torch.set_num_threads(4)
    fold_of=development();names=sorted(fold_of)
    ROOT.mkdir(parents=True,exist_ok=True)
    signature={"config":CONFIG,"source_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "pretrained_sha256":PRETRAINED_SHA,"folds":fold_of,
               "acquisition_sha256":hashlib.sha256((DATA/"acquisition.json").read_bytes()).hexdigest()}
    path=ROOT/"signature.json"
    if path.exists() and json.loads(path.read_text())!=signature:raise ValueError("experiment signature changed")
    path.write_text(json.dumps(signature,indent=2))
    prepare_cache(names)
    arrays={k:np.load(CACHE/(k+".npy"),mmap_mode="r") for k in ("x","target","valid")}
    device="mps" if torch.backends.mps.is_available() else "cpu"
    print("device",device,flush=True)
    for k in args.folds:run_fold(k,names,fold_of,arrays,device)


if __name__=="__main__":main()
