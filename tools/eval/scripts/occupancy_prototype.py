import json,bisect,time
from pathlib import Path
import numpy as np, torch, torch.nn.functional as F
from PIL import Image
from transformers.models.segformer import SegformerForSemanticSegmentation
import sys; sys.path.insert(0,str(Path(__file__).resolve().parent))
from eval_segformer_v2 import load_image
from urbanego_obstacle import load_gt, gt_blocking, RUN
ROOT=Path(__file__).resolve().parents[1]
ck=torch.load(ROOT/'models/segformer_sanpo_b0_v2.pt',map_location='cpu')
model=SegformerForSemanticSegmentation.from_pretrained(ck['model'],num_labels=5,ignore_mismatched_sizes=True).eval(); model.load_state_dict(ck['state_dict'])
frames,dets=load_gt()
rgb=[json.loads(x) for x in (RUN/'rgb_frames.jsonl').open()]
depth=[json.loads(x) for x in (RUN/'depth_frames.jsonl').open()]
first=rgb[0]['ts_unix_ns']; dts=[d['ts_unix_ns'] for d in depth]
def nearest_depth(ts):
 i=bisect.bisect_left(dts,ts); cand=[j for j in (i-1,i,i+1) if 0<=j<len(depth)]
 j=min(cand,key=lambda k:abs(dts[k]-ts)); return depth[j]
rows=[]
for i,fp in enumerate(sorted((RUN/'clip_375').glob('*.jpg'))):
 t=375.0+i*0.1; ts=first+int(t*1e9); d=nearest_depth(ts)
 recs=dets.get(min(dets.keys(),key=lambda k:abs(k-t)),[]) if dets else []
 gt=bool(gt_blocking(recs))
 im=Image.open(fp).convert('RGB'); x=load_image(fp,512,288)
 with torch.no_grad():
  o=model(pixel_values=x).logits; pred=o.argmax(1)[0].numpy().astype(np.uint8)
 safe=pred==1
 dp=RUN/'depth'/f"{d['frame']:06d}.png"
 if not dp.exists(): continue
 dep=np.array(Image.open(dp).resize((512,288),Image.NEAREST)).astype(np.float32)/1000.0
 h,w=safe.shape; y0,y1=int(h*.55),int(h*.98); x0,x1=int(w*.2),int(w*.8)
 roi_s=safe[y0:y1,x0:x1]; roi_d=dep[y0:y1,x0:x1]
 fg=(roi_d>0)&(roi_d<2.0)
 frac=float((fg&roi_s).sum()/max(1,roi_s.sum()))
 # instance oracle: any GT box in corridor
 inst=False
 for g in gt_blocking(recs):
  x1b,y1b,x2b,y2b=g['box']; wx1,wx2=w*.3,w*.7
  if min(x2b,wx2)>max(x1b,wx1) and y2b/720>0.58: inst=True
 rows.append({'i':i,'t':t,'gt':gt,'safe_frac':frac,'instance_oracle':inst})
# temporal persistence
for i,r in enumerate(rows):
 r['occ_depth']=r['safe_frac']>=0.05 and i>=1 and rows[i-1]['safe_frac']>=0.05
 r['occ_depth_inst']=r['occ_depth'] or r['instance_oracle']
def score(key):
 tp=sum(r['gt'] and r[key] for r in rows); fp=sum(not r['gt'] and r[key] for r in rows); fn=sum(r['gt'] and not r[key] for r in rows)
 return {'frames':len(rows),'gt_blocking':sum(r['gt'] for r in rows),'recall':round(tp/max(1,tp+fn),4),
 'precision':round(tp/max(1,tp+fp),4),'false_positive_rate':round(fp/max(1,len(rows)-sum(r['gt'] for r in rows)),4)}
res={'dataset':'UrbanEgo clip 375-400s, 10fps; SegFormer SANPO FT v2 walkable + depth<2m + 2-frame persistence',
 'depth_only_temporal':score('occ_depth'),'depth_plus_gt_instance_oracle':score('occ_depth_inst'),
 'note':'instance variant uses GT tracks; unknown-object recall unmeasured; ORACLE not production'}
out=ROOT/'results'; out.mkdir(exist_ok=True); (out/'occupancy_prototype.json').write_text(json.dumps(res,indent=1)); print(json.dumps(res,indent=1))
