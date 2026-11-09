import csv,json,collections,sys
from pathlib import Path
p=Path(sys.argv[1]); out=Path(sys.argv[2]); rows=list(csv.DictReader(p.open(newline='')))

def yes(r,k):
 return r.get(k,'').lower() in ('true','1')
def compute(rs):
 n=len(rs); d=sum(yes(r,'danger_any') for r in rs); c=sum(yes(r,'confirmed_clear') for r in rs)
 return {'frames':n,'danger':d,'confirmed_clear':c,'authorized_directions':sum(r['authorized_motion'] in ('STRAIGHT','LEFT','RIGHT') for r in rs),'authorized_on_confirmed_clear':sum(yes(r,'confirmed_clear') and r['authorized_motion'] in ('STRAIGHT','LEFT','RIGHT') for r in rs),'any_miss':sum(yes(r,'any_miss') for r in rs),'false_stop':sum(yes(r,'true_false_stop') for r in rs),'unknown':sum(r['safety_state']=='UNKNOWN' for r in rs),'observe':sum(r['safety_state']=='OBSERVE' for r in rs),'blocked':sum(r['safety_state']=='BLOCKED' for r in rs),'clear':sum(r['safety_state']=='CLEAR' for r in rs),'hint_only':sum(bool(r['observation_hint']) and not r['authorized_motion'] for r in rs),'transitions':max(0,n-1),'command_flips':sum(a['effective_command']!=b['effective_command'] for a,b in zip(rs,rs[1:]))}
groups=collections.defaultdict(list)
for r in rows:
 if r['condition']=='production':groups[(r['arm'],r['session'])].append(r)
result=[]
for (arm,sid),rs in sorted(groups.items()):
 rs.sort(key=lambda r:int(r['frame'])); result.append({'arm':arm,'session':sid,**compute(rs)})
fields=list(result[0]);out.mkdir(parents=True,exist_ok=True)
with (out/'per_session.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=fields,lineterminator='\n');w.writeheader();w.writerows(result)
allmetrics={}
for arm in sorted({a for a,s in groups}):
 xs=[r for r in result if r['arm']==arm]; allmetrics[arm]={k:sum(r[k] for r in xs) for k in fields if k not in ('arm','session')}
(out/'metrics_selected.json').write_text(json.dumps({'definitions':{'authorized_directions':'authorized_motion in STRAIGHT/LEFT/RIGHT, not observation_hint','authorized_on_confirmed_clear':'authorized direction on proxy confirmed_clear; direction correctness not evaluated','any_miss':'proxy danger frame with direction command; turn safety not evaluated','false_stop':'STOP on confirmed_clear','command_flips':'effective_command changes between consecutive processed annotated frames in each session; not continuous-video rate','hint_only':'observation_hint present with no authorized motion','danger_warning':'NOT_EVALUABLE: warnings in legacy metrics include UNKNOWN; no unambiguous risk-specific alert evaluation'},'arms':allmetrics},indent=2)+'\n')
print(json.dumps(allmetrics,indent=2))
