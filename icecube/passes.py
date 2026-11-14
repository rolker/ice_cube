"""Pass segmentation: one pass = one stretch of points that share an RTK vertical bias.

A new pass starts at any of (all settable in the survey's `[surveys.pass_rule]`):
  gap_s        a time gap longer than this between consecutive points (needs time)
  jump_m       a horizontal jump longer than this between consecutive points
  markers      a point whose remark contains one of these substrings (case insensitive)
  fixed_only   points whose quality is not 4 (RTK fixed) are dropped and break the pass
  max_s        a pass longer than this is cut into pieces of at most max_s seconds
  settle_s     the first settle_s seconds of every pass are dropped (RTK right after a
               re-fix can be decimetres off while the sigma still says 1 cm)
  min_points   passes with fewer points are dropped
  from_file    keep the pass ids the reader gave (the soundings table carries its own
               passes, cut by fix stretches); only min_points applies

Points keep their file order. The result is `pass_id` on the table (0, 1, 2, ... per
survey) plus a pass table for the report.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .io import Points

DEFAULT_RULE = dict(gap_s=120.0, jump_m=3.0, markers=(), fixed_only=False,
                    max_s=None, settle_s=0.0, min_points=1, from_file=False)


@dataclass
class PassInfo:
    survey: str
    pass_id: int
    n: int
    t0: float
    t1: float
    length_m: float


def segment(pts: Points, rule: dict | None = None):
    """Assign pass ids to a survey's points. Returns (Points, [PassInfo])."""
    r = dict(DEFAULT_RULE)
    r.update(rule or {})
    n = len(pts)
    if n == 0:
        return pts, []
    order = np.argsort(pts.seq, kind="stable")
    p = pts.subset(order)
    if r["from_file"]:
        if np.any(p.pass_id < 0):
            raise ValueError(f"survey {p.survey}: from_file needs a pass id on every point")
        infos, keep = [], np.zeros(n, bool)
        for k in np.unique(p.pass_id):
            idx = np.where(p.pass_id == k)[0]
            if len(idx) < int(r["min_points"]):
                continue
            keep[idx] = True
            t = p.t[idx]
            fin = np.isfinite(t)
            infos.append(PassInfo(p.survey, int(k), len(idx), float(t[fin].min()) if fin.any() else float("nan"),
                                  float(t[fin].max()) if fin.any() else float("nan"),
                                  float(np.sum(np.hypot(np.diff(p.x[idx]), np.diff(p.y[idx]))))))
        return p.subset(keep), infos
    keep = np.ones(n, bool)
    if r["fixed_only"]:
        keep &= p.quality == 4
    has_t = np.isfinite(p.t)
    starts = np.zeros(n, bool)
    starts[0] = True
    dt = np.diff(p.t)
    if r["gap_s"] is not None:
        starts[1:] |= np.isfinite(dt) & (dt > float(r["gap_s"]))
    if r["jump_m"] is not None:
        d = np.hypot(np.diff(p.x), np.diff(p.y))
        starts[1:] |= d > float(r["jump_m"])
    for m in r["markers"] or ():
        starts |= np.array([m.lower() in str(s).lower() for s in p.remark])
    # a dropped point (non-fixed) breaks the pass: the next kept point starts a new one
    if r["fixed_only"]:
        prev_dropped = np.r_[False, ~keep[:-1]]
        starts |= prev_dropped
    pid = np.cumsum(starts) - 1
    # settle: drop the start of each real pass (before any max_s cut, which is not a re-fix)
    if r["settle_s"] and np.any(has_t):
        for k in np.unique(pid):
            idx = np.where(pid == k)[0]
            t = p.t[idx]
            if np.all(np.isfinite(t)):
                keep[idx] &= t - t[0] >= float(r["settle_s"])
    # cut long passes
    if r["max_s"]:
        new = np.zeros(n, bool)
        for k in np.unique(pid):
            idx = np.where((pid == k) & keep)[0]
            t = p.t[idx]
            if len(idx) == 0 or not np.all(np.isfinite(t)):
                continue
            piece = np.floor((t - t[0]) / float(r["max_s"])).astype(int)
            new[idx[1:]] |= np.diff(piece) > 0
        pid = np.cumsum(starts | new) - 1
    # renumber after dropping, enforce min_points
    pid = np.where(keep, pid, -1)
    ids = [k for k in np.unique(pid) if k >= 0]
    out_pid = np.full(n, -1, int)
    infos = []
    nxt = 0
    for k in ids:
        idx = np.where(pid == k)[0]
        if len(idx) < int(r["min_points"]):
            continue
        out_pid[idx] = nxt
        t = p.t[idx]
        length = float(np.sum(np.hypot(np.diff(p.x[idx]), np.diff(p.y[idx]))))
        infos.append(PassInfo(p.survey, nxt, len(idx),
                              float(np.nanmin(t)) if np.any(np.isfinite(t)) else float("nan"),
                              float(np.nanmax(t)) if np.any(np.isfinite(t)) else float("nan"), length))
        nxt += 1
    p.pass_id = out_pid
    return p.subset(out_pid >= 0), infos
