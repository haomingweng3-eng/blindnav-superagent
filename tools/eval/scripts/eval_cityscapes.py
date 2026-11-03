import io,json,os,statistics
import numpy as np, pyarrow.parquet as pq, torch, torch.nn.functional as F
from PIL import Image
from transformers.models.segformer import SegformerForSemanticSegmentation
from eval_segformer_v2 import load_image, metrics
ROOT=__import__('pathlib').Path(__file__).resolve().parents[1]
t=pq.ParquetFile(ROOT/'data/cityscapes/test0.parquet').read_row_group(0).to_pydict()
n=min(100,len(t['image']))
def gt_remap(a):
 a=a[:,:,0]; out=np.full(a.shape,4,np.uint8)
 out[a==1]=1; out[a==0]=3; out[a==9]=3; out[a==255]=0
 return out
city=SegformerForSemanticSegmentation.from_pretrained('nvidia/segformer-b0-finetuned-cityscapes-1024-1024').eval()
CKPT=os.getenv('CKPT','segformer_sanpo_b0_v2.pt'); SUFFIX=os.getenv('SUFFIX','_v2'); LABEL=os.getenv('LABEL','SANPO FT v2')
ck=torch.load(ROOT/'models'/CKPT,map_location='cpu')
fine=SegformerForSemanticSegmentation.from_pretrained(ck['model'],num_labels=5,ignore_mismatched_sizes=True).eval(); fine.load_state_dict(ck['state_dict'])
def run(model,mode,w,h):
 rows=[]
 for i in range(n):
  im=Image.open(io.BytesIO(t['image'][i]['bytes'])).convert('RGB'); gt=gt_remap(np.array(Image.open(io.BytesIO(t['semantic_segmentation'][i]['bytes']))))
  x=load_image_from(im,w,h)
  with torch.no_grad():
   o=model(pixel_values=x).logits; o=F.interpolate(o,size=gt.shape,mode='bilinear',align_corners=False); pred=o.argmax(1)[0].numpy()
  if mode=='city':
   r=np.full(pred.shape,4,np.uint8); r[pred==1]=1; r[np.isin(pred,[0,9])]=3; pred=r
  v,iou,rec,curb,swc,rfs,off,gtoff=metrics(pred,gt)
  rows.append({'valid':v,'iou':iou,'rec':rec,'swc':swc,'rfs':rfs})
 def mean(k):
  vals=[r[k] for r in rows if r[k] is not None]; return round(float(np.mean(vals)),4) if vals else None
 return {'images':n,'valid_rate':mean('valid'),'safe_iou':mean('iou'),'safe_recall':mean('rec'),'sidewalk_as_conditional':mean('swc'),'road_false_safe':mean('rfs')}
def load_image_from(im,w,h):
 im=im.resize((w,h),Image.BILINEAR); x=np.asarray(im,np.float32)/255
 x=(x-np.array([.485,.456,.406],np.float32))/np.array([.229,.224,.225],np.float32)
 return torch.from_numpy(x.transpose(2,0,1))[None]
res=[{'name':'cityscapes_city','model':'Cityscapes B0',**run(city,'city',512,512)},
     {'name':f'cityscapes_ft{SUFFIX}','model':LABEL,**run(fine,'fine',512,288)}]
print(json.dumps(res,indent=1)); json.dump(res,open(ROOT/f'results/cityscapes{SUFFIX}.json','w'),indent=1)
