"""`python -m icecube run config.toml [--out DIR]`

The config (see icecube.example.toml at the repo root) lists the site origin, sigma
defaults, the grid, an optional outline polygon, the surveys (each with a date, a
frame, a reader, a pass rule and filters) grouped into surfaces, and the growth pairs.
Command-line arguments take priority over the config.

For each surface: read its surveys, segment passes, solve the crossover adjustment,
grid the corrected points, and write figures + a summary. Then every growth pair, and
fill-to-level if asked. Outputs go to the output directory: summary.md, one npz per
surface (local metres), pass and crossover tables, PNG figures in local axes.
"""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

import numpy as np

from . import crossover, growth, io, nodes, passes, report


def load_config(path):
    cfg = tomllib.loads(Path(path).read_text())
    cfg["_base_dir"] = Path(path).resolve().parent
    return cfg


def _surfaces(cfg):
    """Group `[[surveys]]` by their `surface` (default: own id). Returns an ordered dict
    surface -> list of survey configs."""
    out = {}
    for s in cfg.get("surveys", []):
        out.setdefault(s.get("surface", s["id"]), []).append(s)
    return out


def _grid_from(cfg, outline, all_points):
    g = cfg.get("grid", {})
    cell = float(g.get("cell", 0.5))
    margin = float(g.get("margin", 1.0))
    if "extent" in g:
        xmin, xmax, ymin, ymax = (float(v) for v in g["extent"])
    elif outline is not None:
        xmin, ymin = outline.min(0) - margin; xmax, ymax = outline.max(0) + margin
    else:
        x = np.concatenate([p.x for p in all_points]); y = np.concatenate([p.y for p in all_points])
        xmin, xmax, ymin, ymax = x.min() - margin, x.max() + margin, y.min() - margin, y.max() + margin
    # snap the origin to a multiple of the cell so repeated runs share nodes
    xmin = np.floor(xmin / cell) * cell; ymin = np.floor(ymin / cell) * cell
    return nodes.Grid.from_extent(xmin, xmax, ymin, ymax, cell)


