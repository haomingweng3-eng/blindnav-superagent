import csv,json,hashlib,sys,collections,os
from pathlib import Path
if len(sys.argv)!=5: sys.exit('usage: python /tmp/e3_independent_check.py OFF/per_frame.csv ON/per_frame.csv ON/rays.jsonl input_inventory.json')
off,on,trace,inventory=map(Path,sys.argv[1:])
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''): h.update(b)
 return h.hexdigest()
def read(p):
 with p.open(newline='') as f: rows=list(csv.DictReader(f))
 keys=[(r['condition'],r['arm'],r['session'],r['frame']) for r in rows]
 if len(set(keys))!=len(keys): raise ValueError('duplicate frame keys '+str(p))
 return dict(zip(keys,rows)),keys
A,ka=read(off); B,kb=read(on)
fields=['surface_state','occupancy_state','safety_state','risk_state','effective_command','authorized_motion','guidance','guidance_command','observation_hint','reason','forward_guidance','timestamp_ms','relative_time_ms','pose_source','depth_source','code_commit','input_manifest_sha256']
missing=[x for x in fields if x not in next(iter(A.values())) or x not in next(iter(B.values()))]
diffs=collections.Counter(); samples=[]
for k in A.keys() & B.keys():
 for f in fields:
  if A[k].get(f)!=B[k].get(f):
   diffs[f]+=1
   if len(samples)<20:samples.append([k,f,A[k].get(f),B[k].get(f)])
t=[]
with trace.open() as f:
 for l in f:t.append(json.loads(l))
tkeys=[(x['condition'],x['arm'],x['session'],str(x['frame'])) for x in t]
candidates={k for k in B if k[1]=='mobilio'}
statuses=collections.Counter(x['trace_status'] for x in t)
raycounts=collections.Counter(len(x['rays']) for x in t)
traceids=collections.Counter((x.get('code_commit'),x.get('input_manifest_sha256')) for x in t)
def truth(v):return str(v).lower() in ('true','1')
metrics={}
for arm in sorted({k[1] for k in B}):
 rows=[r for k,r in B.items() if k[1]==arm]
 metrics[arm]={'n':len(rows),'danger':sum(truth(r.get('danger_any')) for r in rows),'confirmed_clear':sum(truth(r.get('confirmed_clear')) for r in rows),'any_miss':sum(truth(r.get('any_miss')) for r in rows),'true_false_stop':sum(truth(r.get('true_false_stop')) for r in rows),'states':dict(collections.Counter(r.get('safety_state') for r in rows))}
result={'sha256':{str(p):sha(p) for p in (off,on,trace,inventory)},'off_rows':len(A),'on_rows':len(B),'frame_keys_equal':set(ka)==set(kb),'frame_order_equal':ka==kb,'missing_fields':missing,'differences':dict(diffs),'difference_examples':samples,'trace_rows':len(t),'trace_duplicate_keys':len(tkeys)-len(set(tkeys)),'trace_key_coverage_equal':set(tkeys)==candidates,'trace_status':dict(statuses),'ray_counts':dict(raycounts),'trace_commit_manifest_pairs':[{ 'commit':k[0], 'manifest':k[1], 'count':v} for k,v in traceids.items()],'trace_manifest_matches_inventory':all(k[1]==sha(inventory) for k in traceids),'on_metrics':metrics,'note':'Counts are proxy labels; UNKNOWN is not a warning; no direction accuracy or TTS validation.'}
print(json.dumps(result,indent=2,ensure_ascii=False))
