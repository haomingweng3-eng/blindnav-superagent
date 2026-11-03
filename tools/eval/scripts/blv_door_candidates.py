import json,re,csv,statistics
from pathlib import Path
from ultralytics import YOLO
ROOT=Path(__file__).resolve().parents[1]; REPO=ROOT/'data/BLV-Road-Nav-Accessibility'
labels={}
for p in (REPO/'Ground Truth Annotation').glob('*.csv'):
 m=re.match(r'video-(\d+)-segment-(\d+)',p.stem)
 if not m: continue
 rows=list(csv.reader(p.open(encoding='utf-8',errors='replace'))); hdr=rows[0]
 for row in rows[1:]:
  if not row: continue
  obj=row[0].strip().strip('"')
  if obj!='Doorway': continue
  for col,val in zip(hdr[1:],row[1:]):
   if str(val).strip() in ('1','1.0'):
    fm=re.match(r'frame_(\d+)',col)
    if fm: labels[(int(m.group(1)),int(m.group(2)),int(fm.group(1)))]=1
paths=[]
for fp in sorted((REPO/'Video Frames').glob('*.jpeg')):
 m=re.search(r'video-(\d+)-segment-(\d+)-frame-(\d+)',fp.name)
 if m: paths.append(fp)
model=YOLO(str(ROOT/'yolov8s-worldv2.pt')); model.set_classes(['door','doorway','glass door','entrance'])
counts=[]; lat=[]
for i in range(0,len(paths),16):
 chunk=paths[i:i+16]; import time; t=time.time()
 rs=model.predict([str(p) for p in chunk],imgsz=640,conf=0.15,verbose=False); lat.append((time.time()-t)/len(chunk)*1000)
 for r in rs: counts.append(len(r.boxes))
summary={'dataset':'BLV doorway frames','frames':len(paths),'mean_candidates':round(statistics.mean(counts),2) if counts else None,
 'median_candidates':statistics.median(counts) if counts else None,'p90_candidates':sorted(counts)[int(.9*(len(counts)-1))] if counts else None,
 'candidate_count_std':round(statistics.pstdev(counts),2) if len(counts)>1 else None,
 'latency_ms_median':round(statistics.median(lat),1) if lat else None,
 'recall_iou':'UNAVAILABLE: no per-door bounding boxes in BLV labels'}
out=ROOT/'results'; out.mkdir(exist_ok=True); (out/'blv_door_candidates.json').write_text(json.dumps(summary,indent=1)); print(json.dumps(summary,indent=1))
