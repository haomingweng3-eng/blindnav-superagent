"""Aggregate recorded E3 outputs without inferring causes beyond ray evidence."""
import collections
import csv
import json
import sys
from pathlib import Path


def truth(value):
    return value in ('True', 'true', '1')


def write(path, rows, fields):
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def main(csv_path, jsonl_path, out):
    out.mkdir(parents=True, exist_ok=True)
    with csv_path.open(newline='', encoding='utf-8') as f:
        rows = [r for r in csv.DictReader(f) if r['condition'] == 'production' and r['arm'] == 'mobilio']
    indexed = {(r['session'], int(r['frame'])): r for r in rows}
    if len(indexed) != len(rows):
        raise ValueError('duplicate per-frame key')
    groups = collections.Counter()
    buckets = collections.Counter()
    count = 0
    seen = set()
    with jsonl_path.open(encoding='utf-8') as f:
        for line in f:
            trace = json.loads(line)
            key = (trace['session'], int(trace['frame']))
            if key in seen or key not in indexed:
                raise ValueError('duplicate or unexpected trace key')
            seen.add(key)
            row = indexed[key]
            danger, clear = truth(row['danger_any']), truth(row['confirmed_clear'])
            label = 'overlap' if danger and clear else 'danger' if danger else 'confirmed_clear' if clear else 'other'
            group = (row['session'], label, row['surface_state'], row['occupancy_state'], row['safety_state'], row['reason'], trace['trace_status'])
            groups[group] += 1
            if row['reason'] == 'surface:no_candidate_ray':
                best = [r for r in trace['rays'] if r['best_rejected_diagnostic']]
                if len(best) > 1:
                    raise ValueError('multiple best rejected rays')
                bucket = best[0]['termination_reason'] if best else 'NO_POSITIVE_LENGTH_REJECTED_RAY'
                buckets[(row['session'], label, trace['trace_status'], bucket)] += 1
            count += 1
    if seen != set(indexed):
        raise ValueError('missing trace frames')
    fields = ['session', 'gt_group', 'surface_state', 'occupancy_state', 'safety_state', 'reason', 'trace_status']
    write(out / 'per_group.csv', [dict(zip(fields, k), frames=v) for k, v in sorted(groups.items())], fields + ['frames'])
    fields = ['session', 'gt_group', 'trace_status', 'best_rejected_termination']
    write(out / 'no_candidate_buckets.csv', [dict(zip(fields, k), frames=v) for k, v in sorted(buckets.items())], fields + ['frames'])
    print(json.dumps({'frames': count, 'groups': len(groups), 'no_candidate_frames': sum(buckets.values()), 'buckets': len(buckets)}, indent=2))


if __name__ == '__main__':
    if len(sys.argv) != 4:
        sys.exit('usage: group_analysis.py ON/per_frame.csv ON/rays.jsonl OUTPUT_DIR')
    main(*(Path(x) for x in sys.argv[1:]))
