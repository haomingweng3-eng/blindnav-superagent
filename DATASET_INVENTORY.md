# DATASET_INVENTORY — 2026-11-03

All paths are local. No dataset was re-downloaded for this round.

| Dataset | Source / license | Local path | Size / subset | GT | Can test | Cannot test |
|---|---|---|---|---|---|---|
| SANPO-Real | google-research-datasets/sanpo_dataset; CC BY 4.0 | `~/blindnav-real-eval/data/sanpo` | 3 test sessions × 15 frames + masks; 202 MB | Per-pixel semantic labels (road, sidewalk, curb, terrain, crosswalk, etc.) | General traversability, tactile-vs-general corridor, centerline | Obstacles boxes, target/door, speech usefulness |
| UrbanEgo run7 | Zenodo 23062929; CC BY 4.0 | `~/blindnav-real-eval/data/urbanego/run7_20260714_171427` | 1 pedestrian-heavy run; 900 MB + 958 1 s frames + 30 s clip | YOLO11x+BoT-SORT tracks, depth, heading, IMU | Real-video obstacle recall/false STOP, dynamic crossing, temporal policy | Human-verified boxes, target/door, BLV object taxonomy |
| BLV-Road-Nav-Accessibility | github.com/Shohan29531/BLV-Road-Nav-Accessibility; no license file | `~/blindnav-real-eval/data/BLV-Road-Nav-Accessibility` | 21 videos / 31 segments / 1795 JPEG frames; 256 MB | Per-frame object presence (90 categories) | BLV-relevant object coverage, COCO class gaps | Box localization, corridor overlap, precision/recall by box |
| GuideDog | huggingface.co/datasets/kjunh/GuideDog; CC BY-NC 4.0, gated | `~/blindnav-real-eval/data/GuideDog` (code only) | repo 7.2 MB; gold 2106 requires HF token | Human-verified image-description pairs | Scene usefulness, BLV guidance | Not downloaded; no metrics reported |
| Signboard Datasets | github.com/ervinyo/Signboard-datasets; MIT code | `~/blindnav-real-eval/data/Signboard-datasets` (code only) | repo 39 MB; data on Google Drive | 14 logo classes, VOC boxes | Target search (KFC/Starbucks/7-ELEVEN) | Data not downloaded; no metrics reported |
| DeepDoors2 | github.com/gasparramoa/DeepDoors2; no license file | `~/blindnav-real-eval/data/DeepDoors2` (code only) | repo 2.8 MB; data on Google Drive | Door segmentation/classification | Automatic entrance proposal | Data not downloaded; no metrics reported |
| Mapillary Vistas | mapillary.com; login/license required | none | — | Pixel-level road/curb/sidewalk | General traversability | Login/license gate; not used |
| Mobilio | Nature paper / public code | none | — | Design and user-study reference | Design comparison | No local data |

## Existing BlindNav data

- `app/src/test/resources/last10m/*`: 166 Last10m JVM tests, real street-view
  replay assets, no new relation GT.
- `app/src/main/assets/demo/last10m/*.jpg`: 6 real street-view images with
  replay facts; not a full benchmark.
- `eval/core_v1/*`: synthetic policy cases from the previous round; kept only
  as engineering regression, not real-world evidence.

## Evidence limitations

- SANPO masks provide segmentation GT but no obstacle boxes.
- UrbanEgo detections are model-generated, not human-verified; they are used as
  a real-video proxy, not ground truth.
- BLV labels are frame-level presence, not boxes; no precision can be computed
  from them.
- GuideDog/Signboard/DeepDoors2 data requires external access or Google Drive;
  not downloaded, so no fake metrics.
