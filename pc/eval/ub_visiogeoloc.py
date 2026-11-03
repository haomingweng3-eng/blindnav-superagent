"""uB-VisioGeoloc loader - STUB (not implemented, not tested against real data).

Dataset: Scalvini et al., "uB-VisioGeoloc: An image sequences dataset of pedestrian navigation including
geolocalised-inertial information and spatial sound rendering of the urban environment's obstacles", Data in Brief
53 (2024) 110088; Harvard Dataverse doi:10.7910/DVN/UYFPKM; CC BY 4.0.  Collected in Dijon (France): time-stamped
RGB-D images + GPS + IMU (acceleration, rotation) + bounding-box obstacle annotations (+ synthetic sequences from a
3D model of a Dijon place).

Intended use here (what makes it different from SANPO): a *real IMU* stream, so the gyro / step proxies used for
SANPO can be replaced by genuine sensor data, and the bounding boxes give a coarse obstacle-in-path label.

TODO when the data is downloaded (needs a manual look at the folder layout first - this stub does not guess it):
  * ``UBVisioGeolocSequence.inputs(i)`` -> ``eval.sanpo.SanpoInput``: RGB frame, gravity from the accelerometer
    low-pass / rotation vector, heading from the rotation vector;
  * ``gt(i)`` -> depth (RGB-D sensor depth; check its units and range) and a box-derived obstacle mask;
  * time sync: images, GPS and IMU come as separate time-stamped files (see the paper) - interpolate by timestamp.
"""
from __future__ import annotations


class UBVisioGeolocSequence:
    def __init__(self, sequence_dir: str):
        raise NotImplementedError(
            "uB-VisioGeoloc loader is a stub: download the dataset (doi:10.7910/DVN/UYFPKM), inspect the folder "
            "layout, then implement inputs()/gt() against eval.sanpo.SanpoInput / SanpoGT (see module docstring).")