def run(cfg, out_dir=None, log=print):
    base = cfg.get("_base_dir")
    site = io.Site.from_config(cfg, base)
    d = cfg.get("defaults", {})
    sigma_z = float(d.get("sigma_z", 0.015)); sigma_h = float(d.get("sigma_h", 0.01))
    prior = float(d.get("wander_prior", 0.03)); xr = float(d.get("crossover_radius", 0.5))
    slope = float(d.get("slope", 0.05)); sep = float(d.get("separable_ratio", 0.8))
    min_pairs = int(d.get("min_pairs", 3)); max_dz = float(d.get("max_dz", 0.25))
    reject = float(d.get("reject_sigma", 4.0))
    g = cfg.get("grid", {})
    capture = float(g.get("capture_radius", 1.5 * float(g.get("cell", 0.5))))
    k_sigma = float(g.get("k_sigma", 3.0))
    out = Path(out_dir or cfg.get("output", {}).get("dir", "output/ice-cube"))
    if not out.is_absolute() and base is not None:
        out = base / out
    out.mkdir(parents=True, exist_ok=True)

    outline = None
    oc = cfg.get("outline")
    if oc:
        p = Path(oc["path"])
        if not p.is_absolute() and base is not None:
            p = base / p
        outline = io.read_polygon_outline(p, site, oc["name"])

    # read everything first so the grid can cover all points
    surveys = {}
    for scfg in cfg.get("surveys", []):
        pts = io.read_survey(scfg, site, base).with_defaults(sigma_z, sigma_h)
        pts, infos = passes.segment(pts, scfg.get("pass_rule"))
        surveys[scfg["id"]] = (pts, infos, scfg)
        log(f"{scfg['id']}: {len(pts)} points, {len(infos)} passes")
    grid = _grid_from(cfg, outline, [v[0] for v in surveys.values()])
    mask = None
    if outline is not None:
        X, Y = np.meshgrid(grid.xs, grid.ys)
        mask = growth.point_in_polygon(X.ravel(), Y.ravel(), outline).reshape(X.shape)

    summary = [f"# Ice-Cube run", "", f"Grid: {grid.nx} x {grid.ny} nodes, cell {grid.cell} m, capture radius "
               f"{capture} m, k_sigma {k_sigma}, slope {slope} m/m. Sigma defaults {sigma_z*100:.1f} cm (z), "
               f"{sigma_h*100:.1f} cm (h). Wander prior {prior*100:.1f} cm, crossover radius {xr} m.", ""]
    surfaces = {}
    results = {}
    for sname, scfgs in _surfaces(cfg).items():
        parts = [surveys[s["id"]] for s in scfgs]
        pts_list = [p[0] for p in parts]
        infos = sum((p[1] for p in parts), [])
        role = scfgs[0].get("role", "surface")
        frames = {p.frame for p in pts_list}
        if len(frames) > 1:
            raise SystemExit(f"surface {sname}: surveys in different frames {sorted(frames)}")
        res = crossover.adjust(pts_list, radius=xr, prior=prior, slope=slope, separable_ratio=sep,
                               min_pairs=min_pairs, max_dz=max_dz, reject_sigma=reject)
        corrected, sbs = [], []
        for p in pts_list:
            c, sb = crossover.apply(p, res)
            corrected.append(c); sbs.append(sb)
        date = max(p.date for p in pts_list)
        surf = nodes.grid_surface(corrected, sbs, grid, capture_radius=capture, slope=slope, k_sigma=k_sigma,
                                  name=sname, date=date, frame=pts_list[0].frame)
        surfaces[sname] = surf
        results[sname] = res
        np.savez(out / f"surface_{sname}.npz", z=surf.z, sigma=surf.sigma, n=surf.n, flags=surf.flags,
                 last_t=surf.last_t, npass=surf.npass, **grid.as_dict())
        (out / f"passes_{sname}.md").write_text(report.table_passes(infos) + "\n")
        (out / f"crossover_{sname}.md").write_text(report.table_crossover(res) + "\n")
        (out / f"flags_{sname}.md").write_text(report.table_flags(surf) + "\n")
        report.fig_points(corrected, out / f"points_{sname}.png", outline, title=f"{sname}: points by pass")
        report.fig_biases(res, out / f"biases_{sname}.png", title=f"{sname}: crossover biases")
        if res.n_obs:
            report.fig_hist(res.residuals * 100, out / f"xover_resid_{sname}.png", "pair residual after adjustment (cm)",
                            f"{sname}: crossover residuals")
        for what in ("z", "sigma", "n", "flags"):
            report.fig_surface(surf, out / f"{what}_{sname}.png", what, outline, title=f"{sname} ({date}): {what}",
                               mask=mask if (role != "validation" and mask is not None) else None)
        m = mask if (mask is not None and role != "validation") else (surf.n > 0)
        report.fig_hist(surf.sigma[m & (surf.n > 0)] * 100, out / f"sigma_hist_{sname}.png", "node sigma (cm)",
                        f"{sname}: node sigma")
        rel = growth.relief(surf, m)
        prel = growth.point_relief(corrected, outline if role != "validation" else None)
        praw = growth.point_relief(pts_list, outline if role != "validation" else None)
        summary += [f"## Surface {sname} ({date}, frame {surf.frame}, role {role})", "",
                    f"Surveys: {', '.join(p.survey for p in pts_list)}; {sum(len(p) for p in pts_list)} points, "
                    f"{len(infos)} passes.", "", "### Passes", "", report.table_passes(infos), "",
                    "### Crossover", "", report.table_crossover(res), "", "### Nodes", "",
                    report.table_surface(surf, m), ""]
        if rel.get("n"):
            summary += [f"Relief inside mask: n {rel['n']}, max-min {rel['range']*100:.1f} cm, sd {rel['sd']*100:.1f} cm, "
                        f"p2.5-p97.5 {rel['p2_5_97_5']*100:.1f} cm, node sigma median {rel['sigma_median']*100:.1f} cm; "
                        f"datum sigma {surf.datum_sigma*100:.1f} cm.", ""]
        if prel.get("n"):
            summary += [f"Point relief inside mask (after bias correction): n {prel['n']}, max-min {prel['range']*100:.1f} cm, "
                        f"sd {prel['sd']*100:.1f} cm; raw points: max-min {praw['range']*100:.1f} cm, sd {praw['sd']*100:.1f} cm.", ""]
        summary += ["### Flagged nodes", "", report.table_flags(surf, m, limit=15), ""]
        log(f"surface {sname}: " + report.table_surface(surf, m))

    for gc in cfg.get("growth", []):
        a, b = gc["from"], gc["to"]
        if a not in surfaces or b not in surfaces:
            log(f"growth {a}->{b}: skipped, surface missing"); continue
        gr = growth.difference(surfaces[a], surfaces[b], offset=gc.get("offset"), mask=mask)
        st = gr.stats()
        report.fig_growth(gr, grid, out / f"growth_{a}_{b}.png", outline, title=f"{b} minus {a}")
        np.savez(out / f"growth_{a}_{b}.npz", dz=gr.dz, sigma=gr.sigma, mask=gr.mask, **grid.as_dict())
        vol = growth.volume(gr.dz, gr.mask, grid.cell)
        summary += [f"## Growth {a} -> {b}", "",
                    (f"n {st['n']}, mean {st['mean']*100:+.1f} cm (weighted {st['wmean']*100:+.1f}; sigma of the mean "
                     f"{st['sigma_of_mean']*100:.1f} cm, of which datum {gr.datum_sigma*100:.1f} cm), median {st['median']*100:+.1f} cm, sd over nodes {st['sd']*100:.1f} cm, "
                     f"node sigma median {st['sigma_median']*100:.1f} cm, p5..p95 {st['p05']*100:+.1f}..{st['p95']*100:+.1f} cm, "
                     f"volume above {a} {vol:.2f} m^3" if st['n'] else "no overlap"), ""]
        log(f"growth {a}->{b}: {summary[-2]}")

    for fc in cfg.get("fill", []):
        s = surfaces.get(fc["surface"])
        if s is None:
            continue
        m = mask if mask is not None else (s.n > 0)
        fill, level, vol = growth.fill_to_level(s, m, level=fc.get("level"), margin=float(fc.get("margin", 0.05)),
                                                percentile=float(fc.get("percentile", 99.0)))
        np.savez(out / f"fill_{fc['surface']}.npz", fill=fill, level=level, **grid.as_dict())
        med = float(np.nanmedian(s.z[m & np.isfinite(s.z)]))
        summary += [f"## Fill to level, surface {fc['surface']}", "",
                    f"Level {(level-med)*100:+.1f} cm above the surface median, fill volume {vol:.2f} m^3, "
                    f"max depth {np.nanmax(fill)*100:.1f} cm, mean depth {np.nanmean(fill[m])*100:.1f} cm.", ""]

    (out / "summary.md").write_text("\n".join(summary))
    (out / "run.json").write_text(json.dumps({"grid": grid.as_dict(), "surfaces": list(surfaces),
                                              "crossover": {k: dict(n_obs=v.n_obs, chi2_dof=v.chi2_dof,
                                                                     components=v.components,
                                                                     bias_cm=(v.bias * 100).round(2).tolist(),
                                                                     sigma_cm=(v.sigma * 100).round(2).tolist(),
                                                                     separable=v.separable.tolist())
                                                            for k, v in results.items()}}, indent=1))
    return surfaces, results


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m icecube", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the pipeline on a config")
    r.add_argument("config")
    r.add_argument("--out", help="output directory (overrides [output].dir)")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        cfg = load_config(a.config)
        run(cfg, a.out)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
