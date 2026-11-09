import sys, json, time, traceback
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path('pc/tools').resolve()))
from joint_route_candidate_eval import SharedDepth, sha256_file, write_json
from eval.sanpo import SanpoSession
snapshot=Path('/workspace/scratch/09dfb5545ec7/blindnav_models/dav2_5426e4f')
data=Path('/workspace/scratch/09dfb5545ec7/blindnav_data/sanpo-real')
out=Path('pc/out/joint_route_public_depth_precompute_20261109')
out.mkdir(parents=True, exist_ok=False)
args=SimpleNamespace(device='cpu', torch_threads=4, depth_input_size=None,
    depth_snapshot=str(snapshot), depth_revision='5426e4f0f36572d16453bbda7a8389317b1bef99',
    inference_cache='/workspace/scratch/09dfb5545ec7/blindnav_data/dav2_cache')
model_inventory=[{'path':str(p.relative_to(snapshot)), 'size':p.stat().st_size,
                  'sha256':sha256_file(p)} for p in sorted(snapshot.rglob('*')) if p.is_file()]
runner=SharedDepth(args,model_inventory)
records=[]
started=time.perf_counter()
status='FAILED'
try:
    for folder in sorted(data.iterdir()):
        if not (folder/'description.json').exists(): continue
        session=SanpoSession(str(folder),'camera_chest',max_width=640)
        for frame in session.available_frames():
            inp=session.inputs(frame)
            q,signature,source,duration=runner.predict(session,frame,inp.bgr)
            record={'session':folder.name,'frame':frame,'shape':list(q.shape),
                    'inference_seconds':duration,'source':source,'signature':signature,
                    'q_min':float(q.min()),'q_max':float(q.max())}
            records.append(record)
            print(json.dumps(record),flush=True)
            write_json(out/'per_frame_inference.json',records)
    if len(records)!=36: raise AssertionError(f'expected36, got{len(records)}')
    status='COMPLETED'
except Exception:
    (out/'error.txt').write_text(traceback.format_exc())
    raise
finally:
    write_json(out/'run_metadata.json',{'status':status,'frames':len(records),
        'elapsed_seconds':time.perf_counter()-started,
        'model_load_seconds':runner.model_load_seconds,'fresh_inference_calls':runner.calls,
        'verified_cache_hits':runner.cache_hits,'model_inventory':model_inventory,
        'torch_threads':args.torch_threads,'depth_input_size_override':None,
        'data_kind':'SANPO-real public RGB','semantics_used_for_this_precompute':False})
