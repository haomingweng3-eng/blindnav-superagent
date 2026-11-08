"""Recompute published group counts and isolate the surface ageing arithmetic.

This is NOT a model run, dataset replay, navigation benchmark or safety test.
Source: pinned GitHub per_group.csv and SurfaceGrid2D._age_cells, read via connector.
The lists below are literal counts grouped from that CSV, not estimated frame labels.
"""
from collections import Counter
from dataclasses import dataclass, field
from types import SimpleNamespace
import json
from pathlib import Path

# Exact published counts grouped by final state, across the three sessions.
clear_by_state = {
    'CLEAR': [8,21],
    'OBSERVE': [1,25,5,38,1,1,7,2],
    'UNKNOWN': [1,4,9,1,4,3,2,2,5,1,1,3,1,2,1,1]
}
# UNKNOWN rows, keeping the original danger/clear/other classification.
unknown_by_label = {
    'confirmed_clear': clear_by_state['UNKNOWN'],
    'danger': [1,5,2,23,3,6,3,10,9,2,4,6,49,7,42,33],
    'other': [1,1,7,6,3,2,2,3,3,1]
}
clear_reasons = {
    'surface_walkable_CLEAR': [8,21],
    'surface_non_walkable': [25,5,38,7,2],
    'occupancy_clearance_unknown': [1,4,9,2,2,5,1,2,1],
    'surface_no_candidate_ray': [1,3,1,1,1],
    'no_metric_depth': [4,3],
    'occupancy_unconfirmed_body': [1],
    'surface_walkable_OBSERVE': [1,1],
}
counts = {
    'confirmed_clear_final_states': {k:sum(v) for k,v in clear_by_state.items()},
    'unknown_by_proxy_label': {k:sum(v) for k,v in unknown_by_label.items()},
    'confirmed_clear_final_reasons': {k:sum(v) for k,v in clear_reasons.items()},
}
assert sum(counts['confirmed_clear_final_states'].values()) == 150
assert sum(counts['unknown_by_proxy_label'].values()) == 275
assert sum(counts['confirmed_clear_final_reasons'].values()) == 150
assert counts['confirmed_clear_final_states']['UNKNOWN'] == 41
assert counts['unknown_by_proxy_label']['danger'] == 205

@dataclass
class Cell:
    scores:dict = field(default_factory=lambda:{1:1.0})
    last_frame:int=0
    def decay(self, factor):
        # Same operation as _CellEvidence.decay.
        for role in tuple(self.scores):
            self.scores[role] *= factor
            if self.scores[role] < 1e-4:
                del self.scores[role]

# Verbatim method body from pinned SurfaceGrid2D._age_cells; types omitted.
def original_age(self, frame_id, touched):
    touched_set = set(touched)
    if self._last_frame < 0:
        return
    for key, cell in tuple(self._cells.items()):
        if key in touched_set or cell.last_frame < 0:
            continue
        delta = max(0, int(frame_id) - int(cell.last_frame))
        if delta:
            cell.decay(self.cfg.evidence_decay ** delta)
        if not cell.scores or delta > self.cfg.stale_after_frames:
            del self._cells[key]

# Candidate arithmetic fix only: incremental decay; identical last-observation expiry.
def incremental_age(self, frame_id, touched):
    touched_set = set(touched)
    if self._last_frame < 0:
        return
    for key, cell in tuple(self._cells.items()):
        if key in touched_set or cell.last_frame < 0:
            continue
        age = max(0, int(frame_id) - int(cell.last_frame))
        elapsed = max(0, int(frame_id) - max(int(cell.last_frame), self._last_frame))
        if elapsed:
            cell.decay(self.cfg.evidence_decay ** elapsed)
        if not cell.scores or age > self.cfg.stale_after_frames:
            del self._cells[key]

def run(fn, frames):
    obj=SimpleNamespace(_cells={(0,0):Cell()}, _last_frame=0,
        cfg=SimpleNamespace(evidence_decay=.9,stale_after_frames=15))
    trace=[]
    for frame in frames:
        fn(obj, frame, ())
        obj._last_frame=max(frame, obj._last_frame) # update_points sets this after ageing
        value=obj._cells.get((0,0))
        trace.append({'frame':frame,'score':value.scores.get(1) if value else None})
    return trace
old=run(original_age,[5,10,15,20]);new=run(incremental_age,[5,10,15,20])
assert abs(old[2]['score']-.9**30)<1e-12
assert abs(new[2]['score']-.9**15)<1e-12
assert old[-1]['score'] is None and new[-1]['score'] is None
# Same final time should have the same decay in the absence of new observations.
assert abs(run(incremental_age,[15])[0]['score']-new[2]['score'])<1e-12
assert abs(run(incremental_age,[5,5,10])[-1]['score']-.9**10)<1e-12
report={
    'source_commit':'6327cd985200af2aab8e29496a87e5b7526615be',
    'scope':'Published grouped CSV arithmetic + synthetic test of retrieved ageing method; NO model/real-data replay',
    **counts,
    'ageing_case':{'decay':.9,'last_observed_frame':0,'original':old,'incremental_candidate':new,
        'limitation':'Uniform score decay may leave a cell role unchanged until expiry; it can affect later competing observations. Impact on the real 481-frame metrics is NOT_MEASURED. Candidate not applied to production.'}
}
p=Path(__file__).with_name('reuse_findings.json');p.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False,indent=2))
