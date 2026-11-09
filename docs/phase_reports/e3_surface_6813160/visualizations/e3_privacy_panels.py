"""Create privacy-protected diagnostics; ground dots are sampled inputs, not map cells."""
import argparse,json,sys
from pathlib import Path
import cv2,numpy as np
p=argparse.ArgumentParser();p.add_argument('--capture-dir',required=True,type=Path);p.add_argument('--pc-dir',required=True,type=Path);p.add_argument('--output-dir',required=True,type=Path);args=p.parse_args();sys.path.insert(0,str(args.pc_dir.resolve()))
from blindnav_pc.bev import depth_to_level_points
args.output_dir.mkdir(parents=True,exist_ok=True)
for meta in sorted(args.capture_dir.glob('*.json')):
 d=json.loads(meta.read_text()); src=meta.with_suffix('.png'); raw=cv2.imread(str(src)); a=np.load(meta.with_suffix('.npz'))
 if raw is None or raw.shape[:2]!=(960,1280): raise ValueError(f'missing 4-panel capture {src}')
 rgb=raw[:480,:640].copy();h,w=a['depth_m'].shape
 # Do not retain the original RGB panel; pixelate first, then add diagnostic marks.
 rgb=cv2.resize(cv2.resize(rgb,(32,24),interpolation=cv2.INTER_AREA),(640,480),interpolation=cv2.INTER_NEAREST)
 _,_,height,(vs,us)=depth_to_level_points(a['depth_m'],a['K'],a['up'],float(a['camera_height_m']),stride=4)
 keep=np.isfinite(height)&(np.abs(height)<=0.15); roles=a['roles'][vs,us]
 palette=np.array([[100,100,100],[60,180,60],[0,165,255],[50,50,230]],np.uint8)
 # Saved maps may differ in size from the display panel.
 xx=np.minimum(639,(us[keep]*640/w).astype(int));yy=np.minimum(479,(vs[keep]*480/h).astype(int));rgb[yy,xx]=palette[np.clip(roles[keep],0,3).astype(np.uint8)]
 cv2.rectangle(rgb,(0,0),(639,30),(0,0,0),-1);cv2.putText(rgb,'PIXELATED RGB + ground-band input pixels',(5,20),cv2.FONT_HERSHEY_SIMPLEX,.48,(255,255,255),1,cv2.LINE_AA)
 result=raw.copy();result[:480,:640]=rgb
 target=args.output_dir/(meta.stem+'_privacy.png');cv2.imwrite(str(target),result)
 print(d['session'],d['frame'],'samples',int(keep.sum()),'output',target)
