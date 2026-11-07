"""Differences between surfaces on one grid, masks, volumes and fill-to-level.

difference(a, b): b minus a per node, sigma = sqrt(sigma_a^2 + sigma_b^2), only where
both have soundings. The mean growth over the mask gets sigma_of_mean =
sqrt(noise part + datum_a^2 + datum_b^2): node noise averages down over nodes, the
pass-bias (datum) part does not. Surfaces in different frames are refused unless an explicit
`offset` (added to a) is given: frames differ by about a metre here and silently
differencing them would look like a metre of ice.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .nodes import Surface


def point_in_polygon(x, y, ring):
    """Even-odd test, vectorised over x, y. `ring` is (m, 2), not closed."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    inside = np.zeros(x.shape, bool)
    m = len(ring)
    for k in range(m):
        x1, y1 = ring[k]
        x2, y2 = ring[(k + 1) % m]
        cond = ((y1 > y) != (y2 > y))
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = (x2 - x1) * (y - y1) / (y2 - y1) + x1
        inside ^= cond & (x < xint)
    return inside


@dataclass
class Growth:
    a: str
    b: str
    dz: np.ndarray      # b - a
    sigma: np.ndarray
    mask: np.ndarray    # where both are defined (and inside the outline if given)
    offset: float
    datum_sigma: float = 0.0   # sqrt(datum_a^2 + datum_b^2)

    def stats(self):
        d = self.dz[self.mask]; s = self.sigma[self.mask]
        if len(d) == 0:
            return dict(n=0)
        w = 1 / s**2
        noise = float(1 / np.sqrt(np.sum(w)))
        return dict(n=int(len(d)), mean=float(d.mean()), wmean=float(np.sum(w * d) / np.sum(w)),
                    median=float(np.median(d)), sd=float(d.std()),
                    sigma_median=float(np.median(s)), sigma_of_mean_noise=noise,
                    sigma_of_mean=float(np.sqrt(noise**2 + self.datum_sigma**2)),
                    p05=float(np.percentile(d, 5)), p95=float(np.percentile(d, 95)))


def difference(a: Surface, b: Surface, offset=None, mask=None) -> Growth:
    if not a.grid.same(b.grid):
        raise ValueError("surfaces are on different grids")
    if a.frame != b.frame and offset is None:
        raise ValueError(f"frames differ ({a.frame!r} vs {b.frame!r}); give an explicit offset to difference them")
    off = float(offset or 0.0)
    dz = b.z - (a.z + off)
    sig = np.sqrt(a.sigma**2 + b.sigma**2)
    m = (a.n > 0) & (b.n > 0)
    if mask is not None:
        m &= mask
    return Growth(a.name, b.name, dz, sig, m, off, float(np.hypot(a.datum_sigma, b.datum_sigma)))


def volume(dz, mask, cell, positive_only=True):
    """Volume (m^3) of dz over the masked nodes; positive part only by default."""
    d = dz[mask]
    d = d[np.isfinite(d)]
    if positive_only:
        d = np.clip(d, 0, None)
    return float(d.sum() * cell**2)


def fill_to_level(surface: Surface, mask, level=None, margin=0.05, percentile=99.0):
    """Depth of water needed at each node to reach `level`. With level=None the level
    is the given percentile of the surface inside the mask plus `margin` (the rule the
    siting scripts use: 5 cm above the 99th percentile). Returns (fill, level, volume)."""
    z = surface.z
    ok = mask & np.isfinite(z)
    if not ok.any():
        raise ValueError("fill_to_level: no nodes inside the mask")
    if level is None:
        level = float(np.percentile(z[ok], percentile) + margin)
    fill = np.where(ok, np.clip(level - z, 0, None), np.nan)
    return fill, float(level), float(np.nansum(fill[ok]) * surface.grid.cell**2)


def relief(surface: Surface, mask):
    """max - min, sd and percentile range of a surface inside the mask."""
    z = surface.z[mask & np.isfinite(surface.z)]
    if len(z) == 0:
        return dict(n=0)
    return dict(n=int(len(z)), range=float(z.max() - z.min()), sd=float(z.std()),
                p2_5_97_5=float(np.percentile(z, 97.5) - np.percentile(z, 2.5)),
                mean=float(z.mean()), sigma_median=float(np.nanmedian(surface.sigma[mask])))


def point_relief(points_list, outline=None):
    """max - min and sd of the raw point heights (inside the outline if given), for
    comparison with point-level numbers from other scripts."""
    x = np.concatenate([p.x for p in points_list]); y = np.concatenate([p.y for p in points_list])
    z = np.concatenate([p.z for p in points_list])
    m = point_in_polygon(x, y, outline) if outline is not None else np.ones(len(z), bool)
    z = z[m]
    if len(z) == 0:
        return dict(n=0)
    return dict(n=int(len(z)), range=float(z.max() - z.min()), sd=float(z.std()),
                p2_5_97_5=float(np.percentile(z, 97.5) - np.percentile(z, 2.5)))
