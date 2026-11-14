"""A bare-earth DEM (airborne LiDAR) as low-weight soundings.

A `[[surveys]]` entry with `reader = "dem"` reads a DEM grid and turns every cell into a
sounding, so the estimator can fill the parts of a property nobody walked, with a sigma that
says so. Formats: `.npz` with 1-D `xs`, `ys` (local metres, cell centres) and 2-D `Z` (ny, nx),
or an ESRI ASCII grid (`.asc`, local metres). The grid must already be in the run's local frame
horizontally (move it there first; a translation is enough over a yard); heights are shifted
by `tie` (m, added: e.g. the geoid separation plus the datum difference that takes NAVD88 to
the local z), which is best measured against RTK in the open.

Per-sample sigma (m):
  sigma_open             where the ground is smooth (default 0.10)
  sigma_rough            where the local slope exceeds `rough_slope` (m/m) or the RMS
                         residual of a 3 x 3 plane fit exceeds `rough_rms` (m): canopy,
                         banks, walls (default 0.20)
  sigma_bad              where the DEM differs from RTK soundings within `check_radius` of the
                         sample by more than `check_dz` (median, default 0.30), set by
                         `inflate_where_rtk_disagrees()` after all surveys are read
The DEM's error is not white: neighbouring samples share their error (interpolation, canopy,
the flight strip). So the samples are grouped into square tiles of `tile_m` metres and each
tile is one pass for the crossover adjustment, with the survey's `wander_prior` (the
tile-scale error, default 0.05) and `common_prior` (the DEM's overall bias after the tie,
default 0.10): where RTK overlaps a tile, the tile's offset is solved; elsewhere it is held
at zero with its prior sigma, which does not average down inside a node.

`slope_field()` gives the DEM's local slope at any point, for a slope allowance that grows on
steep ground (grid `slope_from_dem`).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .io import Points, Site


def read_grid(path):
    """(xs, ys, Z) of a DEM file: .npz (xs, ys, Z) or ESRI ASCII (.asc)."""
    path = Path(path)
    if path.suffix.lower() == ".npz":
        d = np.load(path)
        return np.asarray(d["xs"], float), np.asarray(d["ys"], float), np.asarray(d["Z"], float)
    if path.suffix.lower() == ".asc":
        head, n = {}, 0
        lines = path.read_text().splitlines()
        while n < len(lines) and lines[n].split()[0].lower() in (
                "ncols", "nrows", "xllcorner", "yllcorner", "xllcenter", "yllcenter", "cellsize", "nodata_value"):
            k, v = lines[n].split()[:2]
            head[k.lower()] = float(v)
            n += 1
        Z = np.array([[float(v) for v in ln.split()] for ln in lines[n:] if ln.strip()])[::-1]
        c = head["cellsize"]
        x0 = head.get("xllcenter", head.get("xllcorner", 0.0) + c / 2)
        y0 = head.get("yllcenter", head.get("yllcorner", 0.0) + c / 2)
        Z[Z == head.get("nodata_value", -9999.0)] = np.nan
        return x0 + c * np.arange(Z.shape[1]), y0 + c * np.arange(Z.shape[0]), Z
    raise ValueError(f"{path}: DEM must be .npz or .asc")


def slope_and_roughness(xs, ys, Z):
    """Per cell: slope magnitude (m/m, central differences) and the RMS residual of a plane
    fitted to the 3 x 3 neighbourhood (m). NaN at the border and next to gaps."""
    dx, dy = float(np.mean(np.diff(xs))), float(np.mean(np.diff(ys)))
    gy, gx = np.gradient(Z, dy, dx)
    slope = np.hypot(gx, gy)
    ny, nx = Z.shape
    rough = np.full(Z.shape, np.nan)
    ox, oy = np.meshgrid([-1, 0, 1], [-1, 0, 1])
    A = np.c_[np.ones(9), ox.ravel() * dx, oy.ravel() * dy]
    P = A @ np.linalg.pinv(A)
    for j in range(1, ny - 1):
        for i in range(1, nx - 1):
            v = Z[j - 1:j + 2, i - 1:i + 2].ravel()
            if np.all(np.isfinite(v)):
                res = v - P @ v
                rough[j, i] = np.sqrt(np.sum(res**2) / 6.0)   # 9 values, 3 parameters
    return slope, rough


def dem_soundings(xs, ys, Z, *, tie=0.0, survey="dem", frame="", date="", sigma_open=0.10, sigma_rough=0.20,
                  rough_slope=0.15, rough_rms=0.05, sigma_h=0.5, tile_m=10.0, extent=None):
    """Points from a DEM grid (see the module docstring). `extent` (xmin, xmax, ymin, ymax)
    keeps only the cells inside. pass_id = tile index; remark = 'open' or 'rough'."""
    slope, rough = slope_and_roughness(xs, ys, Z)
    X, Y = np.meshgrid(xs, ys)
    ok = np.isfinite(Z)
    if extent is not None:
        ok &= (X >= extent[0]) & (X <= extent[1]) & (Y >= extent[2]) & (Y <= extent[3])
    is_rough = (np.nan_to_num(slope, nan=np.inf) > rough_slope) | (np.nan_to_num(rough, nan=np.inf) > rough_rms)
    x, y, z = X[ok], Y[ok], Z[ok] + tie
    sz = np.where(is_rough[ok], sigma_rough, sigma_open)
    tx = np.floor((x - xs.min()) / tile_m).astype(int)
    ty = np.floor((y - ys.min()) / tile_m).astype(int)
    tiles = {k: i for i, k in enumerate(sorted(set(zip(tx.tolist(), ty.tolist()))))}
    pid = np.array([tiles[k] for k in zip(tx.tolist(), ty.tolist())], int)
    n = len(z)
    return Points(t=np.full(n, np.nan), x=x, y=y, z=z, sz=sz, sh=np.full(n, float(sigma_h)),
                  quality=np.full(n, -1, int), survey=survey,
                  remark=np.where(is_rough[ok], "rough", "open").astype(object), seq=np.arange(n),
                  pass_id=pid, group=np.full(n, survey, dtype=object), frame=frame, date=date,
                  pass_names=[f"tile{a}_{b}" for a, b in sorted(tiles, key=tiles.get)])


def read_dem_survey(scfg, path, site: Site, base_dir=None):
    """A `[[surveys]]` entry with reader = "dem" (keys in the module docstring)."""
    xs, ys, Z = read_grid(path)
    ext = scfg.get("extent")
    pts = dem_soundings(xs, ys, Z, tie=float(scfg.get("tie", 0.0)), survey=scfg["id"], frame=scfg.get("frame", ""),
                        date=scfg.get("date", ""), sigma_open=float(scfg.get("sigma_open", 0.10)),
                        sigma_rough=float(scfg.get("sigma_rough", 0.20)),
                        rough_slope=float(scfg.get("rough_slope", 0.15)), rough_rms=float(scfg.get("rough_rms", 0.05)),
                        sigma_h=float(scfg.get("sigma_h", 0.5)), tile_m=float(scfg.get("tile_m", 10.0)),
                        extent=None if ext is None else [float(v) for v in ext])
    pts.prior = float(scfg.get("wander_prior", 0.05))
    pts.common_prior = float(scfg.get("common_prior", 0.10))
    return pts


def inflate_where_rtk_disagrees(dem: Points, others: list[Points], radius=1.0, dz=0.20, sigma_bad=0.30,
                                min_n=2):
    """Raise the sigma of DEM samples whose height differs from the median of at least min_n
    RTK soundings within `radius` m by more than `dz` (canopy, a bank the DEM smooths, a
    building edge). Returns (new Points, number inflated, per-sample median difference or NaN)."""
    if not others:
        return dem, 0, np.full(len(dem), np.nan)
    ox = np.concatenate([o.x for o in others]); oy = np.concatenate([o.y for o in others])
    oz = np.concatenate([o.z for o in others])
    cells = {}
    for k, (a, b) in enumerate(zip(np.floor(ox / radius).astype(int), np.floor(oy / radius).astype(int))):
        cells.setdefault((a, b), []).append(k)
    diff = np.full(len(dem), np.nan)
    for k in range(len(dem)):
        a, b = int(np.floor(dem.x[k] / radius)), int(np.floor(dem.y[k] / radius))
        idx = [q for da in (-1, 0, 1) for db in (-1, 0, 1) for q in cells.get((a + da, b + db), [])]
        if not idx:
            continue
        idx = np.array(idx)
        idx = idx[np.hypot(ox[idx] - dem.x[k], oy[idx] - dem.y[k]) <= radius]
        if len(idx) >= min_n:
            diff[k] = float(np.median(oz[idx]) - dem.z[k])
    bad = np.abs(np.nan_to_num(diff)) > dz
    sz = np.where(bad, np.maximum(dem.sz, sigma_bad), dem.sz)
    rem = np.where(bad, "disagrees", dem.remark).astype(object)
    return dem.replace(sz=sz, remark=rem), int(bad.sum()), diff


def slope_field(xs, ys, Z):
    """f(x, y) -> DEM slope (m/m), bilinear on the cell slopes; NaN outside."""
    slope, _ = slope_and_roughness(xs, ys, Z)
    return lambda x, y: bilinear(xs, ys, slope, x, y)


def bilinear(xs, ys, A, x, y):
    """Bilinear interpolation of grid A (ny, nx) on regular xs, ys at points x, y; NaN outside
    or next to NaN cells."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    dx, dy = xs[1] - xs[0], ys[1] - ys[0]
    fi = (x - xs[0]) / dx; fj = (y - ys[0]) / dy
    i0 = np.floor(fi).astype(int); j0 = np.floor(fj).astype(int)
    ok = (i0 >= 0) & (j0 >= 0) & (i0 < len(xs) - 1) & (j0 < len(ys) - 1)
    i0c = np.clip(i0, 0, len(xs) - 2); j0c = np.clip(j0, 0, len(ys) - 2)
    u = fi - i0c; v = fj - j0c
    val = (A[j0c, i0c] * (1 - u) * (1 - v) + A[j0c, i0c + 1] * u * (1 - v)
           + A[j0c + 1, i0c] * (1 - u) * v + A[j0c + 1, i0c + 1] * u * v)
    return np.where(ok, val, np.nan)
