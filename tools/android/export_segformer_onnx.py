#!/usr/bin/env python3
"""把 SegFormer-B0(Cityscapes) 导出成手机可跑的 ONNX，供 BlindNav 影子链路使用。

为什么要有这个脚本
------------------
电脑阶段用的是 HuggingFace 官方 `nvidia/segformer-b0-finetuned-cityscapes-768-768`
（HF hub 已缓存），但手机侧要的是 ONNX。App 里 `TraversabilityShadowModel` 找的资产名固定是
`segformer_cityscapes.onnx`，输入 256x256，ImageNet 均值方差归一化，输出 [1,19,H,W] logits。

必须用 venv，不要用系统 python
------------------------------
这台 Mac 的 Homebrew python3.14 里，numpy 会先加载 /opt/homebrew/Cellar/libomp 的 libomp，
Homebrew 版 torch 又自带一份 torch/lib/libomp.dylib，同进程两份会触发
`OMP: Error #15` 并 abort（macOS 弹"Python 意外退出"）。所以：

    VENV=/Users/mima0000/Documents/Codex/2026-10-25/w-k/work/blindnav-audit/.venv-vision
    $VENV/bin/python -m pip install torch transformers onnx onnxruntime
    $VENV/bin/python tools/android/export_segformer_onnx.py \
        --out /tmp/segformer_cityscapes.onnx

为什么是 256 而不是官方 768
---------------------------
768x768 在手机上单帧 CPU 推理不现实；256 是"先看手机上到底能跑多快"的起点。
这是**降质实验**，不能拿它的数字去和电脑阶段 768 的结果直接比 accuracy，
只能比 mask 分布与 WorldPath 几何。要换尺寸用 --size，改完 App 里的常量要同步。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def guard_interpreter() -> None:
    """挡住 Homebrew 系统 python：那里 numpy 与 torch 会各带一份 libomp，import torch 直接 abort。"""
    executable = sys.executable
    if "/opt/homebrew/bin/python3" in executable or "/Cellar/python" in executable:
        print("!! 正在用 Homebrew 系统解释器：%s" % executable, file=sys.stderr)
        print("   请改用任意 venv，例如 /tmp/blindnav-export-venv/bin/python 或", file=sys.stderr)
        print("   blindnav-audit/.venv-vision/bin/python，否则会撞上 OMP Error #15 崩溃。", file=sys.stderr)
        raise SystemExit(2)


def main() -> int:
    guard_interpreter()
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="nvidia/segformer-b0-finetuned-cityscapes-768-768")
    parser.add_argument("--size", type=int, default=256, help="正方形输入边长")
    parser.add_argument("--out", required=True)
    parser.add_argument("--opset", type=int, default=17)
    parser.add_argument("--check", action="store_true", default=True,
                        help="导出后用 onnxruntime 跑一次随机输入并打印类别分布")
    args = parser.parse_args()

    import numpy as np
    import torch
    from transformers import SegformerForSemanticSegmentation, SegformerImageProcessor

    print("torch", torch.__version__, "| numpy", np.__version__, "| model", args.model)
    processor = SegformerImageProcessor.from_pretrained(args.model)
    model = SegformerForSemanticSegmentation.from_pretrained(args.model)
    model.eval()

    size = args.size
    # 归一化常数只从 processor 读，不改它：ONNX 图里不含预处理，
    # 手机侧 YuvConverter/TraversabilityShadowModel 自己 resize 到 size 并用同一组 mean/std。
    mean = list(getattr(processor, "image_mean", None) or [0.485, 0.456, 0.406])
    std = list(getattr(processor, "image_std", None) or [0.229, 0.224, 0.225])
    print("image_mean", mean, "image_std", std)

    dummy = torch.zeros(1, 3, size, size)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    started = time.time()
    torch.onnx.export(
        model,
        (dummy,),                      # forward 第一个参数就是 pixel_values，按位置传最稳
        args.out,
        opset_version=args.opset,
        input_names=["pixel_values"],
        output_names=["logits"],
        do_constant_folding=True,
        dynamo=False,                  # 用 legacy exporter：手机侧要固定形状，不需要 dynamo 路径
    )
    print("exported in %.1fs -> %s (%.1f MB)" % (
        time.time() - started, args.out, os.path.getsize(args.out) / 1e6))

    raw_labels = getattr(model.config, "id2label", None)
    if isinstance(raw_labels, dict):
        labels = {int(k): str(v) for k, v in raw_labels.items()}
    elif isinstance(raw_labels, (list, tuple)):
        labels = {i: str(v) for i, v in enumerate(raw_labels)}
    else:
        labels = {}
    meta = {
        "source_model": args.model,
        "input_name": "pixel_values",
        "input_shape": [1, 3, size, size],
        "image_mean": list(mean),
        "image_std": list(std),
        "opset": args.opset,
        "id2label": {str(k): v for k, v in labels.items()},
        "torch_version": torch.__version__,
        "note": "手机侧 TraversabilityShadowModel 用 road/sidewalk 两类作为 traversable；"
                "label 顺序以本文件 id2label 为准，真机日志里的 seg_class_hist 要和它对得上。",
    }
    with open(os.path.splitext(args.out)[0] + ".json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("labels:", json.dumps(labels, ensure_ascii=False)[:400])

    if args.check:
        import onnxruntime as ort

        session = ort.InferenceSession(args.out, providers=["CPUExecutionProvider"])
        print("ort inputs:", [(i.name, i.shape) for i in session.get_inputs()])
        print("ort outputs:", [(o.name, o.shape) for o in session.get_outputs()])
        # 把实测输出形状写回 sidecar：SegFormer 的 logits 是 1/4 分辨率（256 输入 → 64x64 输出），
        # 手机侧按 sqrt(hw) 推边长，但这个事实必须留在资产旁边，不能靠下一个人重新发现。
        try:
            meta_path = Path(os.path.splitext(args.out)[0] + ".json")
            meta["output_shape"] = [list(o.shape) for o in session.get_outputs()]
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            print("sidecar 回写失败：%s" % e, file=sys.stderr)
        rng = np.random.default_rng(0)
        raw = rng.integers(0, 256, size=(size, size, 3), dtype=np.uint8).astype(np.float32) / 255.0
        normalized = (raw - np.array(mean, dtype=np.float32)) / np.array(std, dtype=np.float32)
        planar = np.transpose(normalized, (2, 0, 1))[None].astype(np.float32)
        t0 = time.time()
        logits = session.run(["logits"], {"pixel_values": planar})[0]
        print("random-input inference %.0f ms on Mac CPU, logits shape %s" % (
            (time.time() - t0) * 1000, logits.shape))
        classes = logits.argmax(axis=1)[0]
        counts = {labels.get(int(c), str(c)): int((classes == c).sum()) for c in np.unique(classes)}
        print("class pixel counts on random noise:", counts)
        if logits.shape[1] != len(labels) and labels:
            print("!! 输出通道数与 id2label 数量不一致，手机侧 label 顺序映射需要重新核对", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
