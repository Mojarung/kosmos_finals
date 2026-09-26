import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

r=json.loads(Path('research/cv_results.json').read_text())
rows=[x for x in r['models'] if x['folds']==[0,1,2] and not x['name'].endswith('_rot4')]
order=['control','balanced','context','lovasz','balanced_context','balanced_lovasz']
labels=['Control','Balanced\ncrops','384 px\ncontext','BCE +\nLovasz','Balanced +\ncontext','Balanced +\nLovasz']
selection=Path('runs/cv/confirmation_selection.json')
chosen=json.loads(selection.read_text())['candidate'] if selection.exists() else max(rows,key=lambda x:x['event_mean_iou'])['name'].removeprefix('cv_r34_')
fig,axes=plt.subplots(1,2,figsize=(12,4.5),constrained_layout=True)
for seed,marker,offset in [(0,'o',-.07),(1,'^',.07)]:
    points={x['name'].removeprefix('cv_r34_'):x['event_mean_iou'] for x in rows if x['seed']==seed}
    indices=[i for i,n in enumerate(order) if n in points]
    if indices: axes[0].scatter(np.array(indices)+offset,[points[order[i]] for i in indices],marker=marker,s=70,label=f'seed {seed}')
axes[0].set_xticks(range(len(order)),labels,fontsize=8)
axes[0].set_ylabel('Mean IoU across 10 held-out events')
axes[0].set_title('Local event CV: individual models')
axes[0].grid(axis='y',alpha=.25);axes[0].legend()
by_key={(x['name'],x['seed']):x for x in rows}
seeds=[s for s in [0,1] if ('cv_r34_'+chosen,s) in by_key and ('cv_r34_control',s) in by_key]
events=sorted(by_key[('cv_r34_control',0)]['events'])
deltas=np.array([[by_key[('cv_r34_'+chosen,s)]['events'][e]-by_key[('cv_r34_control',s)]['events'][e] for e in events] for s in seeds])
mean=deltas.mean(0);idx=np.argsort(mean)
axes[1].axvline(0,color='black',linewidth=.8)
axes[1].scatter(mean[idx],np.arange(len(events)),c=['#16835c' if x>=0 else '#c26928' for x in mean[idx]],s=45)
axes[1].set_yticks(range(len(events)),[events[i] for i in idx]);axes[1].grid(axis='x',alpha=.25)
axes[1].set_xlabel('Event IoU difference versus control')
axes[1].set_title(f"{chosen.replace('_',' + ')}; mean of seeds {seeds}",fontsize=10)
fig.suptitle('Development CV only; final test excluded',fontsize=11)
Path('research/figures').mkdir(exist_ok=True)
fig.savefig('research/figures/cv_comparison.png',dpi=160)
print('research/figures/cv_comparison.png')
