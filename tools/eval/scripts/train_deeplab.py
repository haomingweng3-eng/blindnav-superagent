import os, torch, torch.nn.functional as F
from pathlib import Path
from torch.utils.data import DataLoader
from torchvision.models.segmentation import deeplabv3_mobilenet_v3_large, DeepLabV3_MobileNet_V3_Large_Weights
from train_sanpo_segformer import Sanpo, DATA, FROZEN, OUT

sessions=[p.name for p in sorted(DATA.iterdir()) if p.is_dir()]
train_s=Sanpo([s for s in sessions if s not in FROZEN][:21], True)
val_s=Sanpo([s for s in sessions if s not in FROZEN][21:27], False)
device=torch.device('mps' if torch.backends.mps.is_available() else 'cpu')
m=deeplabv3_mobilenet_v3_large(weights=DeepLabV3_MobileNet_V3_Large_Weights.DEFAULT)
m.classifier[4]=torch.nn.Conv2d(256,5,1); m.aux_classifier[4]=torch.nn.Conv2d(10,5,1)
m=m.to(device)
opt=torch.optim.AdamW(m.parameters(),lr=1e-4,weight_decay=0.01)
w=torch.tensor([1.0,2.5,20.0,3.0,1.0],device=device)
tl=DataLoader(train_s,batch_size=8,shuffle=True,num_workers=0); vl=DataLoader(val_s,batch_size=8)
best=1e9
for ep in range(3):
 m.train(); tot=0
 for st,(x,y) in enumerate(tl):
  x,y=x.to(device),y.to(device); opt.zero_grad()
  o=m(x)['out']; o=F.interpolate(o,size=y.shape[-2:],mode='bilinear',align_corners=False)
  loss=F.cross_entropy(o,y,weight=w,ignore_index=255); loss.backward(); opt.step(); tot+=float(loss)
  if st%20==0: print('ep',ep,'step',st,'loss',round(float(loss),4),flush=True)
 m.eval(); v=0
 with torch.no_grad():
  for x,y in vl:
   x,y=x.to(device),y.to(device); o=m(x)['out']; o=F.interpolate(o,size=y.shape[-2:],mode='bilinear',align_corners=False)
   v+=float(F.cross_entropy(o,y,weight=w,ignore_index=255))
 v/=max(1,len(vl)); print('ep',ep,'train',round(tot/max(1,len(tl)),4),'val',round(v,4),flush=True)
 if v<best: best=v; torch.save({'state_dict':m.state_dict()},OUT/'deeplab_mbv3_sanpo.pt')
print('saved',OUT/'deeplab_mbv3_sanpo.pt','best',best)
