import json,math,cv2,numpy as np,os
from pathlib import Path
root=Path(os.environ['SANPO_REAL']); folder=Path(os.environ['E3_CAPTURE_DIR']); records={}
with open('pc/out/e3_surface_6813160_on/rays.jsonl') as f:
 for line in f:
  d=json.loads(line); records[(d['session'],d['frame'])]=d
for j in folder.glob('*.json'):
 d=json.loads(j.read_text()); sid,frame=d['session'],d['frame']; a=np.load(j.with_suffix('.npz')); rgb=cv2.imread(str(root/sid/'camera_chest/left/video_frames'/f'{frame:06}.png')); rgb=cv2.resize(rgb,(640,480)); roles=a['roles']; depth=a['depth_m']; pal=np.array([[100,100,100],[60,180,60],[0,165,255],[50,50,230]],np.uint8); seg=pal[np.clip(roles,0,3).astype(np.uint8)]; seg=cv2.resize(seg,(640,480),interpolation=cv2.INTER_NEAREST); z=np.nan_to_num(depth,nan=0,posinf=0,neginf=0); heat=cv2.applyColorMap(np.uint8(np.clip(z/15,0,1)*255),cv2.COLORMAP_TURBO); heat=cv2.resize(heat,(640,480)); grid=np.full((480,640,3),245,np.uint8); ox,oy,scale=320,440,22; px,pz,yaw=d['pose']; c,s=math.cos(yaw),math.sin(yaw)
 for cell in d['surface']['cells']:
  dx,dz=cell['x_m']-px,cell['z_m']-pz; x=dx*c-dz*s; z=dx*s+dz*c; u,v=round(ox+x*scale),round(oy-z*scale)
  if 0<=u<640 and 0<=v<480: cv2.rectangle(grid,(u-2,v-2),(u+2,v+2),pal[cell['role']].tolist(),-1)
 cv2.circle(grid,(ox,oy),5,(0,0,0),-1)
 trace=records[(sid,frame)]; best=next((r for r in trace['rays'] if r['best_rejected_diagnostic']),None); selected=next((r for r in trace['rays'] if r['selected_by_original_algorithm']),None)
 for ray,col in [(best,(0,0,255)),(selected,(255,0,0))]:
  if ray:
   angle=ray['angle_rad']; length=ray['termination_distance_m']; cv2.line(grid,(ox,oy),(round(ox+math.sin(angle)*length*scale),round(oy-math.cos(angle)*length*scale)),col,2)
 for img,label in [(rgb,'RGB'),(seg,'Predicted roles'),(heat,'DAV2 calibrated depth 0-15m'),(grid,'Surface grid, red=best rejected, blue=selected')]: cv2.rectangle(img,(0,0),(640,28),(0,0,0),-1);cv2.putText(img,label,(8,20),cv2.FONT_HERSHEY_SIMPLEX,.55,(255,255,255),1)
 cv2.imwrite(str(j.with_suffix('.png')),np.vstack([np.hstack([rgb,seg]),np.hstack([heat,grid])]))
 print(sid,frame,'best',bool(best),'selected',bool(selected))
