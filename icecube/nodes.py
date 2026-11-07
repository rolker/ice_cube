"""The node estimator, after CUBE (Calder & Mayer 2003) with one hypothesis.

Grid. Nodes at x = x0 + i*cell, y = y0 + j*cell. A sounding contributes to every
node within `capture_radius` of it.

Propagation. A sounding with vertical sigma sz, horizontal sigma sh, at horizontal
distance d from the node, is given at the node the variance

    v = sz^2 + (slope * (d + sh))^2

i.e. the surface may differ from the sounding by `slope` metres per metre of
horizontal separation (one sigma), and the sounding's own horizontal uncertainty adds
to the separation. This replaces CUBE's IHO-order-based inflation, which has no meaning
for a 1 cm RTK pole. In addition each sounding carries the bias uncertainty sb of its
pass (the crossover posterior sigma, or the prior where no adjustment was possible).

Update. Soundings are applied in time order (file order when there is no time). The
node keeps the weighted mean of its soundings with weights 1/v, so its estimate is

    z = sum(z_i / v_i) / sum(1 / v_i),     var_noise = 1 / sum(1 / v_i)

plus the bias part, which is shared by all soundings of one pass and so does not
average down inside a pass:

    var_bias = sum_p (W_p / W)^2 * sb_p^2       (W_p = sum of weights from pass p)

    sigma = sqrt(var_noise + var_bias)

Datum. The surface as a whole has a level uncertainty from the pass biases,
datum_sigma = sqrt(sum_p (n_p / n)^2 * sb_p^2), the same formula at survey level;
growth.difference adds it to the uncertainty of the mean growth.

Inconsistency test. Before a sounding is fused, if the node already has an estimate
and |z_i - z| > k_sigma * sqrt(var_node + v_i + sb_p^2), the node's `flags` counter
is incremented. The sounding is still fused (there is one surface by assumption; the
counter says where that assumption looked wrong). CUBE would instead open a second
hypothesis; that is the part this tool drops.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .io import Points


@dataclass
class Grid:
    x0: float
    y0: float
    cell: float
    nx: int
    ny: int

    @classmethod
    def from_extent(cls, xmin, xmax, ymin, ymax, cell):
        nx = int(np.ceil((xmax - xmin) / cell)) + 1
        ny = int(np.ceil((ymax - ymin) / cell)) + 1
        return cls(float(xmin), float(ymin), float(cell), nx, ny)

    @property
    def xs(self):
        return self.x0 + np.arange(self.nx) * self.cell

    @property
    def ys(self):
        return self.y0 + np.arange(self.ny) * self.cell

    def same(self, other):
        return (self.cell == other.cell and self.nx == other.nx and self.ny == other.ny
                and abs(self.x0 - other.x0) < 1e-9 and abs(self.y0 - other.y0) < 1e-9)

    def as_dict(self):
        return dict(x0=self.x0, y0=self.y0, cell=self.cell, nx=self.nx, ny=self.ny)


@dataclass
class Surface:
    grid: Grid
    z: np.ndarray        # (ny, nx), NaN where no sounding
    sigma: np.ndarray
    n: np.ndarray        # soundings per node
    flags: np.ndarray    # inconsistency count per node
    last_t: np.ndarray   # time of the last sounding (NaN when untimed)
    npass: np.ndarray    # passes contributing per node
    maxnr: np.ndarray = None   # largest normalised disagreement seen at the node
    datum_sigma: float = 0.0   # survey-level bias sigma, sqrt(sum_p (n_p/n)^2 sb_p^2)
    name: str = ""
    date: str = ""
    frame: str = ""

    def inside_mask(self, outline):
        """True for nodes inside the (x, y) ring `outline`."""
        from .growth import point_in_polygon
        X, Y = np.meshgrid(self.grid.xs, self.grid.ys)
        return point_in_polygon(X.ravel(), Y.ravel(), outline).reshape(X.shape)


def grid_surface(surveys: list[Points], sb_list: list[np.ndarray], grid: Grid,
                 capture_radius=0.75, slope=0.05, k_sigma=3.0, name="", date="", frame="") -> Surface:
    """Fuse the (bias-corrected) points of one or more surveys into node estimates.
    `sb_list[k]` is the per-point bias sigma of `surveys[k]` (from crossover.apply)."""
    frames = {s.frame for s in surveys}
    if len(frames) > 1:
        raise ValueError(f"gridding across frames refused: {sorted(frames)}")
    xs, ys, zs, vs, sbs, ts, pks = [], [], [], [], [], [], []
    pass_offset = 0
    for s, sb in zip(surveys, sb_list):
        if len(sb) != len(s):
            raise ValueError("sb length mismatch")
        if not np.all(np.isfinite(s.sz)) or not np.all(np.isfinite(s.sh)):
            raise ValueError(f"survey {s.survey}: fill sigma defaults first")
        xs.append(s.x); ys.append(s.y); zs.append(s.z); ts.append(s.t)
        vs.append(np.c_[s.sz, s.sh]); sbs.append(sb)
        pks.append(s.pass_id + pass_offset)
        pass_offset += int(s.pass_id.max()) + 1 if len(s) else 0
    x, y, z, t, sb, pk = map(np.concatenate, (xs, ys, zs, ts, sbs, pks))
    szsh = np.concatenate(vs)
    npts = len(z)
    # time order; untimed points keep file order after the timed ones of the same survey
    order = np.lexsort((np.arange(npts), np.where(np.isfinite(t), t, np.inf)))
    # contributions: for each point, nodes within capture radius
    r = capture_radius
    rc = int(np.ceil(r / grid.cell))
    i0 = np.round((x - grid.x0) / grid.cell).astype(int)
    j0 = np.round((y - grid.y0) / grid.cell).astype(int)
    ny, nx = grid.ny, grid.nx
    W = np.zeros((ny, nx)); WZ = np.zeros((ny, nx)); N = np.zeros((ny, nx), int)
    F = np.zeros((ny, nx), int); LT = np.full((ny, nx), np.nan); MR = np.zeros((ny, nx))
    # per-node per-pass weight sums for the bias variance
    PW = [dict() for _ in range(ny * nx)]
    di, dj = np.meshgrid(np.arange(-rc, rc + 1), np.arange(-rc, rc + 1))
    di, dj = di.ravel(), dj.ravel()
    for k in order:
        ii = i0[k] + di
        jj = j0[k] + dj
        ok = (ii >= 0) & (ii < nx) & (jj >= 0) & (jj < ny)
        if not ok.any():
            continue
        ii, jj = ii[ok], jj[ok]
        d = np.hypot(grid.x0 + ii * grid.cell - x[k], grid.y0 + jj * grid.cell - y[k])
        ok = d <= r
        if not ok.any():
            continue
        ii, jj, d = ii[ok], jj[ok], d[ok]
        sz, sh = szsh[k]
        v = sz**2 + (slope * (d + sh))**2
        sb2 = sb[k] ** 2
        for a, b, vk in zip(jj, ii, v):
            if N[a, b] > 0:
                w = W[a, b]
                pw = PW[a * nx + b]
                var_node = 1.0 / w + sum((wp / w) ** 2 * s2 for wp, s2 in pw.values())
                nr = abs(z[k] - WZ[a, b] / w) / np.sqrt(var_node + vk + sb2)
                if nr > k_sigma:
                    F[a, b] += 1
                if nr > MR[a, b]:
                    MR[a, b] = nr
            wk = 1.0 / vk
            W[a, b] += wk; WZ[a, b] += wk * z[k]; N[a, b] += 1
            pw = PW[a * nx + b]
            old = pw.get(pk[k])
            pw[pk[k]] = (wk + (old[0] if old else 0.0), sb2)
            if np.isfinite(t[k]):
                LT[a, b] = t[k]
    has = N > 0
    Z = np.full((ny, nx), np.nan)
    S = np.full((ny, nx), np.nan)
    NP = np.zeros((ny, nx), int)
    Z[has] = WZ[has] / W[has]
    for idx in zip(*np.where(has)):
        a, b = idx
        pw = PW[a * nx + b]
        w = W[a, b]
        S[a, b] = np.sqrt(1.0 / w + sum((wp / w) ** 2 * s2 for wp, s2 in pw.values()))
        NP[a, b] = len(pw)
    # survey-level datum sigma: the pass biases weighted by their share of the points
    ds = 0.0
    if npts:
        for pkey in np.unique(pk):
            m = pk == pkey
            ds += (m.sum() / npts) ** 2 * float(sb[m][0]) ** 2
    return Surface(grid=grid, z=Z, sigma=S, n=N, flags=F, last_t=LT, npass=NP, maxnr=MR,
                   datum_sigma=float(np.sqrt(ds)), name=name, date=date, frame=frame)
