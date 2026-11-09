"""Synthetic contract tests only; these are NOT a SANPO/model replay."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

path = Path(__file__).resolve().parents[1] / 'tools/controlled_2x2_replay.py'
spec = importlib.util.spec_from_file_location('controlled_2x2_replay', path)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def make_rows(n=2):
    rows = []
    for frame in range(n):
        for condition in c.CONDITIONS:
            rows.append(dict(session='s', frame=frame, condition=condition, arm='mobilio',
                geometry_sha256='g', calibration_mask_sha256='m', ground_fit_sha256='f',
                pose_sha256='p', K_sha256='k', q_sha256='q', calibration_update_index=frame+1,
                depth_sha256='reference' if condition in c.CONDITIONS[:2] else 'predicted',
                semantics_sha256='gt' if condition in ('A_gt_gt', 'C_gt_dav2') else 'v5',
                danger_any=frame == 0, confirmed_clear=frame != 0,
                input_depth_available=True, authorized_motion=None,
                surface_state='UNKNOWN', occupancy_state='UNKNOWN', safety_state='UNKNOWN',
                effective_command='UNKNOWN', any_miss=False, true_false_stop=False))
    return rows


def test_pairs_accept_matched_geometry_and_separate_factors():
    assert len(c.check_pairs(make_rows())) == 2


@pytest.mark.parametrize('field', ['geometry_sha256', 'ground_fit_sha256', 'calibration_update_index'])
def test_pairs_reject_changed_geometry_or_calibrator_state(field):
    rows = make_rows()
    rows[0][field] = 'changed'
    with pytest.raises(ValueError, match='not fixed'):
        c.check_pairs(rows)


def test_duplicate_or_missing_conditions_fail_closed():
    rows = make_rows()
    with pytest.raises(ValueError, match='duplicate'):
        c.check_pairs(rows + [dict(rows[0])])
    with pytest.raises(ValueError, match='incomplete'):
        c.check_pairs(rows[:-1])


def test_depth_pair_and_semantic_pair_are_checked():
    rows = make_rows()
    rows[0]['depth_sha256'] = 'different'
    with pytest.raises(ValueError, match='factor pairing'):
        c.check_pairs(rows)
    rows = make_rows()
    rows[1]['semantics_sha256'] = 'different'
    with pytest.raises(ValueError, match='factor pairing'):
        c.check_pairs(rows)


def test_effects_include_abstentions_and_keep_denominators():
    rows = make_rows()
    for r in rows:
        if r['frame'] == 1 and r['condition'] in ('A_gt_gt', 'C_gt_dav2'):
            r.update(authorized_motion='STRAIGHT', effective_command='STRAIGHT', safety_state='CLEAR')
    summary = c.summarize(rows)['ALL']
    effect = summary['paired_rate_differences']['authorized_on_clear']
    assert effect['A_minus_B'] == 1.0
    assert effect['C_minus_D'] == 1.0
    assert effect['interaction_A_B_C_D'] == 0.0
    assert summary['conditions']['B_v5_gt']['confirmed_clear'] == 1
    assert summary['conditions']['B_v5_gt']['authorized_on_clear'] == 0


def test_no_denominator_returns_none_not_fake_zero_accuracy():
    rows = make_rows(n=1)
    assert c.summarize(rows)['ALL']['paired_rate_differences']['authorized_on_clear'] is None


def test_history_parity_detects_changed_frame_not_only_total():
    rows = make_rows()
    history = [dict(r, condition='production') for r in rows if r['condition'] == 'D_v5_dav2']
    assert c.history_check(rows, history)['pass']
    rows[-1]['safety_state'] = 'CLEAR'
    result = c.history_check(rows, history)
    assert not result['pass'] and result['differences'][0]['field'] == 'safety_state'


def test_prepare_once_per_frame_gt_plane_does_not_enter_projection():
    seen = []
    fit = NS(a=1., b=0., ok=True, confidence=.9, reused=False, up_source='visual', up=np.array([0., 1., 0.]))
    reference = np.ones((2, 2), dtype=np.float32) * 5
    predicted = np.ones((2, 2), dtype=np.float32) * 6
    gt_cls, v5 = np.zeros((2, 2), dtype=np.uint8), np.ones((2, 2), dtype=np.uint8)
    def update(q, mask, K, up, up_source):
        seen.append(mask.copy())
        return fit
    m = NS(predict_v5=lambda *a: v5, up_from_gravity=lambda x: np.array([0., 0., 1.]),
           rse=NS(walkable_mask_from_classes=lambda x: x == 1),
           resolve_condition_depth=lambda *a, **k: NS(ok=True, up=fit.up, depth_m=predicted))
    session = NS(K=np.eye(3), camera_height_m=1.35)
    inp = NS(bgr=np.zeros((2, 2, 3)), gravity_cam=np.array([0., 0., -1.]))
    args = (m, session, inp, gt_cls, reference, None, 'cpu',
            NS(predict=lambda x: np.ones((2, 2))), NS(update=update), NS(ground=NS(max_depth_m=15.)))
    products, up, height, shared = c.prepare_four(*args)
    assert len(seen) == 1 and seen[0].all()
    assert np.array_equal(up, fit.up) and height == 1.35
    assert products['A_gt_gt'][1] is products['B_v5_gt'][1]
    assert products['C_gt_dav2'][1] is products['D_v5_dav2'][1]
    assert np.array_equal(products['A_gt_gt'][0], products['C_gt_dav2'][0])
    assert shared['depth_model_id'] == c.DEPTH_ID
    c.prepare_four(*args)
    assert len(seen) == 2  # Two frames, not eight condition-specific updates.


def test_prepare_missing_predicted_depth_is_not_gt_fallback():
    fit = NS(a=None, b=None, ok=False, confidence=0, up_source='attitude')
    m = NS(predict_v5=lambda *a: np.zeros((2, 2)), up_from_gravity=lambda x: np.array([0., 1., 0.]),
           rse=NS(walkable_mask_from_classes=lambda x: x == 0),
           resolve_condition_depth=lambda *a, **k: NS(ok=False, up=None, depth_m=None))
    products, _, _, _ = c.prepare_four(m, NS(K=np.eye(3), camera_height_m=1.35),
        NS(bgr=np.zeros((2, 2, 3)), gravity_cam=np.ones(3)), np.zeros((2, 2)), np.ones((2, 2)),
        None, 'cpu', NS(predict=lambda x: np.ones((2, 2))), NS(update=lambda *a, **k: fit),
        NS(ground=NS(max_depth_m=15.)))
    assert products['A_gt_gt'][1] is not None
    assert products['C_gt_dav2'][1] is products['D_v5_dav2'][1] is None
