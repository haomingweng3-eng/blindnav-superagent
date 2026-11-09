"""Render three actual ray traces as SVG, without reconstructed imagery."""
import csv, html, json, math, sys
from pathlib import Path
rows={(r['session'],int(r['frame'])):r for r in csv.DictReader(open(sys.argv[1])) if r['arm']=='mobilio'}
chosen={}
with open(sys.argv[2]) as f:
    for line in f:
        d=json.loads(line); row=rows[(d['session'],d['frame'])]
        if row['reason']!='surface:no_candidate_ray': continue
        best=[x for x in d['rays'] if x['best_rejected_diagnostic']]
        kind=best[0]['termination_reason'] if best else d['trace_status']
        if kind in ('UNKNOWN_GAP','NON_WALKABLE','NO_OBSERVATION') and kind not in chosen:
            chosen[kind]=(d,best[0] if best else None)
        if len(chosen)==3: break
if len(chosen)!=3: raise ValueError('required example categories missing')
svg=['<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="660" viewBox="0 0 1000 660">', '<rect width="100%" height="100%" fill="white"/>', '<text x="30" y="25" font-family="sans-serif" font-size="17">Recorded ray evidence (not an authorized route)</text>', '<text x="30" y="48" font-family="sans-serif" font-size="12" fill="#1464b5">Blue: planner observed distance</text>', '<text x="270" y="48" font-family="sans-serif" font-size="12" fill="#a63722">Red: continuous walkable length; dot: best rejected ray</text>']
for i,(kind,(d,best)) in enumerate(chosen.items()):
    y0=75+i*185; rays=d['rays']; ymax=max(1.0,max(max(r['observed_distance_m'],r['continuous_walkable_length_m']) for r in rays)); ymax=math.ceil(ymax)
    amin,amax=rays[0]['angle_rad'],rays[-1]['angle_rad']
    svg.append(f'<text x="30" y="{y0}" font-family="sans-serif" font-size="12">{html.escape(d["session"])} frame {d["frame"]}: {kind}</text>')
    svg.append(f'<path d="M 70 {y0+145} H 955 M 70 {y0+15} V {y0+145}" fill="none" stroke="#777"/>')
    for field,color in [('observed_distance_m','#1464b5'),('continuous_walkable_length_m','#a63722')]:
        pts=' '.join(f'{70+(r["angle_rad"]-amin)/(amax-amin)*885:.2f},{y0+145-r[field]/ymax*130:.2f}' for r in rays)
        svg.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.5"/>')
    if best:
        x=70+(best['angle_rad']-amin)/(amax-amin)*885; y=y0+145-best['continuous_walkable_length_m']/ymax*130
        svg.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="#a63722"/>')
    svg.append(f'<text x="38" y="{y0+24}" font-family="sans-serif" font-size="10">{ymax} m</text><text x="53" y="{y0+145}" font-family="sans-serif" font-size="10">0</text>')
    svg.append(f'<text x="70" y="{y0+160}" font-family="sans-serif" font-size="10">{math.degrees(amin):.1f}°</text><text x="910" y="{y0+160}" font-family="sans-serif" font-size="10">{math.degrees(amax):.1f}°</text>')
svg.append('<text x="400" y="650" font-family="sans-serif" font-size="11">Body-relative ray angle (degrees)</text></svg>')
Path(sys.argv[3]).write_text('\n'.join(svg),encoding='utf-8')
print([(k,d['session'],d['frame']) for k,(d,_) in chosen.items()])
