#!/usr/bin/env python3
"""Reproduce a direction-certificate contract gap, not navigation performance.

Runs the pinned repository's exact fuse function with synthetic route objects.
Only imports of route dataclasses/state constants are replaced. No ML, GPU,
network, maps, or original frames are needed. Do not call this a dataset run.
"""
from __future__ import annotations
import argparse
import ast
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

EXPECTED_BLOB = '1a4a63ef1286e8619634ed7129d5999ac25cac85'


def reproduce(source: Path) -> dict:
    raw = source.read_bytes()
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    if blob != EXPECTED_BLOB:
        raise ValueError('Source differs from the audited historical blob; do not assert the old result.')
    tree = ast.parse(raw)
    tree.body = [n for n in tree.body if not (isinstance(n, ast.ImportFrom) and n.level)]
    module = ModuleType('_blindnav_pinned_fusion_contract')
    previous = sys.modules.get(module.__name__)
    sys.modules[module.__name__] = module
    module.__dict__.update(CLEAR='CLEAR', OBSERVE='OBSERVE', UNKNOWN='UNKNOWN', BLOCKED='BLOCKED',
                           SurfaceRoute=SimpleNamespace, OccupancyRoute=SimpleNamespace)
    cases = []
    try:
        exec(compile(tree, str(source), 'exec'), module.__dict__)
        for surface_angle, obstacle_angle in [(-30, 30), (0, 30), (0, 0)]:
            surface = SimpleNamespace(state='CLEAR', angle_rad=math.radians(surface_angle), reason='walkable')
            occupancy = SimpleNamespace(state='CLEAR', angle_rad=math.radians(obstacle_angle), reason='visible_free_ray')
            result = asdict(module.fuse(surface, occupancy))
            cases.append({'surface_clear_angle_deg': surface_angle,
                          'occupancy_clear_angle_deg': obstacle_angle, 'result': result})
    finally:
        if previous is None:
            sys.modules.pop(module.__name__, None)
        else:
            sys.modules[module.__name__] = previous
    gap = any(x['surface_clear_angle_deg'] != x['occupancy_clear_angle_deg']
              and x['result']['state'] == 'CLEAR' and x['result']['guidance'] is not None for x in cases)
    return {'status': 'DIRECTION_CERTIFICATE_GAP_REPRODUCED' if gap else 'NOT_REPRODUCED',
            'source_git_blob': blob,
            'scope': 'SYNTHETIC_FUNCTION_COUNTEREXAMPLE; not model inference, map integration, dataset performance or collision proof',
            'limitation': 'Actual number of affected real frames is NOT_EVALUATED; different angles may both be safe but the selected one is not certified by this interface.',
            'cases': cases}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'blindnav_pc/mobilio_fusion.py')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    report = reproduce(args.source)
    text = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + '\n'
    if args.out:
        with args.out.open('x', encoding='utf-8') as handle:
            handle.write(text)
    print(text, end='')
    return 0  # Successful diagnostic execution, NOT navigation acceptance.


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f'NOT_EXECUTED: {exc}', file=sys.stderr)
        raise SystemExit(2)
