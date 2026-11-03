"""Harness entry point::

    python -m eval.run --session <SANPO session dir> --camera camera_head --frames 0:30:2 --out out/eval_synth
    python -m eval.run --fixture --out out/eval_fixture            # no download needed

System under test = ``blindnav_pc.pipeline.Pipeline`` fed with RGB + (pose-derived) gravity only.  GT depth / seg
are read only to score.  ``--seg gt`` / ``--depth gt`` swap a *component* for its oracle (ablations that isolate
which stage causes an error); the report states which sources were used.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from typing import Dict, List, Optional, Sequence

import numpy as np

from blindnav_pc.config import Config
from blindnav_pc.ground import up_from_gravity
from blindnav_pc.pipeline import Pipeline

from .bev_eval import action_comparison, bev_compare, gt_walkable_mask, oracle_states, plan_action
from .depth_metrics import confidence_vs_error, depth_report
from .ego_eval import memory_prediction_test, pdr_test
from .sanpo import SanpoSession


class OracleSegmenter:
    """ABLATION: returns the GT walkable mask of the frame set with ``set_gt``."""

    def __init__(self):
        self._seg = None

    def set_gt(self, seg):
        self._seg = seg

    def predict(self, frame_bgr):
        return gt_walkable_mask(self._seg)


class OracleInverseDepth:
    """ABLATION: affine-invariant inverse depth built from GT depth (q = 1/z scaled + shifted, as a depth network
    would give), so only the calibration + BEV + planner stages are exercised."""

    def __init__(self, scale: float = 0.7, shift: float = 0.04):
        self._d, self.scale, self.shift = None, scale, shift

    def set_gt(self, depth):
        self._d = depth

    def predict(self, frame_bgr):
        d = np.where(self._d > 0, self._d, 60.0)
        return (self.scale / d + self.shift).astype(np.float32)


def parse_frames(spec: str, available: Sequence[int]) -> List[int]:
    a, b, s = (list(map(int, spec.split(":"))) + [None, None])[:3] if ":" in spec else (None, None, None)
    if a is None:
        return [int(x) for x in spec.split(",") if int(x) in set(available)]
    sel = [i for i in available if a <= i < b]
    return sel[:: (s or 1)]


def _nanmean(v):
    v = np.asarray([x for x in v if x is not None], dtype=np.float64)
    v = v[np.isfinite(v)]
    return float(v.mean()) if len(v) else None


def run_eval(session: SanpoSession, indices: Sequence[int], pipe: Pipeline, cfg: Config,
             seg_oracle: Optional[OracleSegmenter] = None, depth_oracle: Optional[OracleInverseDepth] = None,
             gravity: str = "pose") -> Dict[str, object]:
    """``gravity``: 'pose' = pose-derived IMU proxy (the honest system input); 'gtplane' = ORACLE gravity from the
    GT ground plane (diagnostic: separates a gravity error from a depth/segmentation error)."""
    planner = pipe.planner
    per, o_act, p_act = [], [], []
    for i in indices:
        inp, gt = session.inputs(i), session.gt(i)
        if seg_oracle is not None:
            seg_oracle.set_gt(gt.seg)
        if depth_oracle is not None:
            depth_oracle.set_gt(gt.depth_m)
        g = inp.gravity_cam
        if gravity == "gtplane":
            g = session.gt_gravity(i)
            if g is None:
                g = inp.gravity_cam
        res = pipe.process(inp.bgr, inp.t_ms, g)                        # RGB + gravity proxy only
        up = up_from_gravity(g)
        row: Dict[str, object] = {"index": i, "ground_ok": bool(res.fit.ok), "confidence": float(res.fit.confidence),
                                  "tracking_ok": inp.tracking_ok}
        blind = pipe._blind_distance(up, inp.bgr.shape)
        ostates = oracle_states(gt.depth_m, gt.seg, session.K, up, session.camera_height_m, cfg)
        oplan = planner.plan(ostates, blind_m=blind)
        o_act.append(plan_action(oplan, cfg))
        if res.depth_metric is not None and res.plan is not None:
            row["depth"] = depth_report(res.depth_metric, gt.depth_m)
            row["bev"] = bev_compare(ostates, res.states, cfg, max_z_m=cfg.bev.forward_m)
            p_act.append(plan_action(res.plan, cfg))
        else:
            p_act.append("STOP")                                        # calibration failed: the system would not move
        row["oracle_action"], row["pred_action"] = o_act[-1], p_act[-1]
        row["clearance_after_inflation_m"] = {
            "oracle": _fin(oplan.clearance_after_inflation_m),
            "pred": _fin(res.plan.clearance_after_inflation_m) if res.plan is not None else None}
        per.append(row)
    ok = [r for r in per if "depth" in r]
    summary: Dict[str, object] = {
        "frames": len(per), "frames_ground_ok": len(ok),
        "depth_all": {k: _nanmean([r["depth"]["all"][k] for r in ok]) for k in ("absrel", "rmse", "delta1", "scale_ratio")},
        "depth_near_0_5m": {k: _nanmean([r["depth"]["near"][k] for r in ok]) for k in ("absrel", "rmse", "delta1", "scale_ratio")},
        "confidence_vs_absrel": confidence_vs_error([r["confidence"] for r in ok],
                                                    [r["depth"]["all"]["absrel"] for r in ok]),
        "bev": {k: _nanmean([r["bev"][k] for r in ok]) for k in
                ("false_free_rate", "miss_rate", "false_occ_rate", "nearest_err_m", "width_err_m")},
        "bev_nearest_miss_frames": int(sum(r["bev"]["nearest_miss"] for r in ok)),
        "bev_frames_with_corridor_obstacle_in_oracle": int(sum(np.isfinite(r["bev"]["nearest_oracle_m"]) for r in ok)),
        "actions": action_comparison(o_act, p_act),
    }
    return {"summary": summary, "per_frame": per}


def _fin(x):
    return round(float(x), 3) if np.isfinite(x) else None


def ego_eval(session: SanpoSession, indices: Sequence[int], cfg: Config, step_length_m: float = 0.7) -> Dict[str, object]:
    out: Dict[str, object] = {"pdr": [], "memory_prediction": []}
    for bias in (0.0, 1.0):
        for mode in ("gyro_only", "gyro_step"):
            r = pdr_test(session, indices, mode, step_length_m, bias_deg_per_s=bias, cfg=cfg)
            r["gyro_bias_deg_per_s"] = bias
            out["pdr"].append(r)
    for mode in ("none", "rot_only", "full"):
        out["memory_prediction"].append(memory_prediction_test(session, indices, mode, cfg, step_length_m))
    return out


def format_report_zh(res: Dict[str, object], meta: Dict[str, object]) -> str:
    s = res["summary"]
    f = lambda x, p=3: "n/a" if x is None else f"{x:.{p}f}"
    L = [f"== 评测：{meta.get('session')}  相机={meta.get('camera')}  帧数={s['frames']}（地面标定成功 {s['frames_ground_ok']}）",
         f"   被测来源：seg={meta.get('seg')} depth={meta.get('depth')} gravity={meta.get('gravity')} BEV深度={meta.get('bev_forward_m')}m；相机高度={meta.get('camera_height_m')} m",
         f"深度(全范围)：AbsRel={f(s['depth_all']['absrel'])} RMSE={f(s['depth_all']['rmse'], 2)}m "
         f"delta1={f(s['depth_all']['delta1'])} 尺度比中位数={f(s['depth_all']['scale_ratio'])}",
         f"深度(0.3–5m 近场)：AbsRel={f(s['depth_near_0_5m']['absrel'])} RMSE={f(s['depth_near_0_5m']['rmse'], 2)}m "
         f"delta1={f(s['depth_near_0_5m']['delta1'])}",
         f"置信度-误差 Spearman={f(s['confidence_vs_absrel']['spearman'], 2)}（n={s['confidence_vs_absrel']['n']}，负相关才有用）",
         f"BEV Pred vs Oracle：误判可通行(false-free)={f(s['bev']['false_free_rate'])} 漏检障碍={f(s['bev']['miss_rate'])} "
         f"误报障碍={f(s['bev']['false_occ_rate'])} 最近障碍距离误差={f(s['bev']['nearest_err_m'], 2)}m "
         f"通道宽度误差={f(s['bev']['width_err_m'], 2)}m "
         f"（Oracle 通道内有障碍的帧 {s['bev_frames_with_corridor_obstacle_in_oracle']}，其中 Pred 完全漏检 {s['bev_nearest_miss_frames']}）"]
    a = s["actions"]
    L.append(f"规划动作 Oracle vs Pred：一致率={f(a['agreement'])} 危险前进率={f(a['unsafe_forward_rate'])} "
             f"误停率={f(a['false_stop_rate'])} 选错侧率={f(a['wrong_side_rate'])}")
    if "ego" in res:
        for r in res["ego"]["pdr"]:
            L.append(f"自运动 PDR [{r['mode']}, 陀螺偏置 {r['gyro_bias_deg_per_s']}°/s]：路径 {r['path_len_m']:.1f}m，"
                     f"终点误差 {r['final_pos_err_m']:.2f}m（横向 {r['final_dx_err_m']:+.2f} 前向 {r['final_dz_err_m']:+.2f}），RMS {r['rms_pos_err_m']:.2f}m")
        for r in res["ego"]["memory_prediction"]:
            L.append(f"记忆预测下一帧 [{r['mode']}]：障碍召回={f(r['occ_recall'])} 自由召回={f(r['free_recall'])} 幽灵率={f(r['ghost_rate'])}")
    return "\n".join(L)


def build_pipeline(session: SanpoSession, seg: str, depth: str, cfg: Config):
    seg_o = OracleSegmenter() if seg == "gt" else None
    dep_o = OracleInverseDepth() if depth == "gt" else None
    if seg_o is None:
        from blindnav_pc.segmentation import make_segmenter
        segmenter = make_segmenter(seg)
    else:
        segmenter = seg_o
    if dep_o is None:
        from blindnav_pc.depth import DepthEstimator
        dep = DepthEstimator()
    else:
        dep = dep_o
    cfg.ground.camera_height_m = session.camera_height_m
    return Pipeline(segmenter, dep, session.K, cfg), seg_o, dep_o


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="eval.run", description=__doc__.split("\n")[0])
    ap.add_argument("--session", help="SANPO session directory (contains description.json)")
    ap.add_argument("--fixture", action="store_true", help="use the built-in synthetic fixture instead of data")
    ap.add_argument("--camera", help="camera_head | camera_chest (default: first found)")
    ap.add_argument("--frames", default="0:1000:1", help="start:stop:step over available frames, or a comma list")
    ap.add_argument("--seg", default="hf", help="hf | hf:<id> | onnx:<path> | const | gt (oracle ablation)")
    ap.add_argument("--depth", default="dav2", choices=["dav2", "gt"], help="gt = oracle ablation")
    ap.add_argument("--gravity", default="pose", choices=["pose", "gtplane"],
                    help="pose = pose-derived IMU proxy (default); gtplane = ORACLE gravity from GT ground (diagnostic)")
    ap.add_argument("--bev-forward", type=float, default=8.0,
                    help="BEV depth in m for the eval (default 8: a level head camera does not see the ground nearer than ~3.5 m)")
    ap.add_argument("--height", type=float, help="camera height (m); default per camera type")
    ap.add_argument("--max-width", type=int, default=640)
    ap.add_argument("--step-length", type=float, default=0.7)
    ap.add_argument("--no-ego", action="store_true")
    ap.add_argument("--out", default="out/eval")
    a = ap.parse_args(argv)
    if a.fixture:
        from .fixture import make_fixture_session
        a.session = make_fixture_session(tempfile.mkdtemp(prefix="blindnav_fixture_"))
        a.seg = "gt" if a.seg == "hf" else a.seg
        a.depth = "gt" if a.depth == "dav2" else a.depth
    if not a.session:
        print("need --session or --fixture", file=sys.stderr)
        return 2
    session = SanpoSession(a.session, a.camera, a.max_width, camera_height_m=a.height)
    idx = parse_frames(a.frames, session.available_frames())
    if len(idx) < 2:
        print("fewer than 2 frames selected/available", file=sys.stderr)
        return 2
    cfg = Config()
    cfg.bev.forward_m = a.bev_forward
    pipe, seg_o, dep_o = build_pipeline(session, a.seg, a.depth, cfg)
    res = run_eval(session, idx, pipe, cfg, seg_o, dep_o, a.gravity)
    res["gravity_check"] = session.gravity_check(idx[:: max(1, len(idx) // 6)])
    if not a.no_ego:
        res["ego"] = ego_eval(session, idx, cfg, a.step_length)
    meta = {"session": os.path.basename(a.session.rstrip("/")), "camera": session.camera, "seg": a.seg, "depth": a.depth, "gravity": a.gravity, "bev_forward_m": a.bev_forward,
            "camera_height_m": session.camera_height_m, "frames": idx, "pose_file": os.path.basename(session.pose_file),
            "kind": session.kind}
    res["meta"] = meta
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "eval.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2, default=lambda o: None if o is None else float(o))
    print(format_report_zh(res, meta))
    print(f"重力方向检查（位姿 vs GT 地面法线）：{res['gravity_check']}")
    print(f"结果已写入 {os.path.join(a.out, 'eval.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
