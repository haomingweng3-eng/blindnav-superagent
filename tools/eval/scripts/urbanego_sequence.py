import sys, json, csv, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from urbanego_obstacle import detect, load_gt, gt_blocking, RUN
import onnxruntime as ort
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
CLIP_START=float(os.getenv('CLIP_START','530'))
CLIP_DIR=os.getenv('CLIP_DIR','clip_530')
sess=ort.InferenceSession('/Users/mima0000/blindnav-app/app/src/main/assets/yolov8n.onnx',providers=['CPUExecutionProvider'])
frames,dets=load_gt()
# clip starts at 530 s, 10 fps
stop_since=None; last_stop_at=None; rows=[]
for i,fp in enumerate(sorted((RUN/CLIP_DIR).glob('*.jpg'))):
    t=CLIP_START+i*0.1
    nearest=min(dets.keys(),key=lambda k:abs(k-t)) if dets else None
    gt=gt_blocking(dets.get(nearest,[])) if nearest is not None and abs(nearest-t)<=0.2 else []
    im=Image.open(fp).convert('RGB')
    mh=detect(sess,im)
    in_corr=[m for m in mh if m['corridor_overlap']>=0.25]
    if in_corr:
        if stop_since is None: stop_since=i
        last_stop_at=i
    else:
        stop_since=None
    fresh = stop_since is not None and (i-stop_since)>=1 and (i-stop_since)*100>=300
    hold = last_stop_at is not None and (i-last_stop_at)*100<800
    stop = fresh or (hold and last_stop_at is not None)
    rows.append({'i':i,'t':round(t,1),'gt':int(bool(gt)),'model':int(stop),'hits':len(in_corr),
                 'gt_names':'|'.join(g['name'] for g in gt),'model_names':'|'.join(m['name'] for m in in_corr)})
out=ROOT/'results'; out.mkdir(exist_ok=True)
with (out/f'urbanego_sequence_{int(CLIP_START)}.csv').open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
tp=sum(1 for r in rows if r['gt'] and r['model']); fp=sum(1 for r in rows if not r['gt'] and r['model']); fn=sum(1 for r in rows if r['gt'] and not r['model'])
# segments
segments=[]; start=None
for r in rows:
    if r['gt'] and start is None: start=r['i']
    if not r['gt'] and start is not None: segments.append((start,r['i']-1)); start=None
if start is not None: segments.append((start,rows[-1]['i']))
t2s=[]; rec=[]
for a,b in segments:
    first=next((r['i'] for r in rows if a<=r['i']<=b and r['model']),None)
    if first is not None: t2s.append((first-a)*100)
    # recovery after last GT frame in segment: first clear after b
    after=next((r['i'] for r in rows if r['i']>b and not r['model']),None)
    if after is not None: rec.append((after-b)*100)
summary={'dataset':f'UrbanEgo run7 clip {CLIP_START}-{CLIP_START+25}s, 10fps','frames':len(rows),
 'gt_blocking_frames':sum(r['gt'] for r in rows),'model_stop_frames':sum(r['model'] for r in rows),
 'frame_tp':tp,'frame_fp':fp,'frame_fn':fn,
 'time_to_stop_ms':t2s,'recovery_ms':rec,
 'stop_flaps':sum(1 for a,b in zip(rows,rows[1:]) if a['model']!=b['model'])}
(ROOT/'results'/f'urbanego_sequence_summary_{int(CLIP_START)}.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print(json.dumps(summary,ensure_ascii=False,indent=2))
