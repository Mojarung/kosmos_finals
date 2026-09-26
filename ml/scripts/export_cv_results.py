import hashlib
import json
from pathlib import Path
import numpy as np
from floodvalue.cv import summarize_runs
from floodvalue.metrics import Confusion


def aggregate(records):
    events = {}
    total = Confusion()
    brier = ece = 0.
    for r in records:
        s = r['score']; c = s['overall']['micro']
        total += Confusion(**{k:c[k] for k in ['tp','fp','fn','tn']})
        events.update({e:v['micro']['iou'] for e,v in s['events'].items()})
        brier += s['probability']['brier'] * c['n_pixels']
        ece += s['probability']['ece'] * c['n_pixels']
    return {'folds': sorted(r['fold'] for r in records), 'event_mean_iou':float(np.mean(list(events.values()))),
            'pooled':total.as_dict(), 'brier':brier/total.n_pixels, 'weighted_fold_ece':ece/total.n_pixels, 'events':events}

models=summarize_runs(Path('runs/cv'))
blends={}
for p in sorted(Path('runs/blends').glob('*_fold*.json')):
    blends.setdefault(p.stem.rsplit('_fold',1)[0],[]).append(json.loads(p.read_text()))
calibrations={}
for p in sorted(Path('runs/calibration').glob('*_fold*.json')):
    d=json.loads(p.read_text())
    for method,r in d['results'].items():
        calibrations.setdefault(p.stem.rsplit('_fold',1)[0]+'_'+method,[]).append({'fold':d['fold'],'score':r['score']})
provenance=[]
for p in sorted(Path('runs/cv').glob('*/seed*/fold*/cv_result.json')):
    r=json.loads(p.read_text())
    item={k:r[k] for k in ['config','fold','plan_sha256','best_epoch','inner_valid_best_iou','calibration','device','training_seconds']}
    item['path']=str(p.parent)
    weight=p.parent/'model.safetensors'
    if weight.exists(): item['weights_sha256']=hashlib.sha256(weight.read_bytes()).hexdigest()
    if 'source_run' in r: item['source_run']=r['source_run']
    provenance.append(item)
result={'note':models['note'], 'models':models['runs'],
        'blends':{name:{**aggregate(rs), 'space':rs[0]['space'], 'weights':rs[0]['weights'],
                        'calibration_method':next(k for k in rs[0]['calibration'] if k!='threshold'),
                        'runs_by_fold':{str(r['fold']):r['runs'] for r in rs}}
                  for name,rs in blends.items()},
        'calibrations':{name:aggregate(rs) for name,rs in calibrations.items()},
        'provenance':provenance}
Path('research/cv_results.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
for row in result['models']:
    print(row['name'],row['seed'],row['folds'],f"macro={row['event_mean_iou']:.4f} micro={row['pooled']['iou']:.4f} Brier={row['brier']:.5f}")
for name,row in result['blends'].items():
    print('blend',name,row['folds'],f"macro={row['event_mean_iou']:.4f} micro={row['pooled']['iou']:.4f} Brier={row['brier']:.5f}")
