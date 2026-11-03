import json, statistics, time, torch
from pathlib import Path
from torchvision.models.segmentation import deeplabv3_mobilenet_v3_large
from eval_segformer_v2 import DATA, FROZEN, load_image, gt_remap, metrics
from PIL import Image
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
m=deeplabv3_mobilenet_v3_large(weights=None,num_classes=5,aux_loss=True)
ck=torch.load(ROOT/'models/deeplab_mbv3_sanpo.pt',map_location='cpu'); m.load_state_dict(ck['state_dict']); m.eval()
def run(sessions):
 rows=[]; times=[]
 for sid in sessions:
  for fp in sorted((DATA/sid/'video_frames').glob('*.png'))[:20]:
   mp=DATA/sid/'segmentation_masks'/fp.name
   if not mp.exists(): continue
   gt=gt_remap(np.array(Image.open(mp))[:,:,0]); x=load_image(fp,512,288)
   t=time.time()
   with torch.no_grad():
    o=m(x)['out']; o=torch.nn.functional.interpolate(o,size=gt.shape,mode='bilinear',align_corners=False); pred=o.argmax(1)[0].numpy()
   times.append((time.time()-t)*1000)
   v,iou,rec,curb,swc,rfs,off,gtoff=metrics(pred,gt)
   rows.append({'valid':v,'safe_iou':iou,'safe_recall':rec,'curb_recall':curb,'sidewalk_as_cond':swc,'road_false_safe':rfs,'off':off,'gt_off':gtoff})
 def mean(k):
  vals=[r[k] for r in rows if r[k] is not None]; return round(float(np.mean(vals)),4) if vals else None
 errs=[abs(r['off']-r['gt_off']) for r in rows if r['off'] is not None and r['gt_off'] is not None]
 return {'frames':len(rows),'valid_rate':mean('valid'),'safe_iou':mean('safe_iou'),'safe_recall':mean('safe_recall'),
 'curb_recall':mean('curb_recall'),'sidewalk_as_conditional':mean('sidewalk_as_cond'),'road_false_safe':mean('road_false_safe'),
 'center_error_median':round(float(statistics.median(errs)),4) if errs else None,
 'center_error_p90':round(float(np.percentile(errs,90)),4) if errs else None,
 'latency_ms_median':round(float(statistics.median(times)),1) if times else None}
sessions=[p.name for p in sorted(DATA.iterdir()) if p.is_dir()]
res=[{'name':'frozen45_deeplab',**run(FROZEN)},{'name':'real_v2_deeplab',**run([s for s in sessions if s not in FROZEN][21:27])}]
print(json.dumps(res,indent=1)); json.dump(res,open(ROOT/'results/deeplab_v2_summary.json','w'),indent=1)
