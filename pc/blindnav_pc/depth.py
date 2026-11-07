"""Monocular depth wrapper: Depth-Anything-V2-**Small** only (Apache-2.0).

The network outputs *relative affine-invariant inverse depth* q (larger = nearer); the scale and
shift are recovered per frame by ``ground.py`` (flat-ground calibration).  Base/Large/Giant
checkpoints are CC-BY-NC, so they are refused unless ``BLINDNAV_ALLOW_NONSMALL_DEPTH=1`` is set.
"""
from __future__ import annotations

import os
from typing import Callable, Optional, Union

import numpy as np

DEFAULT_MODEL_ID = "depth-anything/Depth-Anything-V2-Small-hf"
_FORBIDDEN = ("base", "large", "giant")


class DepthEstimator:
    def __init__(self, model_id: str = DEFAULT_MODEL_ID, device: str = "cpu",
                 input_size: Optional[int] = None, *, revision: Optional[str] = None,
                 local_snapshot: Optional[str] = None):
        """input_size: shorter-side resolution fed to the network (multiple of 14); smaller = faster."""
        if any(w in model_id.lower() for w in _FORBIDDEN) and os.environ.get("BLINDNAV_ALLOW_NONSMALL_DEPTH") != "1":
            raise ValueError(
                f"{model_id!r}: only Depth-Anything-V2-Small is Apache-2.0; Base/Large/Giant are CC-BY-NC. "
                "Set BLINDNAV_ALLOW_NONSMALL_DEPTH=1 to override for non-commercial experiments.")
        if input_size is not None and input_size % 14:
            raise ValueError("input_size must be a multiple of 14")
        import torch
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation
        self._torch = torch
        self.device = device
        self.input_size = input_size
        source = local_snapshot or model_id
        self.processor = AutoImageProcessor.from_pretrained(source, revision=revision,
                                                             local_files_only=bool(local_snapshot))
        self.model = AutoModelForDepthEstimation.from_pretrained(source, revision=revision,
                                                                 local_files_only=bool(local_snapshot)).to(device).eval()

    def predict(self, frame_bgr: np.ndarray) -> np.ndarray:
        """float32 HxW inverse-depth-like map (larger = nearer), bilinear-resized to the frame size."""
        kwargs = {}
        if self.input_size is not None:
            kwargs["size"] = {"shortest_edge": self.input_size}
        rgb = np.ascontiguousarray(frame_bgr[..., ::-1])
        inputs = self.processor(images=rgb, return_tensors="pt", **kwargs).to(self.device)
        torch = self._torch
        with torch.inference_mode():
            out = self.model(**inputs).predicted_depth            # (1, h, w)
            out = torch.nn.functional.interpolate(out[:, None], size=frame_bgr.shape[:2],
                                                  mode="bilinear", align_corners=False)
        return out[0, 0].float().cpu().numpy().astype(np.float32)


class SyntheticDepth:
    """Test double: returns a fixed array or ``fn(frame)``."""

    def __init__(self, source: Union[np.ndarray, Callable[[np.ndarray], np.ndarray]]):
        self.source = source

    def predict(self, frame_bgr: Optional[np.ndarray] = None) -> np.ndarray:
        x = self.source(frame_bgr) if callable(self.source) else self.source
        return np.asarray(x, dtype=np.float32)

    __call__ = predict
