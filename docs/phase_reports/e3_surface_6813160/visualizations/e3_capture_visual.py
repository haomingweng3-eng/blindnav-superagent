import os, sys, json
from pathlib import Path
import numpy as np
sys.path[:0]=[os.path.abspath('pc'),os.path.abspath('pc/tools')]
import mobilio_sanpo_ab as m
from blindnav_pc.mobilio_surface import SurfaceGrid2D
out=Path('/tmp/e3_visual_capture'); out.mkdir(exist_ok=True)
targets={('-PqSDmiEe2pXjmYHgxh4YEBsj0T5LU10',75),('-PqSDmiEe2pXjmYHgxh4YEBsj0T5LU10',305),('UGNwBEZMxhzlzXVyl8XUfflcvjGwdpA5',35)}
state={'sid':None}
old_eval=m.evaluate_session
old_update=SurfaceGrid2D.update_from_frame
old_snapshot=SurfaceGrid2D.snapshot
def evalwrap(session,*args,**kw):
 state['sid']=os.path.basename(session.dir.rstrip('/'))
 return old_eval(session,*args,**kw)
def update(self,roles,depth_m,K,up,camera_height_m,pose,frame_id,pixel_stride=4):
 result=old_update(self,roles,depth_m,K,up,camera_height_m,pose,frame_id,pixel_stride)
 if (state['sid'],int(frame_id)) in targets:
  name=state['sid']+'_'+str(frame_id)
  np.savez_compressed(out/(name+'.npz'),roles=roles,depth_m=depth_m,K=K,up=up,camera_height_m=camera_height_m,pose=np.array([pose.x_m,pose.z_m,pose.yaw_rad]))
  with (out/(name+'.json')).open('w') as f: json.dump({'session':state['sid'],'frame':int(frame_id),'pose':[pose.x_m,pose.z_m,pose.yaw_rad],'surface':old_snapshot(self,pose)},f)
 return result
m.evaluate_session=evalwrap
SurfaceGrid2D.update_from_frame=update
sys.exit(m.main())
