#!/usr/bin/env python3
"""Audit existing BlindNav CSV directions. No inference and no collision verdict."""
from __future__ import annotations
import argparse
import csv
import gzip
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

DIRECTIONS = {'LEFT', 'RIGHT', 'STRAIGHT'}
REQUIRED = {'condition', 'arm', 'session', 'frame', 'surface_angle_rad',
            'occupancy_angle_rad', 'authorized_motion', 'safety_state',
            'danger_any', 'confirmed_clear'}
TOLERANCE_DEG = 1e-6  # Numeric equality tolerance, NOT a collision/safety threshold.


def truth(value: str) -> bool:
    if value.lower() in ('true', '1'):
        return True
    if value.lower() in ('false', '0'):
        return False
    raise ValueError(f'Invalid or absent proxy label: {value!r}')


def angle(value: str) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def load_rows(path: Path, expected_frames: int = 481) -> list[dict]:
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f'Missing columns: {sorted(missing)}')
        rows, seen = [], set()
        for source in reader:
            if source['condition'] != 'production' or source['arm'] != 'mobilio':
                continue
            row = dict(source)
            row['frame'] = int(row['frame'])
            key = (row['session'], row['frame'])
            if not row['session'] or key in seen:
                raise ValueError(f'Empty session or duplicate frame: {key}')
            seen.add(key)
            row['danger'] = truth(row['danger_any'])
            row['clear_proxy'] = truth(row['confirmed_clear'])
            if row['danger'] and row['clear_proxy']:
                raise ValueError(f'Overlapping proxy groups: {key}')
            auth = row['authorized_motion'].strip()
            if auth and auth not in DIRECTIONS:
                raise ValueError(f'Unexpected motion value: {key}: {auth}')
            row['authorized_motion'] = auth
            rows.append(row)
    if not rows or (expected_frames and len(rows) != expected_frames):
        raise ValueError(f'Frame count {len(rows)}, expected {expected_frames or "nonzero"}')
    return rows


def audit(rows: list[dict]) -> tuple[dict, list[dict], list[dict]]:
    groups = defaultdict(list)
    for row in rows:
        groups[row['session']].append(row)
    detail, by_session = [], []
    for sid, session_rows in groups.items():
        record = dict(session=sid, frames=len(session_rows), authorized=0,
                      finite_angle_pairs=0, missing_angle_pairs=0,
                      different_angle_pairs=0, auth_without_clear=0,
                      danger_proxy=0, clear_proxy=0,
                      authorized_on_danger_proxy=0, authorized_on_clear_proxy=0,
                      different_on_danger_proxy=0, different_on_clear_proxy=0)
        for row in session_rows:
            record['danger_proxy'] += int(row['danger'])
            record['clear_proxy'] += int(row['clear_proxy'])
            if row['authorized_motion'] not in DIRECTIONS:
                continue
            record['authorized'] += 1
            record['authorized_on_danger_proxy'] += int(row['danger'])
            record['authorized_on_clear_proxy'] += int(row['clear_proxy'])
            record['auth_without_clear'] += int(row['safety_state'] != 'CLEAR')
            surface, occupancy = angle(row['surface_angle_rad']), angle(row['occupancy_angle_rad'])
            difference = None
            status = 'MISSING_OR_NONFINITE_ANGLE'
            if surface is not None and occupancy is not None:
                difference = abs(math.degrees(math.atan2(math.sin(surface-occupancy),
                                                        math.cos(surface-occupancy))))
                different = difference > TOLERANCE_DEG
                status = 'DIFFERENT_ANGLES' if different else 'SAME_ANGLE'
                record['finite_angle_pairs'] += 1
                record['different_angle_pairs'] += int(different)
                record['different_on_danger_proxy'] += int(different and row['danger'])
                record['different_on_clear_proxy'] += int(different and row['clear_proxy'])
            else:
                record['missing_angle_pairs'] += 1
            detail.append(dict(session=sid, frame=row['frame'],
                               authorized_motion=row['authorized_motion'], safety_state=row['safety_state'],
                               surface_state=row.get('surface_state', ''),
                               occupancy_state=row.get('occupancy_state', ''),
                               surface_angle_deg=math.degrees(surface) if surface is not None else None,
                               occupancy_angle_deg=math.degrees(occupancy) if occupancy is not None else None,
                               difference_deg=difference, angle_pair_status=status,
                               danger_proxy=row['danger'], confirmed_clear_proxy=row['clear_proxy']))
        by_session.append(record)
    totals = {key: sum(r[key] for r in by_session) for key in by_session[0] if key != 'session'}
    result = dict(scope='EXISTING_CSV_AUDIT_ONLY', totals=totals,
                  equality_tolerance_deg=TOLERANCE_DEG,
                  interpretation='Angle disagreement is NOT a collision or wrong-turn label. '
                                 'Equal angles do NOT certify body clearance or observed free space.',
                  actual_affected_or_unsafe_frames='NOT_EVALUATED',
                  new_model_inference=False)
    return result, detail, by_session


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open('x', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--csv', required=True, type=Path)
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--expected-frames', type=int, default=481)
    p.add_argument('--expected-sha256', help='Optional compressed/input file hash from the pinned manifest')
    args = p.parse_args(argv)
    if args.out.exists():
        raise ValueError('Output directory already exists; use a new directory')
    if args.expected_frames < 0:
        raise ValueError('expected-frames must be >= 0')
    source_hash = hashlib.sha256(args.csv.read_bytes()).hexdigest()
    if args.expected_sha256 and source_hash.lower() != args.expected_sha256.lower():
        raise ValueError('Input SHA256 mismatch')
    rows = load_rows(args.csv, args.expected_frames)
    result, detail, sessions = audit(rows)
    result['input_sha256'] = source_hash
    result['source_name'] = args.csv.name
    args.out.mkdir(parents=True, exist_ok=False)
    with (args.out/'summary.json').open('x', encoding='utf-8') as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write('\n')
    fields = list(detail[0]) if detail else ['session', 'frame', 'authorized_motion', 'angle_pair_status']
    write_csv(args.out/'authorized_route_pairs.csv', detail, fields)
    write_csv(args.out/'per_session.csv', sessions, list(sessions[0]))
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    print('AUDIT_COMPLETED: existing CSV only, NOT navigation acceptance.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, csv.Error, KeyError) as exc:
        print(f'NOT_EXECUTED: {exc}', file=sys.stderr)
        raise SystemExit(2)
