"""VFH+-style heading planner on the BEV grid.

Grid convention: states[iz, ix]; iz = 0 is the nearest row, ix = 0 the leftmost column.
Heading angle theta: 0 = straight ahead, positive = to the RIGHT.
Score = alpha*clear_norm + beta*width_norm - gamma*heading_error_norm - delta*unknown_ratio.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .config import BEVConfig, FREE, OCCUPIED, PlannerConfig, UNKNOWN


@dataclass
class PlanResult:
    headings_deg: np.ndarray
    scores: np.ndarray
    clear_m: np.ndarray
    free_width_m: np.ndarray
    unknown_ratio: np.ndarray
    best_heading_deg: float
    best_side: str                  # 'left' | 'center' | 'right'
    corridor_blocked: bool
    corridor_min_dist_m: float      # nearest occupied cell inside the collision corridor (inf if none)
    dead_end: bool                  # no heading is passable
    passable: np.ndarray            # bool per heading
    left_clear_m: float = 0.0
    center_clear_m: float = 0.0
    right_clear_m: float = 0.0

    def clear_toward(self, heading_deg: float) -> float:
        """Conservative clear distance toward an arbitrary heading (min of the two nearest candidates)."""
        h = np.clip(heading_deg, self.headings_deg[0], self.headings_deg[-1])
        idx = np.argsort(np.abs(self.headings_deg - h))[:2]
        return float(self.clear_m[idx].min())

    def to_log(self) -> dict:
        return {"best_heading_deg": float(self.best_heading_deg), "best_side": self.best_side,
                "corridor_blocked": bool(self.corridor_blocked),
                "corridor_min_dist_m": None if not np.isfinite(self.corridor_min_dist_m) else round(float(self.corridor_min_dist_m), 3),
                "dead_end": bool(self.dead_end),
                "left_clear_m": round(self.left_clear_m, 3), "center_clear_m": round(self.center_clear_m, 3),
                "right_clear_m": round(self.right_clear_m, 3)}


class VFHPlanner:
    def __init__(self, cfg: Optional[PlannerConfig] = None, bev: Optional[BEVConfig] = None):
        self.cfg = cfg or PlannerConfig()
        self.bev = bev or BEVConfig()
        c = self.cfg
        n = int(round(c.heading_max_deg / c.heading_step_deg))
        self.headings = np.arange(-n, n + 1) * c.heading_step_deg
        self._rays = [self._precompute(t) for t in self.headings]

    # ---- geometry helpers -------------------------------------------------
    def _cell(self, x, z):
        b = self.bev
        return (np.floor(z / b.cell_m).astype(int), np.floor((x + b.lateral_half_m) / b.cell_m).astype(int))

    def _precompute(self, theta_deg: float):
        c, b = self.cfg, self.bev
        th = np.deg2rad(theta_deg)
        d = np.array([np.sin(th), np.cos(th)])
        perp = np.array([np.cos(th), -np.sin(th)])
        s_all = np.arange(0.0, b.forward_m, c.ray_step_m)
        n_o = int(np.ceil(2 * c.half_width_m / b.cell_m)) + 1
        offs = np.linspace(-c.half_width_m, c.half_width_m, n_o)
        X = s_all[:, None] * d[0] + offs[None, :] * perp[0]
        Z = s_all[:, None] * d[1] + offs[None, :] * perp[1]
        iz, ix = self._cell(X, Z)
        inside = (iz >= 0) & (iz < b.nz) & (ix >= 0) & (ix < b.nx)
        izc, ixc = self._cell(s_all * d[0] + 1e-9, s_all * d[1] + 1e-9)   # centre line (epsilon avoids -0 cells)
        ray_in = (izc >= 0) & (izc < b.nz) & (ixc >= 0) & (ixc < b.nx)
        # ray ends where the centre line leaves the grid
        if not ray_in.all():
            n_valid = int(np.argmax(~ray_in))
        else:
            n_valid = len(s_all)
        return dict(s=s_all[:n_valid], iz=iz[:n_valid], ix=ix[:n_valid], inside=inside[:n_valid], theta=th)

    # ---- per-heading evaluation ---------------------------------------
    def _eval_ray(self, states: np.ndarray, r: dict, blind_m: Optional[float] = None):
        c, b = self.cfg, self.bev
        blind = c.near_unknown_ignore_m if blind_m is None else blind_m
        s, iz, ix, inside = r["s"], r["iz"], r["ix"], r["inside"]
        if len(s) == 0:
            return 0.0, 0.0, 1.0
        st = np.full(iz.shape, FREE, dtype=np.uint8)
        st[inside] = states[iz[inside], ix[inside]]
        occ = ((st == OCCUPIED) & inside).any(axis=1)
        unk = ((st == UNKNOWN) & inside).mean(axis=1) >= 0.5
        unk_eff = unk & (s >= blind)
        clear = float(s[-1] + c.ray_step_m)
        if occ.any():
            clear = float(s[int(np.argmax(occ))])
        # unknown run that is long enough also ends the clear distance
        k = int(round(c.unknown_block_m / c.ray_step_m))
        if k > 0 and unk_eff.any():
            run = np.convolve(unk_eff.astype(int), np.ones(k, dtype=int), mode="valid")
            hit = np.nonzero(run >= k)[0]
            if len(hit):
                clear = min(clear, float(s[hit[0]]))
        sel = (s >= blind) & (s <= 3.0)
        unknown_ratio = float(unk[sel].mean()) if sel.any() else 0.0
        return clear, 0.0, unknown_ratio

    def _free_width(self, states: np.ndarray, theta_deg: float) -> float:
        c, b = self.cfg, self.bev
        th = np.deg2rad(theta_deg)
        s = min(c.free_width_at_m, b.forward_m - b.cell_m)
        iz = int(np.floor(s * np.cos(th) / b.cell_m))
        ix0 = int(np.floor((s * np.sin(th) + b.lateral_half_m) / b.cell_m))
        if not (0 <= iz < b.nz and 0 <= ix0 < b.nx) or states[iz, ix0] != FREE:
            return 0.0
        l = ix0
        while l - 1 >= 0 and states[iz, l - 1] == FREE:
            l -= 1
        r = ix0
        while r + 1 < b.nx and states[iz, r + 1] == FREE:
            r += 1
        return (r - l + 1) * b.cell_m

    def corridor(self, states: np.ndarray):
        """(blocked, nearest occupied distance) in the straight collision corridor."""
        c, b = self.cfg, self.bev
        x0 = int(np.floor((-c.half_width_m + b.lateral_half_m) / b.cell_m + 1e-9))
        x1 = int(np.ceil((c.half_width_m + b.lateral_half_m) / b.cell_m - 1e-9))
        zn = int(np.ceil(c.corridor_len_m / b.cell_m - 1e-9))
        sub = states[:zn, x0:x1] == OCCUPIED
        if not sub.any():
            return False, float("inf")
        z = np.nonzero(sub.any(axis=1))[0][0]
        return True, float(z * b.cell_m)

    def side_of(self, heading_deg: float) -> str:
        """'left' | 'center' | 'right' for a heading (use axis-frame heading = best + yaw offset)."""
        if abs(heading_deg) < self.cfg.side_deg:
            return "center"
        return "right" if heading_deg > 0 else "left"

    def desired_side(self, plan: "PlanResult", yaw_deg: float = 0.0, target_axis_deg: float = 0.0) -> str:
        """Side for the policy, in the AXIS frame (yaw_deg = walker heading offset from the walking axis).

        If the swept corridor toward the axis target is passable, the walker is free to realign
        -> report the target's side (center when target is 0).  Otherwise follow the planner's best
        heading.  This prevents turning back toward an obstacle that is still beside the walker.
        """
        rel = float(np.clip(target_axis_deg - yaw_deg, -self.cfg.heading_max_deg, self.cfg.heading_max_deg))
        if plan.clear_toward(rel) >= max(self.cfg.passable_clear_m, float(plan.clear_m.max()) - 0.3):
            return self.side_of(target_axis_deg)
        h = plan.best_heading_deg + yaw_deg      # axis is blocked: any non-zero deviation counts as a side
        return "center" if abs(h) < 0.5 else ("right" if h > 0 else "left")

    def planner_target(self, yaw_deg: float = 0.0, target_axis_deg: float = 0.0) -> float:
        """Target heading in the walker frame for plan()."""
        return float(np.clip(target_axis_deg - yaw_deg, -self.cfg.heading_max_deg, self.cfg.heading_max_deg))

    # ---- main ---------------------------------------------------------
    def plan(self, states: np.ndarray, target_heading_deg: float = 0.0,
             prev_heading_deg: float = 0.0, blind_m: Optional[float] = None) -> PlanResult:
        """blind_m: forward distance below which the camera cannot see the ground (unknown there does not block)."""
        c, b = self.cfg, self.bev
        n = len(self.headings)
        clear = np.zeros(n)
        width = np.zeros(n)
        unk = np.zeros(n)
        for i, r in enumerate(self._rays):
            clear[i], _, unk[i] = self._eval_ray(states, r, blind_m)
            width[i] = self._free_width(states, float(self.headings[i]))
        err = np.abs(self.headings - target_heading_deg) / max(c.heading_max_deg, 1e-6)
        scores = (c.alpha * np.clip(clear / b.forward_m, 0, 1) + c.beta * np.clip(width / 1.2, 0, 1)
                  - c.gamma * err - c.delta * unk)
        passable = clear >= c.passable_clear_m
        dead_end = not passable.any()
        # best = highest score; ties -> smaller error, then keep the previous side
        order = np.lexsort((np.abs(self.headings - prev_heading_deg), err, -np.round(scores, 6)))
        best = float(self.headings[order[0]])
        blocked, dist = self.corridor(states)
        side = "center" if abs(best) < c.side_deg else ("right" if best > 0 else "left")

        def at(deg):
            return float(clear[int(np.argmin(np.abs(self.headings - deg)))])
        return PlanResult(self.headings.copy(), scores, clear, width, unk, best, side, blocked, dist,
                          dead_end, passable, at(-20), at(0), at(20))
