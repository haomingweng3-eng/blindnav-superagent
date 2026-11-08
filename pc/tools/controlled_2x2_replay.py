#!/usr/bin/env python3
"""Offline matched-geometry experiment; NEVER a production navigation entrypoint.

Reuses frozen SANPO data, existing inference/map/scoring implementations. A/B/C/D
vary map semantics and depth ONLY. The geometry actually selected by the
production calibrator (not a GT plane) is shared by ALL four conditions. Thus D
can be checked against the historical production D, rather than silently
changing D to a different recorded-up convention. This is a conditional map-input
ablation, not an oracle and not the total effect of replacing segmentation.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import numpy as np

CONDITIONS = ('A_gt_gt', 'B_v5_gt', 'C_gt_dav2', 'D_v5_dav2')
DIRECTIONS = {'LEFT', 'RIGHT', 'STRAIGHT'}
MANIFEST_SHA = '2b4997b6c3e561e62a046fb812edbd31de2ad231596da0ab9219755f2a20e37c'
HISTORY_SHA = '60b4b0c6a2e6f843e14f5650a12dd83ea1d5fcaf58d0ce84584ba91f414a069f'
SEG_ID = 'nvidia/segformer-b0-finetuned-cityscapes-1024-1024'
DEPTH_ID = 'depth-anything/Depth-Anything-V2-Small-hf'


def digest(value):
    if isinstance(value, np.ndarray):
        a = np.ascontiguousarray(value)
        header = json.dumps([a.dtype.str, list(a.shape)]).encode()
        return hashlib.sha256(header + a.tobytes()).hexdigest()
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def number(value):
    if value is None:
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def prepare_four(m, session, inp, gt_cls, gt_depth, model, device, estimator, calibrator, cfg):
    """One inference pair and ONE stateful calibration update per frame."""
    v5 = m.predict_v5(model, device, inp.bgr, gt_cls.shape)
    q = estimator.predict(inp.bgr)
    if q.shape != gt_cls.shape or v5.shape != gt_cls.shape:
        raise ValueError('RGB/depth/semantics pixel grids differ; do not silently resize')
    mask = m.rse.walkable_mask_from_classes(v5)
    recorded_up = m.up_from_gravity(inp.gravity_cam)
    fit = calibrator.update(q, mask, session.K, recorded_up, up_source='attitude')
    predicted = m.resolve_condition_depth(
        'production', q, fit, None, camera_height_m=float(session.camera_height_m),
        max_depth_m=float(cfg.ground.max_depth_m))
    up = np.asarray(predicted.up if predicted.up is not None else recorded_up, dtype=np.float64)
    height = float(session.camera_height_m)
    if up.shape != (3,) or not np.all(np.isfinite(up)) or not np.isclose(np.linalg.norm(up), 1., atol=1e-4):
        raise ValueError('common up must be a finite unit vector')
    if not math.isfinite(height) or height <= 0:
        raise ValueError('invalid common camera height')
    reference = None if gt_depth is None else np.asarray(gt_depth, dtype=np.float32)
    estimate = predicted.depth_m if predicted.ok else None
    for depth in (reference, estimate):
        if depth is not None and depth.shape != gt_cls.shape:
            raise ValueError('depth and semantic shapes differ')
    fit_info = {k: number(getattr(fit, k, None)) for k in ('a', 'b', 'confidence')}
    fit_info.update(ok=bool(fit.ok), reused=bool(getattr(fit, 'reused', False)),
                    reason=str(getattr(fit, 'reason', '')), up=up.tolist(),
                    up_source=str(getattr(fit, 'up_source', 'attitude')))
    shared = {
        'calibration_mask_source': 'v5_shared', 'calibration_mask_sha256': digest(mask),
        'ground_fit_sha256': digest(fit_info), 'ground_fit_json': json.dumps(fit_info, allow_nan=False),
        'up_source': 'shared_production_fit_or_recorded_pose', 'up_vector': json.dumps(up.tolist()),
        'camera_height_source': 'session', 'camera_height_m': height,
        'K_sha256': digest(np.asarray(session.K)), 'q_sha256': digest(q),
        'depth_model_id': DEPTH_ID, 'calibration_ok': bool(fit.ok),
    }
    products = {
        'A_gt_gt': (gt_cls, reference, 'gt_map_semantics', 'sanpo_reference_depth'),
        'B_v5_gt': (v5, reference, 'v5_map_semantics', 'sanpo_reference_depth'),
        'C_gt_dav2': (gt_cls, estimate, 'gt_map_semantics', 'dav2_relative_calibrated'),
        'D_v5_dav2': (v5, estimate, 'v5_map_semantics', 'dav2_relative_calibrated'),
    }
    return products, up, height, shared


def run_map(m, surface, occupancy, roles, depth, K, up, height, pose, frame, target):
    """Same map operations as the frozen evaluator; no new safety policy."""
    stride = m.rse.SW_CFG.pixel_stride
    surface.update_from_frame(roles, depth, K, up, height, pose, frame, pixel_stride=stride)
    x, z, h, (vs, us) = m.depth_to_level_points(depth, K, up, height, stride=stride)
    points = np.column_stack([x, z, h])
    point_roles = roles[vs, us]
    visible = m._visible_ground_cells(occupancy, points, point_roles, pose, surface.cfg.ground_band_m)
    keep = ((points[:, 2] >= occupancy.cfg.min_obstacle_height_m)
            & (points[:, 2] <= occupancy.cfg.person_height_m)
            & (point_roles != int(m.rse.ROLE_WALKABLE)))
    occupancy.update_body_points(points[keep], pose, frame, visible)
    sr = surface.raycast_route(pose, target)
    oc = occupancy.raycast_clearance(pose, target)
    return m.fuse(sr, oc), sr, oc, len(points), int(np.isfinite(points).all(axis=1).sum())


def yes(value):
    return str(value).lower() in {'true', '1', 'yes'}


def check_pairs(rows):
    grouped = defaultdict(dict)
    for r in rows:
        key, c = (r['session'], int(r['frame'])), r['condition']
        if c not in CONDITIONS or c in grouped[key]:
            raise ValueError('unknown condition or duplicate frame/condition')
        grouped[key][c] = r
    if not grouped:
        raise ValueError('empty experiment')
    fixed = ('geometry_sha256', 'calibration_mask_sha256', 'ground_fit_sha256',
             'pose_sha256', 'K_sha256', 'q_sha256', 'calibration_update_index',
             'danger_any', 'confirmed_clear')
    for key, group in grouped.items():
        if set(group) != set(CONDITIONS):
            raise ValueError(f'incomplete four-condition frame: {key}')
        for name in fixed:
            if any(name not in r for r in group.values()) or len({str(r[name]) for r in group.values()}) != 1:
                raise ValueError(f'not fixed across conditions: {key} {name}')
        for left, right, field in [('A_gt_gt', 'B_v5_gt', 'depth_sha256'),
                                   ('C_gt_dav2', 'D_v5_dav2', 'depth_sha256'),
                                   ('A_gt_gt', 'C_gt_dav2', 'semantics_sha256'),
                                   ('B_v5_gt', 'D_v5_dav2', 'semantics_sha256')]:
            if group[left][field] != group[right][field]:
                raise ValueError(f'factor pairing failed: {key} {field}')
    return grouped


def summarize(rows):
    check_pairs(rows)
    sessions = sorted({r['session'] for r in rows})
    result = {}
    for label, selected in [('ALL', rows)] + [(s, [r for r in rows if r['session'] == s]) for s in sessions]:
        conditions = {}
        for c in CONDITIONS:
            rs = [r for r in selected if r['condition'] == c]
            dangerous = [r for r in rs if yes(r['danger_any'])]
            clear = [r for r in rs if yes(r['confirmed_clear'])]
            auth = lambda r: r.get('authorized_motion') in DIRECTIONS
            n, nd, nc = len(rs), len(dangerous), len(clear)
            conditions[c] = {
                'frames': n, 'danger': nd, 'confirmed_clear': nc,
                'authorized': sum(auth(r) for r in rs),
                'authorized_on_clear': sum(auth(r) for r in clear),
                'authorized_on_danger': sum(auth(r) for r in dangerous),
                'false_stop': sum(r['effective_command'] == 'STOP' for r in clear),
                'states': dict(Counter(r['safety_state'] for r in rs)),
                'valid_geometry_frames': sum(yes(r['input_depth_available']) for r in rs),
            }
        effects = {}
        for numerator, denominator in [('authorized_on_clear', 'confirmed_clear'),
                                       ('authorized_on_danger', 'danger'), ('authorized', 'frames')]:
            rates = {c: (v[numerator] / v[denominator] if v[denominator] else None) for c, v in conditions.items()}
            if any(x is None for x in rates.values()):
                effects[numerator] = None
                continue
            a, b, c, d = (rates[x] for x in CONDITIONS)
            effects[numerator] = {'A_minus_B': a-b, 'C_minus_D': c-d,
                                  'A_minus_C': a-c, 'B_minus_D': b-d,
                                  'interaction_A_B_C_D': (a-b)-(c-d)}
        result[label] = {'conditions': conditions, 'paired_rate_differences': effects}
    return result


def history_check(rows, history):
    old = {(r['session'], int(r['frame'])): r for r in history
           if r['condition'] == 'production' and r['arm'] == 'mobilio'}
    current = {(r['session'], int(r['frame'])): r for r in rows if r['condition'] == 'D_v5_dav2'}
    if set(old) != set(current):
        raise ValueError('D history/current frame keys differ')
    fields = ('surface_state', 'occupancy_state', 'safety_state', 'effective_command',
              'authorized_motion', 'danger_any', 'confirmed_clear', 'any_miss', 'true_false_stop')
    norm = lambda v: '' if v is None else str(v)
    differences = []
    for key, row in current.items():
        for f in fields:
            if f not in old[key] or f not in row:
                raise ValueError(f'missing D-parity field: {f}')
            if norm(row[f]) != norm(old[key][f]):
                differences.append({'session': key[0], 'frame': key[1], 'field': f,
                                    'old': old[key][f], 'new': row[f]})
    return {'fields': list(fields), 'frames': len(current), 'differences': differences,
            'pass': not differences, 'scope': 'selected historical CSV fields, not system safety'}


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def main(argv=None):
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', required=True, type=Path)
    parser.add_argument('--v5-checkpoint', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--device', choices=['cpu', 'mps', 'cuda'], required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        raise ValueError('output directory exists; use a new run directory')
    if os.environ.get('HF_HUB_OFFLINE') != '1':
        raise ValueError('HF_HUB_OFFLINE=1 required; no automatic downloads')
    sys.path[:0] = [str(root / 'pc'), str(root / 'pc/tools')]
    import mobilio_sanpo_ab as m
    from huggingface_hub import try_to_load_from_cache
    from blindnav_pc.depth import DepthEstimator

    manifest_path = root / 'docs/phase_reports/e3_input_audit_20261107/input_inventory.json'
    history_path = root / 'docs/assets/review_20261107/mobilio_feedback_20261107/production_per_frame.csv'
    if m._file_sha256(manifest_path) != MANIFEST_SHA or m._file_sha256(history_path) != HISTORY_SHA:
        raise ValueError('frozen manifest/history hash mismatch')
    inventory = json.loads(manifest_path.read_text())
    m.validate_trace_inventory(inventory, args.data_root, args.v5_checkpoint)
    with history_path.open(newline='') as fh:
        history = list(csv.DictReader(fh))
    sid_order = list(dict.fromkeys(r['session'] for r in history
                                  if r['condition'] == 'production' and r['arm'] == 'mobilio'))
    sessions = {s: m.SanpoSession(str(args.data_root / s), 'camera_chest', max_width=640) for s in sid_order}
    for s in sessions.values():
        if Path(s.pose_file).name != 'fixed_camera_poses.csv':
            raise ValueError('corrected pose required')
    keys = {sid: s.available_frames() for sid, s in sessions.items()}
    m.validate_trace_frame_coverage(inventory, keys, 'camera_chest')
    m.validate_frozen_frame_keys(history_path, keys, HISTORY_SHA)
    cache = inventory['model_cache_inventory']
    if set(cache) != {SEG_ID, DEPTH_ID}:
        raise ValueError('unexpected model cache manifest')
    resolver = lambda repo, name, revision: try_to_load_from_cache(repo, name, revision=revision)
    m.validate_model_cache_files(cache, resolver)
    snapshots = {name: m.verified_model_snapshot(cache, name, resolver) for name in cache}
    code = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    dirty = subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=root, text=True)
    m.validate_trace_code_state(code, dirty, code)
    np.random.seed(0)
    model, device = m.load_v5(str(args.v5_checkpoint), args.device, revision=cache[SEG_ID]['revision'],
                              expected_model_id=SEG_ID, local_snapshot=snapshots[SEG_ID])
    estimator = DepthEstimator(device=device, revision=cache[DEPTH_ID]['revision'], local_snapshot=snapshots[DEPTH_ID])
    if Path(model.config._name_or_path) != Path(snapshots[SEG_ID]) or Path(estimator.model.config._name_or_path) != Path(snapshots[DEPTH_ID]):
        raise ValueError('loaded model paths differ from checked snapshots')
    started = datetime.now(timezone.utc).isoformat()
    clock = perf_counter()
    args.out.mkdir(parents=True, exist_ok=False)
    cfg, rows = m.Config(), []
    for sid, session in sessions.items():
        indices = keys[sid]
        positions, headings = m.route_frame(session, indices)
        tangents = m.route_tangents(positions)
        cfg.ground.camera_height_m = float(session.camera_height_m)
        calibrator = m.GroundCalibrator(cfg.ground)
        surfaces = {c: m.SurfaceGrid2D() for c in CONDITIONS}
        occupancies = {c: m.OccupancyGrid3D() for c in CONDITIONS}
        for k, frame in enumerate(indices):
            inp, gt = session.inputs(frame), session.gt(frame)
            gt_cls = m.gt_remap(gt.seg)
            yaw = math.atan2(float(tangents[k][0]), float(tangents[k][1]))
            relative = m.Pose2D(0., 0., float(m.wrap_pi(headings[k] - yaw)))
            pose = m.SurfacePose(float(positions[k, 0]), float(positions[k, 1]), float(headings[k]))
            target = float(m.wrap_pi(yaw - headings[k]))
            # GT reference is scoring-only; NONE of its up/height feeds products.
            truth = m.rse.compute_gt_reference(gt, gt_cls, session, frame, relative, m.rse.SW_CFG)
            products, up, height, shared = prepare_four(m, session, inp, gt_cls, gt.depth_m,
                                                       model, device, estimator, calibrator, cfg)
            pose_values = [pose.x_m, pose.z_m, pose.yaw_rad, target]
            shared.update(pose_sha256=digest(pose_values), calibration_update_index=k+1,
                          geometry_sha256=digest([shared['K_sha256'], up.tolist(), height, pose_values]))
            for c, (classes, depth, seg_source, depth_source) in products.items():
                available = depth is not None and bool(np.any(np.isfinite(depth) & (depth > 0)))
                if available:
                    decision, sr, oc, points, finite = run_map(m, surfaces[c], occupancies[c],
                        m.role_map_from_classes(classes), depth, session.K, up, height, pose, int(frame), target)
                    row = m._candidate_row(c, sid, frame, decision, sr, oc, gt_ref=truth,
                        calib_ok=True, depth_source=depth_source, pose_mode='sanpo_recorded_pose')
                else:
                    row = m._no_geometry_rows(c, sid, frame, gt_ref=truth, calib_ok=False,
                        calib_reason='no_metric_depth', depth_source=depth_source,
                        pose_mode='sanpo_recorded_pose')[1]
                    points = finite = 0
                row.update(shared)
                row.update(segmentation_source=seg_source, semantics_sha256=digest(classes),
                    depth_sha256=digest(depth) if depth is not None else 'MISSING',
                    input_depth_available=available, projected_points=points, finite_projected_points=finite,
                    valid_depth_fraction=float(np.mean(np.isfinite(depth) & (depth > 0))) if depth is not None else 0.,
                    depth_at_or_beyond_limit_fraction=float(np.mean(np.isfinite(depth) & (depth >= cfg.ground.max_depth_m))) if depth is not None else 0.,
                    relative_time_ms=float(inp.t_ms), timestamp_source='dataset_frame_index_over_fps',
                    pose_source='SANPO_recorded_pose_and_route_proxy', code_commit=code,
                    input_manifest_sha256=MANIFEST_SHA)
                rows.append(row)
        print(f'{sid}: {len(indices)} frames, 4 conditions; elapsed={perf_counter()-clock:.1f}s', flush=True)
    pairs = check_pairs(rows)
    if len(pairs) != 481 or len(rows) != 1924:
        raise ValueError('expected 481 paired frames / 1924 Mobilio rows')
    fields = list(dict.fromkeys(name for row in rows for name in row))
    with (args.out / 'per_frame.csv').open('w', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    full = summarize(rows)
    complete = [r for group in pairs.values() if all(yes(v['input_depth_available']) for v in group.values()) for r in group.values()]
    parity = history_check(rows, history)
    dump(args.out / 'metrics.json', full)
    dump(args.out / 'complete_geometry_pairs.json', summarize(complete) if complete else {})
    dump(args.out / 'historical_D_parity.json', parity)
    dump(args.out / 'run_metadata.json', {
        'status': 'RUN_COMPLETED' if parity['pass'] else 'HOLD_D_PARITY', 'code_commit': code,
        'started_utc': started, 'finished_utc': datetime.now(timezone.utc).isoformat(),
        'elapsed_seconds': perf_counter()-clock, 'device': device, 'manifest_sha256': MANIFEST_SHA,
        'ground_config': vars(cfg.ground), 'model_revisions': {n: cache[n]['revision'] for n in cache},
        'inference_calls_per_frame': {'v5': 1, 'relative_dav2': 1, 'calibration': 1},
        'scope': 'conditional map-input ablation; no real VIO, direction GT, or deployment safety claim',
        'geometry_policy': 'common production fit.up (recorded-up fallback) and session height for A/B/C/D',
        'depth_semantics': 'relative DAV2 plus existing ground calibration; NOT Metric VKITTI weights',
        'paired_complete_frames': len(complete)//4,
        'warning': 'Three sessions and correlated frames: differences are descriptive, not statistical significance.',
    })
    print('RUN_COMPLETED' if parity['pass'] else 'HOLD: D differs from historical safety fields; inspect saved differences')
    return 0 if parity['pass'] else 3


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, ImportError) as exc:
        print(f'HOLD: {type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(2)
