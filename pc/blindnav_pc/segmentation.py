"""Pluggable walkable-ground segmentation.  All segmenters return uint8 HxW, 1 = walkable ground.

Default: SegFormer-B0 (ADE20K).  NOTE: the NVIDIA SegFormer weights are released under NVIDIA's
non-commercial research licence -> fine for the demo, replace with the team's own v5 model
(ONNX / TorchScript hooks below) before any commercial use.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Sequence, Tuple

import numpy as np

DEFAULT_SEG_MODEL = "nvidia/segformer-b0-finetuned-ade-512-512"
DEFAULT_WALKABLE = ("floor", "road", "sidewalk", "path", "pavement")


class Segmenter(ABC):
    @abstractmethod
    def predict(self, frame_bgr: np.ndarray) -> np.ndarray:
        """uint8 HxW mask (same size as the frame), 1 = walkable."""


class ConstantSegmenter(Segmenter):
    """Rows below ``horizon_frac`` of the image are walkable (for tests / smoke runs)."""

    def __init__(self, horizon_frac: float = 0.5):
        self.horizon_frac = horizon_frac

    def predict(self, frame_bgr: np.ndarray) -> np.ndarray:
        h, w = frame_bgr.shape[:2]
        m = np.zeros((h, w), np.uint8)
        m[int(h * self.horizon_frac):] = 1
        return m


def match_label_ids(id2label: dict, names: Sequence[str]) -> list:
    """Label ids whose comma-separated synonyms (e.g. 'floor, flooring') contain one of ``names``."""
    wanted = {n.strip().lower() for n in names}
    ids = []
    for i, label in id2label.items():
        syn = {s.strip().lower() for s in str(label).split(",")}
        if syn & wanted:
            ids.append(int(i))
    return sorted(ids)


class HFSegFormer(Segmenter):
    def __init__(self, model_id: str = DEFAULT_SEG_MODEL, device: str = "cpu",
                 walkable_names: Optional[Sequence[str]] = None):
        import torch
        from transformers import AutoImageProcessor, AutoModelForSemanticSegmentation
        self._torch, self.device = torch, device
        self.processor = AutoImageProcessor.from_pretrained(model_id)
        self.model = AutoModelForSemanticSegmentation.from_pretrained(model_id).to(device).eval()
        self.walkable_ids = match_label_ids(self.model.config.id2label, walkable_names or DEFAULT_WALKABLE)
        if not self.walkable_ids:
            raise ValueError("none of the walkable names matched the model's id2label")

    def predict(self, frame_bgr: np.ndarray) -> np.ndarray:
        torch = self._torch
        inputs = self.processor(images=np.ascontiguousarray(frame_bgr[..., ::-1]), return_tensors="pt").to(self.device)
        with torch.inference_mode():
            logits = self.model(**inputs).logits
            logits = torch.nn.functional.interpolate(logits, size=frame_bgr.shape[:2], mode="bilinear",
                                                     align_corners=False)
            labels = logits.argmax(1)[0].cpu().numpy()
        return np.isin(labels, self.walkable_ids).astype(np.uint8)


def _prep(frame_bgr: np.ndarray, size: Tuple[int, int], mean, std) -> np.ndarray:
    import cv2
    x = cv2.resize(frame_bgr, size)[..., ::-1].astype(np.float32) / 255.0
    x = (x - np.asarray(mean, np.float32)) / np.asarray(std, np.float32)
    return np.ascontiguousarray(x.transpose(2, 0, 1)[None], dtype=np.float32)


def _to_mask(out: np.ndarray, walkable_ids: Sequence[int], frame_shape) -> np.ndarray:
    import cv2
    out = np.asarray(out)
    labels = out.argmax(1)[0] if out.ndim == 4 else (out[0] if out.ndim == 3 else out)
    m = np.isin(labels, list(walkable_ids)).astype(np.uint8)
    return cv2.resize(m, (frame_shape[1], frame_shape[0]), interpolation=cv2.INTER_NEAREST)


class OnnxSegmenter(Segmenter):
    """Hook for the team's v5 model exported to ONNX (NCHW float32 in; logits NCHW or label map out)."""

    def __init__(self, path: str, input_size: Tuple[int, int] = (512, 512), mean=(0.485, 0.456, 0.406),
                 std=(0.229, 0.224, 0.225), walkable_class_ids: Sequence[int] = (1,)):
        import onnxruntime as ort
        self.session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        self.input_size, self.mean, self.std, self.ids = input_size, mean, std, tuple(walkable_class_ids)

    def predict(self, frame_bgr: np.ndarray) -> np.ndarray:
        x = _prep(frame_bgr, self.input_size, self.mean, self.std)
        out = self.session.run(None, {self.session.get_inputs()[0].name: x})[0]
        return _to_mask(out, self.ids, frame_bgr.shape)


class TorchScriptSegmenter(Segmenter):
    """Hook for the team's v5 ``.pt`` exported with torch.jit (same pre/post-processing as ONNX)."""

    def __init__(self, path: str, input_size: Tuple[int, int] = (512, 512), mean=(0.485, 0.456, 0.406),
                 std=(0.229, 0.224, 0.225), walkable_class_ids: Sequence[int] = (1,), device: str = "cpu"):
        import torch
        self._torch, self.device = torch, device
        self.module = torch.jit.load(path, map_location=device).eval()
        self.input_size, self.mean, self.std, self.ids = input_size, mean, std, tuple(walkable_class_ids)

    def predict(self, frame_bgr: np.ndarray) -> np.ndarray:
        x = self._torch.from_numpy(_prep(frame_bgr, self.input_size, self.mean, self.std)).to(self.device)
        with self._torch.inference_mode():
            out = self.module(x)
            if isinstance(out, (tuple, list)):
                out = out[0]
        return _to_mask(out.cpu().numpy(), self.ids, frame_bgr.shape)


def make_segmenter(spec: str) -> Segmenter:
    """'hf' | 'hf:<model_id>' | 'onnx:<path>' | 'torchscript:<path>' | 'const'."""
    if spec == "const":
        return ConstantSegmenter()
    if spec == "hf":
        return HFSegFormer()
    if spec.startswith("hf:"):
        return HFSegFormer(spec[3:])
    if spec.startswith("onnx:"):
        return OnnxSegmenter(spec[5:])
    if spec.startswith("torchscript:"):
        return TorchScriptSegmenter(spec[len("torchscript:"):])
    raise ValueError(f"unknown segmenter spec {spec!r}; use hf | hf:<id> | onnx:<path> | torchscript:<path> | const")
