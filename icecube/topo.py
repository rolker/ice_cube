"""Property topography: several grids, masks, a DEM, GIS exports and validation.

Used by `python -m icecube run` when the config has `[[grids]]` (and by `python -m icecube
validate`). The pipeline per surface is the one of cli.run (read, passes, crossover, node
estimator) with these additions:

- Surveys may carry their own wander prior and a common prior (`wander_prior`,
  `common_prior` in `[[surveys]]`, or from the soundings table), and anchor points (repeat
  marks) that enter the crossover only (crossover.py).
- `[[masks]]`: polygons (GeoJSON, lon/lat; `names` and/or `kinds` to pick features) where
  there is no ground to model (house, deck, sheds, a rock). Ground soundings inside are dropped
  before the crossover; nodes inside get no value and flag -1.
- A DEM survey (`reader = "dem"`, see dem.py) fills the rest at low weight; with
  `check_rtk = true` its samples that disagree with nearby RTK get `sigma_bad`. A survey's
  `capture_radius` is a floor on every grid's (a 2 m DEM must reach 1.5 m on a 0.5 m grid).
- `[[grids]]`: name, cell, capture_radius, k_sigma, the extent (`extent = [xmin, xmax, ymin,
  ymax]`, or `extent_polygon = {path, name}` plus `margin`), the slope allowance (`slope`, or
  `slope_from_dem = {survey, factor, floor, cap}`: per node max(floor, factor * DEM slope)),
  and `contour_interval`. Every surface is gridded on every grid.
- Outputs per surface and grid (`<surface>_<grid>`): surface npz, ESRI ASCII z / sigma /
  count / flags with a .prj (`[output] prj_from_gpkg` or `prj`), contours GeoJSON, figures.
  Per surface: passes, crossover (with the common biases), and points_<surface>.csv (every
  sounding with its pass, bias and corrected height).
- `[[fill]]` with `grid` and `outline = {path, name}`: water to `margin` over the
  `percentile` of the surface inside the outline, plus the p5-p95 relief.

validate(): leave-one-group-out (a group is a session: predict each session's ground
soundings from a run without it; z-scores against the claimed sigmas), anchor repeat
statistics, RTK minus DEM by DEM class, and coverage / uncertainty per grid.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import crossover, dem, export, growth, io, nodes, passes, report


# ----------------------------------------------------------------------------- context

@dataclass
class Context:
    cfg: dict
    base: Path | None
    site: io.Site
    surveys: dict = field(default_factory=dict)      # id -> (Points, infos, scfg)
    masks: list = field(default_factory=list)        # (name, ring)
    dem_ids: list = field(default_factory=list)
    defaults: dict = field(default_factory=dict)
    log: object = print


def _path(base, p):
    p = Path(p)
    return p if p.is_absolute() or base is None else base / p


def _surfaces(cfg):
    out = {}
    for s in cfg.get("surveys", []):
        out.setdefault(s.get("surface", s["id"]), []).append(s)
    return out


def prepare(cfg, log=print, exclude_groups=()):
    """Read every survey (passes, sigma defaults), apply masks, check the DEM against RTK.
    Points whose group is in exclude_groups are left out (leave-one-out validation)."""
    base = cfg.get("_base_dir")
    site = io.Site.from_config(cfg, base)
    d = cfg.get("defaults", {})
    ctx = Context(cfg=cfg, base=base, site=site, log=log, defaults=dict(
        sigma_z=float(d.get("sigma_z", 0.015)), sigma_h=float(d.get("sigma_h", 0.01)),
        prior=float(d.get("wander_prior", 0.03)), radius=float(d.get("crossover_radius", 0.5)),
        slope=float(d.get("slope", 0.05)), sep=float(d.get("separable_ratio", 0.8)),
        min_pairs=int(d.get("min_pairs", 3)), max_dz=float(d.get("max_dz", 0.25)),
        reject=float(d.get("reject_sigma", 4.0))))
    for mc in cfg.get("masks", []):
        for nm, kd, ring in io.read_polygons(_path(base, mc["path"]), site, names=mc.get("names"),
                                             kinds=mc.get("kinds")):
            ctx.masks.append((nm or kd, ring))
    for scfg in cfg.get("surveys", []):
        pts = io.read_survey(scfg, site, base).with_defaults(ctx.defaults["sigma_z"], ctx.defaults["sigma_h"])
        if exclude_groups:
            pts = pts.subset(~np.isin(pts.group.astype(str), list(exclude_groups)))
        if ctx.masks and scfg.get("masked", True):
            inside = mask_points(ctx.masks, pts.x, pts.y) & (pts.anchor == "")
            pts = pts.subset(~inside)
        if len(pts) == 0:
            log(f"{scfg['id']}: no points left" + (" (left out)" if exclude_groups else ""))
            continue
        pts, infos = passes.segment(pts, scfg.get("pass_rule"))
        if scfg.get("reader") == "dem":
            ctx.dem_ids.append(scfg["id"])
        ctx.surveys[scfg["id"]] = (pts, infos, scfg)
    for did in ctx.dem_ids:
        pts, infos, scfg = ctx.surveys[did]
        if scfg.get("check_rtk"):
            others = [v[0] for k, v in ctx.surveys.items() if k not in ctx.dem_ids
                      and v[2].get("surface", k) == scfg.get("surface", did)]
            others = [o.subset(o.anchor == "") for o in others]
            pts2, nbad, _ = dem.inflate_where_rtk_disagrees(
                pts, others, radius=float(scfg.get("check_radius", 1.0)), dz=float(scfg.get("check_dz", 0.20)),
                sigma_bad=float(scfg.get("sigma_bad", 0.30)))
            ctx.surveys[did] = (pts2, infos, scfg)
            log(f"{did}: {nbad} DEM samples disagree with RTK by > {float(scfg.get('check_dz', 0.20))*100:.0f} cm, "
                f"sigma raised to {float(scfg.get('sigma_bad', 0.30))*100:.0f} cm")
    for k, (p, infos, _) in ctx.surveys.items():
        log(f"{k}: {len(p)} points ({int((p.anchor != '').sum())} on anchors), {len(infos)} passes")
    return ctx


def mask_points(masks, x, y):
    m = np.zeros(np.shape(x), bool)
    for _, ring in masks:
        m |= growth.point_in_polygon(x, y, ring)
    return m


def solve(ctx, sname):
    """Crossover over the surveys of one surface. Returns (res, pts_list, corrected, sbs)."""
    scfgs = [s for s in _surfaces(ctx.cfg)[sname] if s["id"] in ctx.surveys]
    pts_list = [ctx.surveys[s["id"]][0] for s in scfgs]
    D = ctx.defaults
    res = crossover.adjust(pts_list, radius=D["radius"], prior=D["prior"], slope=D["slope"],
                           separable_ratio=D["sep"], min_pairs=D["min_pairs"], max_dz=D["max_dz"],
                           reject_sigma=D["reject"])
    corrected, sbs = [], []
    for p in pts_list:
        c, sb = crossover.apply(p, res)
        corrected.append(c); sbs.append(sb)
    return res, pts_list, corrected, sbs


def make_grid(ctx, gcfg, all_points):
    cell = float(gcfg.get("cell", 0.5))
    margin = float(gcfg.get("margin", 1.0))
    ring = None
    if "extent" in gcfg:
        xmin, xmax, ymin, ymax = (float(v) for v in gcfg["extent"])
    elif "extent_polygon" in gcfg:
        ep = gcfg["extent_polygon"]
        ring = io.read_polygon_outline(_path(ctx.base, ep["path"]), ctx.site, ep["name"])
        xmin, ymin = ring.min(0) - margin; xmax, ymax = ring.max(0) + margin
    else:
        x = np.concatenate([p.x for p in all_points]); y = np.concatenate([p.y for p in all_points])
        xmin, xmax, ymin, ymax = x.min() - margin, x.max() + margin, y.min() - margin, y.max() + margin
    xmin = np.floor(xmin / cell) * cell; ymin = np.floor(ymin / cell) * cell
    return nodes.Grid.from_extent(xmin, xmax, ymin, ymax, cell), ring


def slope_array(ctx, gcfg, grid):
    sd = gcfg.get("slope_from_dem")
    if not sd:
        return float(gcfg.get("slope", ctx.defaults["slope"]))
    scfg = next(s for s in ctx.cfg["surveys"] if s["id"] == sd["survey"])
    xs, ys, Z = dem.read_grid(_path(ctx.base, scfg["path"]))
    f = dem.slope_field(xs, ys, Z)
    X, Y = np.meshgrid(grid.xs, grid.ys)
    s = f(X, Y)
    floor = float(sd.get("floor", ctx.defaults["slope"]))
    out = np.maximum(floor, float(sd.get("factor", 0.5)) * np.nan_to_num(s, nan=0.0))
    return np.minimum(out, float(sd.get("cap", 0.5)))


def grid_surface(ctx, gcfg, grid, sname, corrected, sbs, slope):
    """Grid the non-anchor corrected points; masked nodes get no value."""
    pts = [c.subset(c.anchor == "") for c in corrected]
    sbl = [sb[c.anchor == ""] for c, sb in zip(corrected, sbs)]
    keep = [k for k, p in enumerate(pts) if len(p)]
    pts = [pts[k] for k in keep]; sbl = [sbl[k] for k in keep]
    default = float(gcfg.get("capture_radius", 1.5 * grid.cell))
    # a survey's own capture_radius (e.g. a 2 m DEM) is a floor for every grid
    capture = [max(default, float(ctx.surveys[p.survey][2].get("capture_radius", 0.0))) for p in pts]
    surf = nodes.grid_surface(pts, sbl, grid, capture_radius=capture, slope=slope,
                              k_sigma=float(gcfg.get("k_sigma", 3.0)), name=sname,
                              date=max(p.date for p in pts), frame=pts[0].frame)
    X, Y = np.meshgrid(grid.xs, grid.ys)
    nmask = mask_points(ctx.masks, X, Y) if ctx.masks else np.zeros(X.shape, bool)
    surf.z = np.where(nmask, np.nan, surf.z)
    surf.sigma = np.where(nmask, np.nan, surf.sigma)
    surf.n = np.where(nmask, 0, surf.n)
    return surf, nmask


# ----------------------------------------------------------------------------- run

def _wkt(ctx):
    oc = ctx.cfg.get("output", {})
    if oc.get("prj_from_gpkg"):
        return io.gpkg_local_wkt(_path(ctx.base, oc["prj_from_gpkg"]))
    if oc.get("prj"):
        return _path(ctx.base, oc["prj"]).read_text()
    return None


def _pass_label(p: io.Points, pid):
    return p.pass_names[pid] if p.pass_names and 0 <= pid < len(p.pass_names) else str(pid)


def table_crossover_named(res, pts_list, limit=None):
    names = {p.survey: p for p in pts_list}
    rows = [f"Observations {res.n_obs} ({int(res.pair_anchor.sum())} on anchors; dropped: {res.n_cut} beyond "
            f"max_dz, {res.n_rejected} by the residual test), components {res.components}, chi2/dof "
            f"{res.chi2_dof:.2f}, rms of pair differences before {res.rms_before*100:.1f} cm, after "
            f"{res.rms_after*100:.1f} cm.", ""]
    if res.common:
        rows += ["| survey | common bias (cm) | sigma (cm) | prior (cm) | pairs | status |", "|---|---|---|---|---|---|"]
        for s, c in res.common.items():
            rows.append(f"| {s} | {c['bias']*100:+.1f} | {c['sigma']*100:.1f} | {c['prior']*100:.1f} | {c['pairs']} | "
                        f"{'found' if c['separable'] else 'not separable'} |")
        rows.append("")
    rows += ["| survey | pass | pairs | prior (cm) | bias (cm) | sigma (cm) | raw (cm) | status |",
             "|---|---|---|---|---|---|---|---|"]
    order = range(len(res.pass_keys))
    for k in order:
        s, p = res.pass_keys[k]
        st = "found" if res.separable[k] else "held at zero"
        if not res.separable[k] and res.bias[k] != 0:
            st = "common bias"
        elif res.separable[k] and abs(res.bias[k]) < 2 * res.sigma[k]:
            st = "found, consistent with zero"
        rows.append(f"| {s} | {_pass_label(names[s], p)} | {res.n_pairs_per_pass[k]} | {res.pass_prior[k]*100:.1f} | "
                    f"{res.bias[k]*100:+.1f} | {res.sigma[k]*100:.1f} | {res.bias_raw[k]*100:+.1f} | {st} |")
    return "\n".join(rows)


def bias_summary(res, pts_list):
    """Per survey: passes, separable, pairs, and the pair-weighted mean applied bias."""
    out = {}
    for s in {k[0] for k in res.pass_keys}:
        idx = [k for k, key in enumerate(res.pass_keys) if key[0] == s]
        b = res.bias[idx]; sg = res.sigma[idx]; sep = res.separable[idx]; npp = res.n_pairs_per_pass[idx]
        w = np.where(sep, 1 / sg**2, 0)
        out[s] = dict(passes=len(idx), separable=int(sep.sum()), pairs=int(npp.sum()),
                      wmean_cm=float(np.sum(w * b) / np.sum(w) * 100) if w.sum() else float("nan"),
                      wmean_sigma_cm=float(np.sqrt(1 / np.sum(w)) * 100) if w.sum() else float("nan"),
                      min_cm=float(b[sep].min() * 100) if sep.any() else float("nan"),
                      max_cm=float(b[sep].max() * 100) if sep.any() else float("nan"),
                      prior_cm=float(np.median(res.pass_prior[idx]) * 100))
    return out


def write_points_csv(path, res, pts_list, corrected, sbs):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["survey", "sid", "group", "pass", "anchor", "x_m", "y_m", "z_m", "bias_m", "bias_sigma_m",
                    "z_corrected_m", "sigma_z_m", "sigma_xy_m"])
        for p, c, sb in zip(pts_list, corrected, sbs):
            for k in range(len(p)):
                w.writerow([p.survey, p.remark[k], p.group[k], _pass_label(p, int(p.pass_id[k])), p.anchor[k],
                            f"{p.x[k]:.3f}", f"{p.y[k]:.3f}", f"{p.z[k]:.4f}", f"{p.z[k] - c.z[k]:.4f}", f"{sb[k]:.4f}",
                            f"{c.z[k]:.4f}", f"{p.sz[k]:.4f}", f"{p.sh[k]:.4f}"])


def fig_map(surf, nmask, path, what, title, masks=(), outline=None, levels=None, lot=None, vmax=None):
    plt = report._plt()
    g = surf.grid
    if what == "z":
        data, label, cmap = surf.z, "height z (m, local)", "terrain"
    elif what == "sigma":
        data, label, cmap = surf.sigma * 100, "node sigma (cm)", "viridis"
    elif what == "n":
        data, label, cmap = np.where(surf.n > 0, surf.n, np.nan).astype(float), "soundings per node", "Blues"
    else:
        data, label, cmap = np.where(surf.n > 0, surf.flags, np.nan).astype(float), "inconsistency count", "Oranges"
    fig, ax = plt.subplots(figsize=(7.2, 6.2))
    im = ax.imshow(data, origin="lower", extent=report._extent(g), cmap=cmap, interpolation="nearest",
                   vmax=vmax)
    fig.colorbar(im, ax=ax, label=label, shrink=0.8)
    if levels:
        for lev, lines in levels:
            for ln in lines:
                a = np.array(ln)
                ax.plot(a[:, 0], a[:, 1], color="black", lw=0.4, alpha=0.6)
    if what == "sigma" and np.isfinite(surf.sigma).any():
        ax.contour(g.xs, g.ys, np.nan_to_num(surf.sigma, nan=0.0), levels=[0.05], colors="red", linewidths=0.8)
    for nm, ring in masks:
        r = np.vstack([ring, ring[:1]])
        ax.fill(r[:, 0], r[:, 1], color="0.6", alpha=0.6, lw=0)
    for ring, style in ((outline, "--"), (lot, "-")):
        if ring is not None:
            r = np.vstack([ring, ring[:1]])
            ax.plot(r[:, 0], r[:, 1], color="black", lw=1.0, ls=style)
    ax.set_xlim(g.xs[0] - g.cell, g.xs[-1] + g.cell); ax.set_ylim(g.ys[0] - g.cell, g.ys[-1] + g.cell)
    ax.set_aspect("equal"); ax.set_xlabel("x east (m, local)"); ax.set_ylabel("y north (m, local)")
    ax.set_title(title, fontsize=10)
    fig.tight_layout(); fig.savefig(path); plt.close(fig)


def fill_stats(surf, ring, percentile=95.0, margin=0.10):
    X, Y = np.meshgrid(surf.grid.xs, surf.grid.ys)
    inside = growth.point_in_polygon(X, Y, ring)
    ok = inside & np.isfinite(surf.z)
    if not ok.any():
        return dict(n=0)
    z = surf.z[ok]
    level = float(np.percentile(z, percentile) + margin)
    depth = np.clip(level - z, 0, None)
    return dict(n=int(ok.sum()), n_inside=int(inside.sum()), coverage=float(ok.sum() / inside.sum()),
                p5_p95_cm=float((np.percentile(z, 95) - np.percentile(z, 5)) * 100),
                range_cm=float((z.max() - z.min()) * 100), sd_cm=float(z.std() * 100),
                mean_z=float(z.mean()), level_minus_mean_cm=float((level - z.mean()) * 100),
                volume_m3=float(depth.sum() * surf.grid.cell**2),
                area_m2=float(inside.sum() * surf.grid.cell**2),
                mean_depth_cm=float(depth.mean() * 100), max_depth_cm=float(depth.max() * 100),
                sigma_median_cm=float(np.nanmedian(surf.sigma[ok]) * 100),
                sigma_p95_cm=float(np.nanpercentile(surf.sigma[ok], 95) * 100),
                datum_sigma_cm=float(surf.datum_sigma * 100))


def run(cfg, out_dir=None, log=print):
    ctx = prepare(cfg, log)
    base = ctx.base
    out = Path(out_dir or cfg.get("output", {}).get("dir", "output/ice-cube"))
    if not out.is_absolute() and base is not None:
        out = base / out
    out.mkdir(parents=True, exist_ok=True)
    wkt = _wkt(ctx)
    D = ctx.defaults
    summary = ["# Ice-Cube topography run", "",
               f"Wander prior default {D['prior']*100:.1f} cm, crossover radius {D['radius']} m, slope "
               f"(crossover) {D['slope']} m/m, max_dz {D['max_dz']*100:.0f} cm, reject {D['reject']} sigma. "
               f"Masks: {', '.join(sorted({m[0] for m in ctx.masks})) or 'none'}.", ""]
    lot = None
    results = {"surfaces": {}, "grids": {}}
    for sname in _surfaces(cfg):
        res, pts_list, corrected, sbs = solve(ctx, sname)
        infos = sum((ctx.surveys[p.survey][1] for p in pts_list), [])
        write_points_csv(out / f"points_{sname}.csv", res, pts_list, corrected, sbs)
        (out / f"crossover_{sname}.md").write_text(table_crossover_named(res, pts_list) + "\n")
        (out / f"passes_{sname}.md").write_text(report.table_passes(infos) + "\n")
        bs = bias_summary(res, pts_list)
        report.fig_hist(res.residuals * 100, out / f"xover_resid_{sname}.png", "pair residual after adjustment (cm)",
                        f"{sname}: crossover residuals")
        summary += [f"## Surface {sname}", "",
                    "| survey | points | anchors | passes | separable | pairs | prior (cm) | bias, weighted mean of "
                    "separable passes (cm) | range (cm) | common bias (cm) |", "|---|---|---|---|---|---|---|---|---|---|"]
        for p in pts_list:
            b = bs[p.survey]; c = res.common.get(p.survey)
            summary.append(f"| {p.survey} | {len(p)} | {int((p.anchor != '').sum())} | {b['passes']} | {b['separable']} | "
                           f"{b['pairs']} | {b['prior_cm']:.1f} | {b['wmean_cm']:+.1f} +- {b['wmean_sigma_cm']:.1f} | "
                           f"{b['min_cm']:+.1f}..{b['max_cm']:+.1f} | "
                           + (f"{c['bias']*100:+.1f} +- {c['sigma']*100:.1f}" + ("" if c['separable'] else " (n.s.)") if c else "-")
                           + " |")
        an = res.pair_anchor
        summary += ["", f"Crossover: {res.n_obs} pairs ({int(an.sum())} anchor pairs), chi2/dof {res.chi2_dof:.2f}, "
                    f"rms before {res.rms_before*100:.1f} cm after {res.rms_after*100:.1f} cm; anchor pairs rms after "
                    f"{(np.sqrt(np.mean(res.residuals[an]**2))*100 if an.any() else float('nan')):.1f} cm. "
                    f"Per pass: crossover_{sname}.md.", ""]
        results["surfaces"][sname] = dict(n_obs=res.n_obs, chi2_dof=res.chi2_dof, bias_by_survey=bs,
                                          common={k: v for k, v in res.common.items()})
        for gcfg in cfg.get("grids", []):
            gname = gcfg["name"]
            grid, ring = make_grid(ctx, gcfg, corrected)
            if gcfg.get("lot"):
                lot = ring
            slope = slope_array(ctx, gcfg, grid)
            surf, nmask = grid_surface(ctx, gcfg, grid, sname, corrected, sbs, slope)
            tag = f"{sname}_{gname}"
            flags = np.where(nmask, -1, surf.flags)
            np.savez(out / f"surface_{tag}.npz", z=surf.z, sigma=surf.sigma, n=surf.n, flags=surf.flags, mask=nmask,
                     maxnr=surf.maxnr, npass=surf.npass, slope=np.broadcast_to(slope, surf.z.shape), **grid.as_dict())
            for what, arr, fmt in (("z", surf.z, "%.4f"), ("sigma", surf.sigma, "%.4f"),
                                   ("count", np.where(surf.n > 0, surf.n, np.nan), "%d"),
                                   ("flags", np.where(nmask | (surf.n > 0), flags, np.nan), "%d")):
                export.write_asc(out / f"{what}_{tag}.asc", grid, arr, fmt=fmt)
                if wkt:
                    export.write_prj(out / f"{what}_{tag}.prj", wkt)
            ci = float(gcfg.get("contour_interval", 0.25))
            levels = export.contours(grid, surf.z, ci)
            nlines = export.contours_geojson(out / f"contours_{tag}.geojson", levels,
                                             dict(grid=gname, surface=sname, interval=ci))
            outline = None
            if gcfg.get("outline"):
                oc = gcfg["outline"]
                outline = io.read_polygon_outline(_path(base, oc["path"]), ctx.site, oc["name"])
            for what in ("z", "sigma", "n", "flags"):
                fig_map(surf, nmask, out / f"{what}_{tag}.png", what,
                        f"{sname}, grid {gname} ({grid.cell} m): {what}" + (f", contours {ci*100:g} cm" if what == "z" else "")
                        + (", red = 5 cm" if what == "sigma" else ""),
                        masks=ctx.masks, outline=outline if outline is not None else ring if not gcfg.get("lot") else None,
                        levels=levels if what == "z" else None, lot=lot,
                        vmax=15 if what == "sigma" else None)
            ok = np.isfinite(surf.z)
            sg = surf.sigma[ok] * 100
            q = np.percentile(sg, [5, 50, 95]) if len(sg) else [np.nan] * 3
            gres = dict(nodes=int(ok.sum()), masked=int(nmask.sum()), sigma_p5=q[0], sigma_median=q[1], sigma_p95=q[2],
                        frac_sigma_gt_5cm=float(np.mean(sg > 5)) if len(sg) else float("nan"),
                        flagged=int((surf.flags[ok] > 0).sum()), contour_lines=nlines, datum_sigma_cm=surf.datum_sigma * 100)
            results["grids"][tag] = gres
            summary += [f"### Grid {gname}: {grid.nx} x {grid.ny} nodes at {grid.cell} m, capture "
                        f"{float(gcfg.get('capture_radius', 1.5 * grid.cell))} m", "",
                        f"Nodes with a value {gres['nodes']} (masked {gres['masked']}); node sigma p5/median/p95 "
                        f"{q[0]:.1f}/{q[1]:.1f}/{q[2]:.1f} cm; sigma > 5 cm at {gres['frac_sigma_gt_5cm']*100:.0f} % of "
                        f"nodes; flagged nodes {gres['flagged']}; datum sigma {surf.datum_sigma*100:.1f} cm; "
                        f"{nlines} contour lines at {ci*100:g} cm.", ""]
            for fc in cfg.get("fill", []):
                if fc.get("surface", sname) != sname or fc.get("grid") != gname:
                    continue
                oc = fc["outline"]
                fring = io.read_polygon_outline(_path(base, oc["path"]), ctx.site, oc["name"])
                fs = fill_stats(surf, fring, float(fc.get("percentile", 95.0)), float(fc.get("margin", 0.10)))
                results["grids"][tag]["fill"] = fs
                if fs.get("n"):
                    summary += [f"Fill inside {oc['name']} ({fs['area_m2']:.0f} m^2, {fs['coverage']*100:.0f} % of nodes "
                                f"with a value): p5-p95 relief {fs['p5_p95_cm']:.1f} cm (max-min {fs['range_cm']:.1f}, sd "
                                f"{fs['sd_cm']:.1f}); water to {float(fc.get('margin', 0.10))*100:.0f} cm over the "
                                f"p{float(fc.get('percentile', 95.0)):g}: {fs['volume_m3']:.1f} m^3 (mean depth "
                                f"{fs['mean_depth_cm']:.1f} cm, max {fs['max_depth_cm']:.1f} cm); node sigma median "
                                f"{fs['sigma_median_cm']:.1f} cm, p95 {fs['sigma_p95_cm']:.1f} cm.", ""]
            log(f"{tag}: nodes {gres['nodes']}, sigma median {q[1]:.1f} cm, > 5 cm {gres['frac_sigma_gt_5cm']*100:.0f} %")
    (out / "summary.md").write_text("\n".join(summary) + "\n")
    (out / "run.json").write_text(json.dumps(results, indent=1, default=float))
    return results


# ----------------------------------------------------------------------------- validation

def _zstats(r, s):
    z = r / s
    if len(r) == 0:
        return dict(n=0)
    med = float(np.median(r))
    return dict(n=int(len(r)), median_cm=med * 100, rms_cm=float(np.sqrt(np.mean(r**2)) * 100),
                rsd_cm=float(1.4826 * np.median(np.abs(r - med)) * 100),
                claimed_sigma_median_cm=float(np.median(s) * 100),
                z_rms=float(np.sqrt(np.mean(z**2))), z_median_abs=float(np.median(np.abs(z))),
                within_1=float(np.mean(np.abs(z) <= 1)), within_2=float(np.mean(np.abs(z) <= 2)),
                within_3=float(np.mean(np.abs(z) <= 3)))


def validate(cfg, out_dir=None, grid_name=None, log=print, groups=None):
    """Leave-one-group-out, anchors, RTK vs DEM, coverage. Writes validation.md/json."""
    base = cfg.get("_base_dir")
    out = Path(out_dir or cfg.get("output", {}).get("dir", "output/ice-cube"))
    if not out.is_absolute() and base is not None:
        out = base / out
    out.mkdir(parents=True, exist_ok=True)
    full = prepare(cfg, log=lambda *a: None)
    gcfgs = cfg.get("grids", [])
    gcfg = next((g for g in gcfgs if g["name"] == grid_name), gcfgs[0])
    lines = ["# Ice-Cube validation", "", f"Grid for the leave-one-out runs: {gcfg['name']}.", ""]
    result = {}
    for sname in _surfaces(cfg):
        res, pts_list, corrected, sbs = solve(full, sname)
        rtk = [p for p in pts_list if p.survey not in full.dem_ids]
        # 1. leave one group out
        allg = sorted({g for p in rtk for g in set(p.group.astype(str))})
        if groups:
            allg = [g for g in allg if g in groups]
        rows, loo = [], {}
        zs_all, r_all = [], []
        for g in allg:
            ctx = prepare(cfg, log=lambda *a: None, exclude_groups=(g,))
            if sname not in _surfaces(cfg) or not any(s["id"] in ctx.surveys for s in _surfaces(cfg)[sname]):
                continue
            res_g, pl_g, corr_g, sbs_g = solve(ctx, sname)
            grid, _ = make_grid(ctx, gcfg, corr_g)
            surf, nmask = grid_surface(ctx, gcfg, grid, sname, corr_g, sbs_g, slope_array(ctx, gcfg, grid))
            held = [p.subset((p.group.astype(str) == g) & (p.anchor == "")) for p in rtk]
            held = [h for h in held if len(h)]
            if not held:
                continue
            hx = np.concatenate([h.x for h in held]); hy = np.concatenate([h.y for h in held])
            hz = np.concatenate([h.z for h in held]); hs = np.concatenate([h.sz for h in held])
            pp = [h.prior if h.prior is not None else full.defaults["prior"] for h in held]
            hp = np.concatenate([np.full(len(h), np.hypot(p_, h.common_prior)) for h, p_ in zip(held, pp)])
            hq = np.concatenate([np.full(len(h), p_) for h, p_ in zip(held, pp)])   # pass part only
            zp = export.sample(grid, surf.z, hx, hy); sp = export.sample(grid, surf.sigma, hx, hy)
            ok = np.isfinite(zp) & np.isfinite(sp)
            r = hz[ok] - zp[ok]
            s = np.sqrt(sp[ok]**2 + hs[ok]**2 + hp[ok]**2)
            st = _zstats(r, s)
            # the held-out session's own offset removed (what is left is shape, not datum)
            st2 = _zstats(r - np.median(r), np.sqrt(sp[ok]**2 + hs[ok]**2 + hq[ok]**2)) if len(r) else dict(n=0)
            st["n_held"] = int(len(hz)); st["after_own_offset"] = st2
            loo[g] = st
            zs_all.append(r / s); r_all.append(r)
            log(f"leave out {g}: predicted {st.get('n', 0)} of {len(hz)}, median {st.get('median_cm', np.nan):+.1f} cm, "
                f"rms {st.get('rms_cm', np.nan):.1f} cm, z rms {st.get('z_rms', np.nan):.2f}")
        lines += [f"## Surface {sname}", "", "### Leave one session out", "",
                  "Each session's ground soundings (anchors excluded) predicted by a run without that session; "
                  "residual = sounding minus prediction; claimed sigma = sqrt(node sigma^2 + sounding sigma^2 + the "
                  "session's pass prior^2, with its common prior if any) (its own pass bias is unknown to the run). "
                  "z = residual / claimed sigma: honest sigmas give z rms near 1 and ~68 / 95 % within 1 / 2. "
                  "'After own offset': the session's median residual removed and its common prior dropped "
                  "(what is left is shape and pass wander).", "",
                  "| session | predicted / held | median (cm) | rms (cm) | robust sd (cm) | claimed sigma median (cm) | "
                  "z rms | within 1 / 2 / 3 | after own offset: rsd (cm), z rms |", "|---|---|---|---|---|---|---|---|---|"]
        for g, st in loo.items():
            if not st.get("n"):
                lines.append(f"| {g} | 0 / {st['n_held']} | | | | | | | |"); continue
            a = st["after_own_offset"]
            lines.append(f"| {g} | {st['n']} / {st['n_held']} | {st['median_cm']:+.1f} | {st['rms_cm']:.1f} | "
                         f"{st['rsd_cm']:.1f} | {st['claimed_sigma_median_cm']:.1f} | {st['z_rms']:.2f} | "
                         f"{st['within_1']*100:.0f} / {st['within_2']*100:.0f} / {st['within_3']*100:.0f} % | "
                         f"{a['rsd_cm']:.1f}, {a['z_rms']:.2f} |")
        if zs_all:
            z = np.concatenate(zs_all); r = np.concatenate(r_all)
            lines += ["", f"All sessions: n {len(z)}, z rms {np.sqrt(np.mean(z**2)):.2f}, median |z| "
                      f"{np.median(np.abs(z)):.2f} (0.67 for a unit normal), within 1 / 2 / 3: "
                      f"{np.mean(np.abs(z) <= 1)*100:.0f} / {np.mean(np.abs(z) <= 2)*100:.0f} / "
                      f"{np.mean(np.abs(z) <= 3)*100:.0f} %; residual rms {np.sqrt(np.mean(r**2))*100:.1f} cm.", ""]
            report.fig_hist(np.clip(z, -6, 6), out / f"loo_z_{sname}.png", "z = residual / claimed sigma (clipped at 6)",
                            f"{sname}: leave-one-session-out z-scores")
        # 2. anchors
        an = res.pair_anchor
        lines += ["### Anchors (repeat marks)", ""]
        apts = [(p, c) for p, c in zip(pts_list, corrected) if (p.anchor != "").any()]
        sites = sorted({a for p, _ in apts for a in set(p.anchor) if a})
        lines += ["| anchor | visits (points) | passes | sd raw (cm) | sd corrected (cm) | max-min corrected (cm) |",
                  "|---|---|---|---|---|---|"]
        anc = {}
        for a in sites:
            zr = np.concatenate([p.z[p.anchor == a] for p, _ in apts]); zc = np.concatenate([c.z[p.anchor == a] for p, c in apts])
            npas = len({(p.survey, int(q)) for p, _ in apts for q in p.pass_id[p.anchor == a]})
            # one value per pass (points of one pass on one mark are one visit)
            pr, pc = [], []
            for p, c in apts:
                for q in np.unique(p.pass_id[p.anchor == a]):
                    m = (p.anchor == a) & (p.pass_id == q)
                    w = 1 / p.sz[m]**2
                    pr.append(np.sum(w * p.z[m]) / w.sum()); pc.append(np.sum(w * c.z[m]) / w.sum())
            pr, pc = np.array(pr), np.array(pc)
            anc[a] = dict(points=int(len(zr)), passes=npas, sd_raw_cm=float(pr.std(ddof=1) * 100) if len(pr) > 1 else float("nan"),
                          sd_corr_cm=float(pc.std(ddof=1) * 100) if len(pc) > 1 else float("nan"),
                          range_corr_cm=float((pc.max() - pc.min()) * 100))
            lines.append(f"| {a} | {len(zr)} | {npas} | {anc[a]['sd_raw_cm']:.1f} | {anc[a]['sd_corr_cm']:.1f} | "
                         f"{anc[a]['range_corr_cm']:.1f} |")
        if an.any():
            za = res.residuals[an] / res.pair_sigma[an]
            lines += ["", f"Anchor pairs in the adjustment: {int(an.sum())}, residual rms "
                      f"{np.sqrt(np.mean(res.residuals[an]**2))*100:.1f} cm, normalised rms {np.sqrt(np.mean(za**2)):.2f}.", ""]
        # 3. RTK minus DEM
        dsec = {}
        for did in full.dem_ids:
            scfg = full.surveys[did][2]
            xs, ys, Z = dem.read_grid(_path(base, scfg["path"]))
            Z = Z + float(scfg.get("tie", 0.0))
            slope, rough = dem.slope_and_roughness(xs, ys, Z)
            isr = (np.nan_to_num(slope, nan=9) > float(scfg.get("rough_slope", 0.15))) | (
                np.nan_to_num(rough, nan=9) > float(scfg.get("rough_rms", 0.05)))
            for p, c in zip(pts_list, corrected):
                if p.survey in full.dem_ids:
                    continue
                m = p.anchor == ""
                zd = dem.bilinear(xs, ys, Z, c.x[m], c.y[m])
                cls = dem.bilinear(xs, ys, isr.astype(float), c.x[m], c.y[m])
                d = c.z[m] - zd
                for nm, sel in (("open", cls < 0.5), ("rough", cls >= 0.5)):
                    v = d[sel & np.isfinite(d)]
                    if len(v):
                        med = float(np.median(v))
                        dsec[(did, p.survey, nm)] = dict(n=int(len(v)), median_cm=med * 100,
                                                         rsd_cm=float(1.4826 * np.median(np.abs(v - med)) * 100),
                                                         p95_abs_cm=float(np.percentile(np.abs(v - med), 95) * 100))
        if dsec:
            lines += ["### RTK minus DEM (bias-corrected RTK, DEM with its tie, before the DEM's own crossover bias)", "",
                      "| DEM | survey | DEM class | n | median (cm) | robust sd (cm) | p95 abs dev (cm) |",
                      "|---|---|---|---|---|---|---|"]
            for (did, sv, nm), v in sorted(dsec.items()):
                lines.append(f"| {did} | {sv} | {nm} | {v['n']} | {v['median_cm']:+.1f} | {v['rsd_cm']:.1f} | {v['p95_abs_cm']:.1f} |")
            lines.append("")
        # 4. coverage / uncertainty per grid
        cov = {}
        lines += ["### Coverage and uncertainty", ""]
        rtkc = [c.subset(c.anchor == "") for p, c in zip(pts_list, corrected) if p.survey not in full.dem_ids]
        for g2 in gcfgs:
            grid, ring = make_grid(full, g2, corrected)
            surf, nmask = grid_surface(full, g2, grid, sname, corrected, sbs, slope_array(full, g2, grid))
            X, Y = np.meshgrid(grid.xs, grid.ys)
            region = growth.point_in_polygon(X, Y, ring) if ring is not None else np.ones(X.shape, bool)
            if g2.get("outline"):
                oc = g2["outline"]
                region = growth.point_in_polygon(X, Y, io.read_polygon_outline(_path(base, oc["path"]), full.site, oc["name"]))
            cap = float(g2.get("capture_radius", 1.5 * grid.cell))
            near = _near_points(grid, rtkc, cap)
            ok = region & np.isfinite(surf.z)
            s = surf.sigma
            cv = dict(region_nodes=int(region.sum()), masked=int((region & nmask).sum()), with_value=int(ok.sum()),
                      rtk_nodes=int((ok & near).sum()), dem_only=int((ok & ~near).sum()),
                      sigma_gt5=int((ok & (s > 0.05)).sum()),
                      sigma_gt5_rtk=int((ok & near & (s > 0.05)).sum()),
                      sigma_median_rtk_cm=float(np.nanmedian(s[ok & near]) * 100) if (ok & near).any() else float("nan"),
                      sigma_median_dem_cm=float(np.nanmedian(s[ok & ~near]) * 100) if (ok & ~near).any() else float("nan"),
                      area_sigma_gt5_m2=float((ok & (s > 0.05)).sum() * grid.cell**2))
            cov[g2["name"]] = cv
            lines.append(f"- grid {g2['name']}: {cv['region_nodes']} nodes in the region ({cv['masked']} masked), "
                         f"{cv['rtk_nodes']} within {cap} m of an RTK sounding (node sigma median "
                         f"{cv['sigma_median_rtk_cm']:.1f} cm), {cv['dem_only']} from the DEM only (median "
                         f"{cv['sigma_median_dem_cm']:.1f} cm). Sigma > 5 cm: {cv['sigma_gt5']} nodes "
                         f"({cv['area_sigma_gt5_m2']:.0f} m^2), of which {cv['sigma_gt5_rtk']} have RTK.")
            _fig_coverage(grid, surf, nmask, near, region, full.masks, out / f"coverage_{sname}_{g2['name']}.png",
                          f"{sname}, grid {g2['name']}: node sigma; dots = RTK coverage, red = 5 cm")
        lines.append("")
        result[sname] = dict(loo=loo, anchors=anc, rtk_minus_dem={"/".join(k): v for k, v in dsec.items()}, coverage=cov)
    (out / "validation.md").write_text("\n".join(lines) + "\n")
    (out / "validation.json").write_text(json.dumps(result, indent=1, default=float))
    return result


def _near_points(grid, pts_list, radius):
    near = np.zeros((grid.ny, grid.nx), bool)
    r = int(np.ceil(radius / grid.cell))
    for p in pts_list:
        i0 = np.round((p.x - grid.x0) / grid.cell).astype(int); j0 = np.round((p.y - grid.y0) / grid.cell).astype(int)
        for di in range(-r, r + 1):
            for dj in range(-r, r + 1):
                ii, jj = i0 + di, j0 + dj
                ok = (ii >= 0) & (ii < grid.nx) & (jj >= 0) & (jj < grid.ny)
                d = np.hypot(grid.x0 + ii * grid.cell - p.x, grid.y0 + jj * grid.cell - p.y)
                ok &= d <= radius
                near[jj[ok], ii[ok]] = True
    return near


def _fig_coverage(grid, surf, nmask, near, region, masks, path, title):
    plt = report._plt()
    fig, ax = plt.subplots(figsize=(7.2, 6.2))
    data = np.where(region, surf.sigma * 100, np.nan)
    im = ax.imshow(data, origin="lower", extent=report._extent(grid), cmap="viridis", vmax=15, interpolation="nearest")
    fig.colorbar(im, ax=ax, label="node sigma (cm)", shrink=0.8)
    ax.contour(grid.xs, grid.ys, np.nan_to_num(np.where(region, surf.sigma, 0.0)), levels=[0.05], colors="red", linewidths=0.8)
    jj, ii = np.where(near & region)
    ax.plot(grid.xs[ii], grid.ys[jj], ",", color="white", alpha=0.6)
    for nm, ring in masks:
        r = np.vstack([ring, ring[:1]])
        ax.fill(r[:, 0], r[:, 1], color="0.6", alpha=0.7, lw=0)
    ax.set_aspect("equal"); ax.set_xlabel("x east (m, local)"); ax.set_ylabel("y north (m, local)")
    ax.set_title(title, fontsize=10)
    fig.tight_layout(); fig.savefig(path); plt.close(fig)
