import json,statistics
from pathlib import Path
from PIL import Image
from ultralytics import YOLO
ROOT=Path(__file__).resolve().parents[1]; DATA=ROOT/'data/openimages'
by=json.load(open(DATA/'door_boxes.json')); ids=[i for i in by if (DATA/'images'/f'{i}.jpg').exists()]
m=YOLO(str(ROOT/'yolov8s-worldv2.pt')); m.set_classes(['door','doorway','glass door','entrance'])
def boxes(im):
 r=m.predict(im,imgsz=640,conf=0.05,verbose=False)[0]
 return [tuple(map(float,b)) for b in r.boxes.xyxy.tolist()]
def iou(a,b):
 ax1,ay1,ax2,ay2=a; bx1,by1,bx2,by2=b
 iw=max(0,min(ax2,bx2)-max(ax1,bx1)); ih=max(0,min(ay2,by2)-max(ay1,by1)); inter=iw*ih
 uni=(ax2-ax1)*(ay2-ay1)+(bx2-bx1)*(by2-by1)-inter
 return inter/uni if uni>0 else 0
def perturb(im,dx,dy,scale):
 w,h=im.size; cx,cy=w/2,h/2; nw,nh=int(w*scale),int(h*scale)
 im2=im.resize((nw,nh)); canvas=Image.new('RGB',(w,h)); canvas.paste(im2,(int(cx-nw/2+dx*w),int(cy-nh/2+dy*h)))
 return canvas
stabs=[]; counts=[]; nimg=0
for i in ids:
 im=Image.open(DATA/'images'/f'{i}.jpg').convert('RGB'); base=boxes(im)
 if not base: continue
 nimg+=1; cs=[len(base)]
 for dx,dy,s in [(0.02,0,1),(0,0.02,1),(0,0,0.98)]:
  bs=boxes(perturb(im,dx,dy,s)); cs.append(len(bs))
  for b in base:
   best=max([iou(b,c) for c in bs],default=0)
   if best>0: stabs.append(best)
 counts.extend(cs)
out=ROOT/'results'; out.mkdir(exist_ok=True)
summary={'dataset':'OpenImages door, controlled perturbation','images_with_candidates':nimg,
 'perturbations':'shift +2% x, shift +2% y, scale 0.98x','matched_candidate_iou_mean':round(statistics.mean(stabs),4) if stabs else None,
 'match_rate':round(len(stabs)/max(1,nimg*3*1),4),
 'candidate_count_std_over_variants':round(statistics.pstdev(counts),3) if len(counts)>1 else None,
 'note':'controlled perturbation stability, not temporal video stability'}
(out/'door_stability.json').write_text(json.dumps(summary,indent=1)); print(json.dumps(summary,indent=1))
