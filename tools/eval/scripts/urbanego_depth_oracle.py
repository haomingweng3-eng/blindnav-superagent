import json, time, statistics
from pathlib import Path
import numpy as np
from PIL import Image
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from urbanego_obstacle import load_gt, gt_blocking, RUN
ROOT=Path(__file__).resolve().parents[1]
frames,dets=load_gt()
rows=[]
for line in (RUN/'depth_frames.jsonl').open():
    r=json.loads(line); t=float(r['ts_unix_ns'])/1e9
    # depth_frames t is relative? use nearest by frame count mapping via ts is costly; use frame/seq to approximate 5Hz
    # nearest detections by t using frames.jsonl timestamp is unavailable here; use frame index -> detections frame nearest seq
    pass
# simpler: use rgb_frames timestamps and detections timestamps
rgb=[json.loads(x) for x in (RUN/'rgb_frames.jsonl').open()]
det=[json.loads(x) for x in (RUN/'yolo/detections.jsonl').open()]
# Build detections by rounded absolute t from rgb metadata nearest? det t is relative; use det t directly.
# depth_frames seq and rgb_frames seq share host time; join depth seq to nearest rgb ts, then det by rgb frame number.
depth=[json.loads(x) for x in (RUN/'depth_frames.jsonl').open()]
rgb_by_frame={r['frame']:r for r in rgb}
det_by_frame={}
for d in det: det_by_frame.setdefault(d['frame'],[]).append(d)
# Map depth seq to nearest rgb frame by ts_unix_ns
import bisect
rt=[r['ts_unix_ns'] for r in rgb]
for d in depth[::10]:
    i=bisect.bisect_left(rt,d['ts_unix_ns'])
    cand=[j for j in (i-2,i-1,i,i+1) if 0<=j<len(rgb)]
    j=min(cand,key=lambda k:abs(rt[k]-d['ts_unix_ns']))
    if abs(rt[j]-d['ts_unix_ns'])>300_000_000: continue
    frame_rgb=rgb[j]['frame']
    recs=det_by_frame.get(frame_rgb,[])
    gt=bool(gt_blocking(recs))
    fp=RUN/'depth'/f"{d['frame']:06d}.png"
    if not fp.exists(): continue
    a=np.array(Image.open(fp)).astype(np.float32)
    h,w=a.shape; roi=a[int(h*.55):int(h*.98), int(w*.2):int(w*.8)]
    valid=roi[roi>0]/1000.0
    rows.append({'t':d['ts_unix_ns'],'gt':gt,'p10':float(np.percentile(valid,10)) if valid.size else 99,
                 'min':float(valid.min()) if valid.size else 99,'frac15':float((valid<1.5).mean()) if valid.size else 0,
                 'frac20':float((valid<2.0).mean()) if valid.size else 0,'frac30':float((valid<3.0).mean()) if valid.size else 0})
res={}
for key,thr in [('frac15',0.05),('frac20',0.05),('frac30',0.05)]:
    occ=[(r['gt'], r[key]>=thr) for r in rows]
    tp=sum(1 for g,p in occ if g and p); fp=sum(1 for g,p in occ if not g and p); fn=sum(1 for g,p in occ if g and not p)
    res[key]={'threshold':thr,'frames':len(occ),'gt_blocking':sum(g for g,_ in occ),
              'recall':round(tp/max(1,tp+fn),4),'precision':round(tp/max(1,tp+fp),4),
              'false_positive_rate':round(fp/max(1,len(occ)-sum(g for g,_ in occ)),4)}
out=ROOT/'results'; out.mkdir(exist_ok=True)
(out/'urbanego_depth_oracle.json').write_text(json.dumps(res,indent=1)); print(json.dumps(res,indent=1))
