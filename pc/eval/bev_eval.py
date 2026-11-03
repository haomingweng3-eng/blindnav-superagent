"""Oracle BEV (GT depth + GT segmentation) vs predicted BEV, and Oracle-vs-Pred planner actions."""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np

from blindnav_pc.bev import BEVMemory, build_bev_frame
from blindnav_pc.config import FREE, OCCUPIED, UNKNOWN, BEVConfig, Config
from blindnav_pc.planner import PlanResult, VFHPlanner

from .sanpo import WALKABLE_IDS

ACTIONS = ("FORWARD", "LEFT", "RIGHT", "STOP")


def gt_walkable_mask(seg: np.ndarray) -> np.ndarray:
    return np.isin(seg, WALKABLE_IDS).astype(np.uint8)


def oracle_evidence(depth_gt_m: np.ndarray, seg_gt: np.ndarray, K: np.ndarray, up: np.ndarray, h: float,
                    cfg: Config):
    return build_bev_frame(depth_gt_m, gt_walkable_mask(seg_gt), K, up, h, cfg.bev)


def oracle_states(depth_gt_m, seg_gt, K, up, h, cfg: Config) -> np.ndarray:
    """Single-frame Oracle BEV: GT depth + GT seg through the *same* rasteriser, thresholds and footprint rule."""
    free, occ = oracle_evidence(depth_gt_m, seg_gt, K, up, h, cfg)
    return BEVMemory(cfg.bev).update(free, occ, None)


def nearest_obstacle_m(states: np.ndarray, cfg: BEVConfig, half_width_m: float) -> float:
    """Distance of the nearest OCCUPIED cell inside the swept corridor |x| <= half_width_m (inf if none)."""
    xs = (np.arange(cfg.nx) + 0.5) * cfg.cell_m - cfg.lateral_half_m
    cols = np.abs(xs) <= half_width_m
    rows = np.nonzero((states[:, cols] == OCCUPIED).any(axis=1))[0]
    return float((rows[0] + 0.5) * cfg.cell_m) if len(rows) else float("inf")


def corridor_width_m(states: np.ndarray, cfg: BEVConfig, at_z_m: float = 1.5) -> float:
    """Width of the contiguous *known free* run containing x = 0 on the row at ``at_z_m`` (0 if the centre is not
    known-free there)."""
    iz = int(np.clip(at_z_m / cfg.cell_m, 0, cfg.nz - 1))
    row = states[iz]
    c = cfg.nx // 2
    if row[c] != FREE and row[c - 1] != FREE:
        return 0.0
    l = c - 1 if row[c - 1] == FREE else c
    r = l
    while l - 1 >= 0 and row[l - 1] == FREE:
        l -= 1
    while r + 1 < cfg.nx and row[r + 1] == FREE:
        r += 1
    return float((r - l + 1) * cfg.cell_m)


def bev_compare(oracle: np.ndarray, pred: np.ndarray, cfg: Config, max_z_m: float = 4.0,
                half_width_m: Optional[float] = None) -> Dict[str, float]:
    """Pred vs Oracle on one frame.

    * ``false_free_rate``   : of the Oracle-OCCUPIED cells, the share Pred calls FREE (the dangerous error);
    * ``miss_rate``         : Oracle-OCCUPIED cells that Pred does not call OCCUPIED (FREE or UNKNOWN);
    * ``false_occ_rate``    : of the Oracle-FREE cells, the share Pred calls OCCUPIED (causes needless stops);
    * ``nearest_err_m``     : Pred - Oracle nearest obstacle distance in the swept corridor (NaN unless both see one);
    * ``nearest_miss``      : Oracle sees a corridor obstacle, Pred sees none;
    * ``width_err_m``       : Pred - Oracle known-free corridor width at 1.5 m.
    """
    b = cfg.bev
    nz = int(min(b.nz, round(max_z_m / b.cell_m)))
    o, p = oracle[:nz], pred[:nz]
    occ_o, free_o = o == OCCUPIED, o == FREE
    hw = cfg.planner.half_width_m if half_width_m is None else half_width_m
    no, np_ = nearest_obstacle_m(oracle, b, hw), nearest_obstacle_m(pred, b, hw)
    wo, wp = corridor_width_m(oracle, b), corridor_width_m(pred, b)
    return {
        "n_occ_oracle": int(occ_o.sum()),
        "false_free_rate": float((p[occ_o] == FREE).mean()) if occ_o.any() else float("nan"),
        "miss_rate": float((p[occ_o] != OCCUPIED).mean()) if occ_o.any() else float("nan"),
        "false_occ_rate": float((p[free_o] == OCCUPIED).mean()) if free_o.any() else float("nan"),
        "nearest_oracle_m": no if np.isfinite(no) else float("nan"),
        "nearest_err_m": float(np_ - no) if np.isfinite(no) and np.isfinite(np_) else float("nan"),
        "nearest_miss": bool(np.isfinite(no) and not np.isfinite(np_)),
        "width_oracle_m": wo, "width_err_m": float(wp - wo),
    }


def plan_action(plan: PlanResult, cfg: Config) -> str:
    """Per-frame action from a plan alone (no debounce): STOP on a dead end or an imminent corridor obstacle,
    otherwise FORWARD / LEFT / RIGHT by the chosen side."""
    if plan.dead_end or (plan.corridor_blocked and plan.corridor_min_dist_m < cfg.policy.imminent_dist_m):
        return "STOP"
    return {"center": "FORWARD", "left": "LEFT", "right": "RIGHT"}[plan.best_side]


def plan_states(planner: VFHPlanner, states: np.ndarray, blind_m: Optional[float], cfg: Config) -> str:
    return plan_action(planner.plan(states, blind_m=blind_m), cfg)


def action_comparison(oracle_actions, pred_actions) -> Dict[str, object]:
    """Oracle-vs-Pred action agreement.  ``unsafe_forward``: Pred says FORWARD while Oracle says STOP/LEFT/RIGHT
    (the walker would be sent into something the Oracle sees); ``false_stop``: Pred STOP while Oracle moves."""
    o, p = list(oracle_actions), list(pred_actions)
    n = len(o)
    conf = {a: {b: 0 for b in ACTIONS} for a in ACTIONS}
    for a, b in zip(o, p):
        conf[a][b] += 1
    need = [k for k in range(n) if o[k] != "FORWARD"]
    move = [k for k in range(n) if o[k] != "STOP"]
    return {"n": n,
            "agreement": float(np.mean([a == b for a, b in zip(o, p)])) if n else float("nan"),
            "unsafe_forward_rate": float(np.mean([p[k] == "FORWARD" for k in need])) if need else float("nan"),
            "false_stop_rate": float(np.mean([p[k] == "STOP" for k in move])) if move else float("nan"),
            "wrong_side_rate": _wrong_side(o, p),
            "confusion": conf}


def _wrong_side(o, p) -> float:
    ks = [k for k in range(len(o)) if o[k] in ("LEFT", "RIGHT") and p[k] in ("LEFT", "RIGHT")]
    return float(np.mean([o[k] != p[k] for k in ks])) if ks else float("nan")
