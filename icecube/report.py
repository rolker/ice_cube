"""Figures and tables. Every figure is in local metres from the site origin (axes
"east (m)" / "north (m)"), no basemap, and nothing here prints an absolute coordinate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .crossover import CrossoverResult
from .growth import Growth
from .nodes import Surface

SEQ = "Blues"       # one hue, light -> dark, for magnitudes (height, sigma, count)
DIV = "RdBu"        # two hues with a neutral midpoint, for growth (sign matters)


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"axes.grid": True, "grid.alpha": 0.25, "axes.spines.top": False,
                         "axes.spines.right": False, "figure.dpi": 110})
    return plt


def _draw_outline(ax, outline):
    if outline is not None and len(outline):
        ring = np.vstack([outline, outline[:1]])
        ax.plot(ring[:, 0], ring[:, 1], color="black", lw=1, ls="--", label="outline")


def _extent(g):
    h = g.cell / 2
    return [g.x0 - h, g.x0 + (g.nx - 1) * g.cell + h, g.y0 - h, g.y0 + (g.ny - 1) * g.cell + h]


def fig_surface(surface: Surface, path, what="z", outline=None, title=None, ref=None, mask=None):
    """Map of z (relative to `ref`, default the masked median), sigma, n or outliers."""
    plt = _plt()
    g = surface.grid
    if what == "z":
        data = surface.z.copy()
        m = mask if mask is not None else np.isfinite(data)
        r = float(np.nanmedian(data[m])) if ref is None else ref
        data = (data - r) * 100
        label = "height minus median (cm)"
        cmap = SEQ
    elif what == "sigma":
        data = surface.sigma * 100; label = "node sigma (cm)"; cmap = SEQ
    elif what == "n":
        data = np.where(surface.n > 0, surface.n, np.nan).astype(float); label = "soundings per node"; cmap = SEQ
    elif what == "outliers":
        data = np.where(surface.n > 0, surface.n_out, np.nan).astype(float); label = "outliers (n_out)"; cmap = "Oranges"
    else:
        raise ValueError(what)
    if mask is not None:
        data = np.where(mask, data, np.nan)
    fig, ax = plt.subplots(figsize=(6.4, 5.2))
    im = ax.imshow(data, origin="lower", extent=_extent(g), cmap=cmap, interpolation="nearest")
    fig.colorbar(im, ax=ax, label=label, shrink=0.85)
    _draw_outline(ax, outline)
    ax.set_aspect("equal"); ax.set_xlabel("east (m, local)"); ax.set_ylabel("north (m, local)")
    ax.set_title(title or f"{surface.name} {what}")
    fig.tight_layout(); fig.savefig(path); plt.close(fig)


def fig_growth(gr: Growth, grid, path, outline=None, title=None, vmax=None):
    plt = _plt()
    data = np.where(gr.mask, gr.dz, np.nan) * 100
    v = vmax if vmax is not None else float(np.nanmax(np.abs(data))) if np.isfinite(data).any() else 1.0
    fig, ax = plt.subplots(figsize=(6.4, 5.2))
    im = ax.imshow(data, origin="lower", extent=_extent(grid), cmap=DIV, vmin=-v, vmax=v, interpolation="nearest")
    fig.colorbar(im, ax=ax, label=f"{gr.b} minus {gr.a} (cm)", shrink=0.85)
    _draw_outline(ax, outline)
    ax.set_aspect("equal"); ax.set_xlabel("east (m, local)"); ax.set_ylabel("north (m, local)")
    ax.set_title(title or f"growth {gr.a} -> {gr.b}")
    fig.tight_layout(); fig.savefig(path); plt.close(fig)


def fig_biases(res: CrossoverResult, path, title=None):
    """Per-pass bias with its posterior sigma; hollow markers where not separable."""
    plt = _plt()
    n = len(res.bias)
    fig, ax = plt.subplots(figsize=(max(5, 0.35 * n + 2), 3.6))
    xs = np.arange(n)
    for k in range(n):
        ax.errorbar(k, res.bias_raw[k] * 100, yerr=res.sigma[k] * 100, fmt="o",
                    mfc="#2f5d8a" if res.separable[k] else "white", mec="#2f5d8a", ecolor="#2f5d8a",
                    capsize=2, ms=5, lw=1)
    ax.axhline(0, color="black", lw=0.8)
    ax.axhspan(-res.prior * 100, res.prior * 100, color="#2f5d8a", alpha=0.08, lw=0)
    ax.set_xticks(xs); ax.set_xticklabels([f"{s}\n{p}" for s, p in res.pass_keys], fontsize=7)
    ax.set_ylabel("pass bias (cm)")
    ax.set_title(title or "crossover biases (hollow = not separable, held at zero; band = prior)")
    fig.tight_layout(); fig.savefig(path); plt.close(fig)


def fig_hist(values_cm, path, xlabel, title, bins=30):
    plt = _plt()
    fig, ax = plt.subplots(figsize=(5.2, 3.4))
    v = np.asarray(values_cm); v = v[np.isfinite(v)]
    ax.hist(v, bins=bins, color="#2f5d8a", alpha=0.9)
    ax.set_xlabel(xlabel); ax.set_ylabel("count"); ax.set_title(title)
    fig.tight_layout(); fig.savefig(path); plt.close(fig)


def fig_points(surveys, path, outline=None, title="points by pass"):
    """Where the points are, coloured by pass (categorical, fixed order)."""
    plt = _plt()
    fig, ax = plt.subplots(figsize=(6.4, 5.2))
    cmap = plt.get_cmap("tab10")
    k = 0
    for s in surveys:
        for pid in np.unique(s.pass_id):
            m = s.pass_id == pid
            ax.plot(s.x[m], s.y[m], ".", ms=3, color=cmap(k % 10), label=f"{s.survey} p{pid}" if k < 10 else None)
            k += 1
    _draw_outline(ax, outline)
    ax.set_aspect("equal"); ax.set_xlabel("east (m, local)"); ax.set_ylabel("north (m, local)")
    ax.set_title(title)
    if k <= 10:
        ax.legend(fontsize=7, loc="best")
    fig.tight_layout(); fig.savefig(path); plt.close(fig)


# ------------------------------------------------------------------------------ tables

def _hms(t):
    return "-" if not np.isfinite(t) else datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M:%S")


def table_passes(infos):
    rows = ["| survey | pass | n | start | end | path length (m) |", "|---|---|---|---|---|---|"]
    for i in infos:
        rows.append(f"| {i.survey} | {i.pass_id} | {i.n} | {_hms(i.t0)} | {_hms(i.t1)} | {i.length_m:.1f} |")
    return "\n".join(rows)


def table_crossover(res: CrossoverResult):
    rows = [f"Observations {res.n_obs} (dropped: {res.n_cut} beyond max_dz, {res.n_rejected} by the residual "
            f"test), components {res.components}, chi2/dof {res.chi2_dof:.2f}, rms of pair differences before "
            f"{res.rms_before*100:.1f} cm, after {res.rms_after*100:.1f} cm, prior {res.prior*100:.1f} cm.", "",
            "| survey | pass | pairs | bias (cm) | sigma (cm) | raw (cm) | status |", "|---|---|---|---|---|---|---|"]
    for k, (s, p) in enumerate(res.pass_keys):
        st = "found" if res.separable[k] else "not separable, held at zero"
        if res.separable[k] and abs(res.bias[k]) < 2 * res.sigma[k]:
            st = "found, consistent with zero"
        rows.append(f"| {s} | {p} | {res.n_pairs_per_pass[k]} | {res.bias[k]*100:+.1f} | {res.sigma[k]*100:.1f} | "
                    f"{res.bias_raw[k]*100:+.1f} | {st} |")
    return "\n".join(rows)


def table_surface(surface: Surface, mask=None):
    m = (surface.n > 0) if mask is None else (mask & (surface.n > 0))
    s = surface.sigma[m] * 100
    if len(s) == 0:
        return "no nodes"
    q = np.percentile(s, [5, 50, 95])
    return (f"nodes {int(m.sum())}, soundings per node median {int(np.median(surface.n[m]))}, "
            f"node sigma p5/median/p95 {q[0]:.1f}/{q[1]:.1f}/{q[2]:.1f} cm, "
            f"nodes with outliers {int((surface.n_out[m] > 0).sum())} (outliers {int(surface.n_out[m].sum())})")


def table_outliers(surface: Surface, mask=None, limit=30):
    """Nodes with outliers, and nodes the plane pass rebuilt, in local metres: soundings,
    outliers (side bins and Eeg culls), the largest side bin (an outlier bin, not a
    surface), the strength max(0, 5 - n_in/n_out), the worst normalised difference of any
    sounding from the node's final estimate (robust = false: n_out is the running flag
    count and "worst" the running maximum), and the neighbour-plane gradient |g| where the
    plane pass replaced pass 1 (issue #29). A first line counts the planar nodes. Planar
    nodes are listed first (cleared ones have a low worst difference and would otherwise
    drop off a long list), then the rest, each group by worst difference; at most `limit`
    rows, and a line says when the list is cut."""
    planar = surface.planar if surface.planar is not None else np.zeros(surface.n.shape, bool)
    m = (surface.n_out > 0) | planar
    if mask is not None:
        m &= mask
    jj, ii = np.where(m)
    if len(jj) == 0:
        return "no nodes with outliers"
    head = [f"Planar nodes (rebuilt on a neighbour plane): {int(planar[m].sum())}"]
    order = np.lexsort((-surface.maxnr[jj, ii], ~planar[jj, ii]))
    if len(order) > limit:
        head.append(f"Showing {limit} of {len(order)} nodes: planar first, then by worst difference.")
    order = order[:limit]
    rows = head + ["", "| east (m) | north (m) | n | passes | outliers | largest bin | bin minus node (cm) | strength | "
            "worst (sigma) | node sigma (cm) | plane gradient |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for k in order:
        a, b = jj[k], ii[k]
        dz = surface.z_alt[a, b] - surface.z[a, b]
        gr = f"{surface.gradient[a, b]:.2f}" if planar[a, b] else ""
        rows.append(f"| {surface.grid.xs[b]:.1f} | {surface.grid.ys[a]:.1f} | {surface.n[a, b]} | {surface.npass[a, b]} | "
                    f"{surface.n_out[a, b]} | {surface.n_alt[a, b]} | {'' if not np.isfinite(dz) else f'{dz*100:+.1f}'} | "
                    f"{surface.strength[a, b]:.1f} | {surface.maxnr[a, b]:.1f} | {surface.sigma[a, b]*100:.1f} | {gr} |")
    return "\n".join(rows)
