# Offline real-eval scripts (from BlindNav package 2026-11-03)

Merged from `BlindNav_current_20261103/real-eval/` into this tree so `pc-v0.1` can run
or inspect the historical offline eval without the Mac.

Layout:
- `scripts/` — Python eval/train helpers (SANPO SegFormer, Cityscapes, UrbanEgo occupancy,
  door detection, BLV, multi-head, …). Paths inside scripts still assume a sibling `data/`
  and `models/` that are **not** in this repo (see DOWNLOAD_LINKS.md).
- `manifests/` — SANPO session ids, OpenImages door ids, models/assets manifests.
- `results/` — frozen JSON/CSV summaries from the Mac runs (evidence only).
- `README_PACKAGE.md` / `DOWNLOAD_LINKS.md` — package inventory and public download URLs.

Relation to `pc/eval/`:
- `pc/eval/` is the closed-loop BlindNav PC harness (ground/BEV/planner/ego on SANPO).
- `tools/eval/scripts/` is the older real-eval suite (seg training, door, occupancy).
  Do not import the latter into `pc` pytest; they need torch/ultralytics and large data.

## Already on this box
- Depth Anything V2 Small HF cache
- SegFormer-B0 Cityscapes HF base (downloaded into HF hub cache)
- Small SANPO Real/Synth samples under `pc/data/sanpo_*` (not the 6TB bucket)

## Still missing (no public / too large / Mac-only)
- `urbanego/` local HoloLens (~2GB)
- Finetuned weights ~145MB (`manifests/models_manifest.txt`)
- ASR/TTS sherpa assets ~546MB
- Custom `yolo-seg-int8.onnx` (~70MB)
- Full SANPO / Cityscapes parquet / BLV / OpenImages dumps (~6.7GB)

DeepSeek key in the package is redacted (`sk-REDACTED_ROTATE_BEFORE_USE`); rotate before any use.
