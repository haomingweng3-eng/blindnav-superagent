"""Command line: ``python -m blindnav_pc run|sim|metrics``."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time


def _cmd_run(a) -> int:
    import cv2

    from .config import Config
    from .depth import DepthEstimator
    from .ego import CAM_OPENCV, CAM_OPENGL
    from .io import (VideoSource, gravity_from_pitch_roll, load_camera, load_imu_csv, load_orientation_csv,
                     load_steps_csv, resolve_gravity)
    from .metrics import compute_metrics, format_report, load_labels_csv
    from .pipeline import Pipeline
    from .segmentation import make_segmenter

    cfg = Config.from_json(a.config)
    if a.height is not None:
        cfg.ground.camera_height_m = a.height
    src_spec = int(a.webcam) if a.webcam is not None else a.video
    if src_spec is None:
        print("需要 --video 或 --webcam", file=sys.stderr)
        return 2
    imu = load_imu_csv(a.imu) if a.imu else None
    orient = load_orientation_csv(a.orientation, CAM_OPENGL if a.cam_axes == "opengl" else CAM_OPENCV) \
        if a.orientation else None
    steps = load_steps_csv(a.steps) if a.steps else None
    seg = make_segmenter(a.seg)
    depth = DepthEstimator(input_size=a.depth_size)
    os.makedirs(a.out, exist_ok=True)
    log_path = os.path.join(a.out, "log.jsonl")
    writer = None
    n, t_start = 0, time.perf_counter()
    gui = not a.no_gui
    with VideoSource(src_spec, max_frames=a.max_frames) as vs, open(log_path, "w", encoding="utf-8") as lf:
        K = load_camera(a.camera, vs.width, vs.height, cfg.camera.fov_deg)
        pipe = Pipeline(seg, depth, K, cfg)
        g_fallback = gravity_from_pitch_roll(cfg.camera.pitch_deg, cfg.camera.roll_deg)
        for frame, t_ms in vs:
            if a.stride > 1 and n % a.stride:
                n += 1
                continue
            # Prefer TYPE_GRAVITY; only then use the attitude-derived gravity, then the fixed pitch.
            g, g_src = resolve_gravity(t_ms, imu, orient, g_fallback)
            hd = orient.heading_at(t_ms) if orient is not None else None
            res = pipe.process(frame, t_ms, g, heading_rad=hd,
                               steps_total=steps.count_at(t_ms) if steps is not None else None,
                               gravity_source=g_src)
            lf.write(json.dumps(res.log, ensure_ascii=False) + "\n")
            n += 1
            print(f"[{t_ms/1000:6.2f}s] {res.command:<8} {res.text}  "
                  f"(a={res.log.get('a')}, b={res.log.get('b')}, conf={res.log.get('confidence')}, "
                  f"{res.log['latency_ms']:.0f} ms)")
            if gui or a.save_video:
                from .viz import VideoWriter, compose, overlay_frame, render_bev
                lines = [f"{res.command} {res.text}", f"a={res.log.get('a')} b={res.log.get('b')} "
                         f"conf={res.log.get('confidence')}"]
                ov = overlay_frame(frame, res.mask, res.depth_metric, lines)
                bev = render_bev(res.states, cfg.bev.cell_m, cfg.bev.lateral_half_m, res.plan,
                                 (cfg.planner.corridor_len_m, cfg.planner.half_width_m))
                img = compose(ov, bev)
                if a.save_video:
                    if writer is None:
                        writer = VideoWriter(os.path.join(a.out, "overlay.mp4"), vs.fps or 10.0,
                                             (img.shape[1], img.shape[0]))
                    writer.write(img)
                if gui:
                    cv2.imshow("blindnav_pc", img)
                    if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                        break
    if writer:
        writer.close()
    if gui:
        cv2.destroyAllWindows()
    wall = time.perf_counter() - t_start
    from .metrics import read_jsonl
    recs = read_jsonl(log_path)
    labels = load_labels_csv(a.labels) if a.labels else None
    m = compute_metrics(recs, labels)
    m["wall_fps"] = round(len(recs) / wall, 3) if wall > 0 else None
    with open(os.path.join(a.out, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    print(format_report(m))
    print(f"处理帧数 {len(recs)}，总耗时 {wall:.1f}s，整体 FPS {m['wall_fps']}；日志：{log_path}")
    return 0


def _cmd_sim(a) -> int:
    from sim import SCENARIOS, run_scenario
    names = list(SCENARIOS) if a.scenario == "all" else [a.scenario]
    ok = True
    for n in names:
        r = run_scenario(n, memory=a.memory, blind_m=a.blind)
        print(f"== {n}: " + " -> ".join(f"{c}×{k}" for c, k in r.timeline()) +
              f" | 碰撞={r.collided} 最小净空={r.min_clearance_m:.2f}m 终点x={r.final_x:+.2f}m z={r.final_z:.1f}m")
        ok &= not r.collided
        if a.log:
            os.makedirs(a.log, exist_ok=True)
            with open(os.path.join(a.log, f"sim_{n}.jsonl"), "w", encoding="utf-8") as f:
                for t in r.trace:
                    f.write(json.dumps(t, ensure_ascii=False) + "\n")
    return 0 if ok else 1


def _cmd_metrics(a) -> int:
    from .metrics import compute_metrics, format_report, load_labels_csv, read_jsonl
    m = compute_metrics(read_jsonl(a.log), load_labels_csv(a.labels) if a.labels else None)
    print(format_report(m))
    if a.json:
        print(json.dumps(m, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="blindnav_pc", description="BlindNav PC 闭环 Demo V0.1")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="在视频/摄像头上运行完整流水线")
    g = r.add_mutually_exclusive_group()
    g.add_argument("--video"), g.add_argument("--webcam", type=int, nargs="?", const=0)
    r.add_argument("--imu", help="imu.csv：t_ms,gx,gy,gz（相机坐标系重力）或 Android 原始 t_ns,sensor_type,x,y,z,w"
                   "（优先取 TYPE_GRAVITY）")
    r.add_argument("--camera", help="camera.json (fx,fy,cx,cy)；缺省按 FOV 估计")
    r.add_argument("--orientation", help="orientation.csv: t_ms,qx,qy,qz,qw (相机→世界，Z 向上) 或 t_ms,heading_deg；"
                   "用于 BEV 记忆自运动补偿 + RECENTER 的 PDR；缺省则历史被大幅缩短、不做回正")
    r.add_argument("--steps", help="steps.csv: 每个步伐事件一行 t_ms（TYPE_STEP_DETECTOR）；缺省则只旋转不平移")
    r.add_argument("--cam-axes", choices=["opencv", "opengl"], default="opencv", dest="cam_axes",
                   help="orientation.csv 的相机轴约定；Android/ARCore 后置摄像头用 opengl")
    r.add_argument("--height", type=float, help="相机离地高度(m)，默认 1.20")
    r.add_argument("--config", help="覆盖默认参数的 JSON")
    r.add_argument("--seg", default="hf", help="hf | hf:<model_id> | onnx:<path> | torchscript:<path> | const")
    r.add_argument("--depth-size", type=int, default=None, dest="depth_size", help="深度模型输入短边(14 的倍数)，越小越快")
    r.add_argument("--out", default="out")
    r.add_argument("--max-frames", type=int, default=None, dest="max_frames")
    r.add_argument("--stride", type=int, default=1, help="每 N 帧处理 1 帧")
    r.add_argument("--labels", help="labels.csv (t_ms,should_stop) 用于误停/漏停")
    r.add_argument("--no-gui", action="store_true", dest="no_gui")
    r.add_argument("--save-video", action="store_true", dest="save_video")
    r.set_defaults(fn=_cmd_run)
    s = sub.add_parser("sim", help="2D 运动学仿真闭环")
    s.add_argument("scenario", nargs="?", default="all")
    s.add_argument("--log", help="保存逐帧日志目录")
    s.add_argument("--memory", default="full", choices=["oracle", "full", "rot_only", "none"],
                   help="oracle=只看当前帧；full=陀螺+步伐；rot_only=只陀螺；none=无自运动输入")
    s.add_argument("--blind", type=float, default=0.6, help="相机近场盲区(m)")
    s.set_defaults(fn=_cmd_sim)
    m = sub.add_parser("metrics", help="从日志计算 误停率/漏停率/指令翻转次数/延迟")
    m.add_argument("log"), m.add_argument("--labels"), m.add_argument("--json", action="store_true")
    m.set_defaults(fn=_cmd_metrics)
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
