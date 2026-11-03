import json,csv,statistics
from pathlib import Path
from PIL import Image
from ultralytics import YOLO
ROOT=Path(__file__).resolve().parents[1]; DATA=ROOT/'data/openimages'
by=json.load(open(DATA/'door_boxes.json'))
ids=[i for i in by if (DATA/'images'/f'{i}.jpg').exists()]
model=YOLO(str(ROOT/'yolov8s-worldv2.pt')); model.set_classes(['door','doorway','glass door','entrance'])
def iou(a,b):
 ax1,ay1,ax2,ay2=a; bx1,by1,bx2,by2=b
 iw=max(0,min(ax2,bx2)-max(ax1,bx1)); ih=max(0,min(ay2,by2)-max(ay1,by1))
 inter=iw*ih; uni=(ax2-ax1)*(ay2-ay1)+(bx2-bx1)*(by2-by1)-inter
 return inter/uni if uni>0 else 0
res={}
for conf in [0.05,0.10,0.15,0.25]:
 tp03=tp05=ngt=0; cands=[]; imgs=[]
 for i in ids:
  im=Image.open(DATA/'images'/f'{i}.jpg'); w,h=im.size
  gt=[]
  for r in by[i]:
   gt.append((float(r['XMin'])*w,float(r['YMin'])*h,float(r['XMax'])*w,float(r['YMax'])*h))
  ngt+=len(gt)
  r=model.predict(str(DATA/'images'/f'{i}.jpg'),imgsz=640,conf=conf,verbose=False)[0]
  boxes=[tuple(map(float,b)) for b in r.boxes.xyxy.tolist()]
  cands.append(len(boxes)); imgs.append(len(gt))
  for g in gt:
   best=max([iou(g,b) for b in boxes],default=0)
   tp03+=best>=0.3; tp05+=best>=0.5
 res[str(conf)]={'images':len(ids),'gt_boxes':ngt,'recall_iou03':round(tp03/ngt,4),'recall_iou05':round(tp05/ngt,4),
  'mean_candidates':round(statistics.mean(cands),2),'median_candidates':statistics.median(cands),
  'candidate_count_std':round(statistics.pstdev(cands),2),'avg_gt_per_image':round(statistics.mean(imgs),2)}
out=ROOT/'results'; out.mkdir(exist_ok=True); (out/'openimages_door.json').write_text(json.dumps(res,indent=1)); print(json.dumps(res,indent=1))
