#!/usr/bin/env python3
"""Check the registration of the local imagery copies made by fetch_imagery.py.

    # crops of an image around surveyed features, the features drawn on top
    python3 analysis/qgis/imagery_check.py crops IMAGE.tif --layers things.geojson fence.geojson \
        --gpkg season.gpkg --match "shed|deck" --point "corner=2.66,29.21" --half 6 --out DIR

    # offset of one image against another (phase correlation over a window)
    python3 analysis/qgis/imagery_check.py align REF.tif IMAGE.tif --window X0 Y0 X1 Y1

crops: for every feature whose name matches --match (case-insensitive regex; Points, and
the vertices' centroid for lines and polygons) and every --point NAME=X,Y (local metres),
writes DIR/<name>.png: the image within --half metres, enlarged --zoom times, with all
features of the layers drawn (points as crosses with a 0.2 m ring, lines and polygons as
outlines) and a 1 m scale bar; and DIR/sheet.png with all crops. Compare where the
surveyed thing is with where the image shows it; an offset common to several well-defined
features is the image's residual (set it as [imagery.extra_shift_m] and refetch).
GeoJSON coordinates are [lon, lat, h] in our frame; the local frame comes from the gpkg's
icecube_meta (or [site.origin] of --config).

align: resamples IMAGE onto REF's grid within the window (local metres; default the
overlap), and finds the shift (dx, dy) in metres that moves IMAGE onto REF by phase
correlation of the grey images (sub-pixel by a parabola through the peak). It prints the
shift and the peak strength; use stable features (roofs, driveway, road) in the window,
since trees, shadows and cars differ between years. Add the printed dx, dy to IMAGE's
extra_shift_m.

Dependencies: numpy, rasterio, Pillow. Outputs show the site: keep them private.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fetch_imagery as fi  # noqa: E402


# ----------------------------------------------------------------------------- features

def load_features(paths, frame):
    """[(name, kind, [(x, y), ...])] in local metres; kind Point/LineString/Polygon."""
    out = []

    def xy(c):
        return frame.enu(c[1], c[0], c[2] if len(c) > 2 else frame.h)[:2]
    for p in paths:
        for f in json.loads(Path(p).read_text()).get("features", []):
            g = f.get("geometry") or {}
            name = str((f.get("properties") or {}).get("name") or (f.get("properties") or {}).get("label") or "")
            t, c = g.get("type"), g.get("coordinates")
            if t == "Point":
                out.append((name, t, [xy(c)]))
            elif t == "LineString":
                out.append((name, t, [xy(k) for k in c]))
            elif t == "Polygon":
                out.append((name, t, [xy(k) for k in c[0]]))
            elif t == "MultiPolygon":
                for poly in c:
                    out.append((name, "Polygon", [xy(k) for k in poly[0]]))
    return out


def centre(pts):
    a = np.asarray(pts, float)
    return float(a[:, 0].mean()), float(a[:, 1].mean())


def slug(s):
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_") or "point"


# ----------------------------------------------------------------------------- crops

def read_rgb(path):
    import rasterio
    with rasterio.open(path) as ds:
        a = ds.read()
        t = ds.transform
    rgb = a[:3] if a.shape[0] >= 4 or a.shape[0] == 3 else np.repeat(a[:1], 3, 0)
    return np.moveaxis(rgb, 0, -1), (t.c, t.f, t.a)   # x_left, y_top, pixel


def crop(rgb, geo, cx, cy, half, zoom, feats, title):
    from PIL import Image, ImageDraw
    xl, yt, p = geo
    c0 = int(np.floor((cx - half - xl) / p))
    r0 = int(np.floor((yt - (cy + half)) / p))
    n = int(np.ceil(2 * half / p))
    h, w = rgb.shape[:2]
    tile = np.zeros((n, n, 3), np.uint8)
    rs, cs = max(r0, 0), max(c0, 0)
    re_, ce = min(r0 + n, h), min(c0 + n, w)
    if re_ > rs and ce > cs:
        tile[rs - r0:re_ - r0, cs - c0:ce - c0] = rgb[rs:re_, cs:ce]
    im = Image.fromarray(tile).resize((n * zoom, n * zoom), Image.NEAREST)
    d = ImageDraw.Draw(im)
    x0, y0 = xl + c0 * p, yt - r0 * p

    def px(x, y):
        return (x - x0) / p * zoom, (y0 - y) / p * zoom
    ring = 0.2 / p * zoom
    for name, kind, pts in feats:
        if kind == "Point":
            x, y = px(*pts[0])
            d.line([x - 8, y, x + 8, y], fill=(255, 0, 255), width=1)
            d.line([x, y - 8, x, y + 8], fill=(255, 0, 255), width=1)
            d.ellipse([x - ring, y - ring, x + ring, y + ring], outline=(255, 255, 0))
            d.text((x + 6, y + 4), name[:28], fill=(255, 255, 0))
        else:
            d.line([px(*q) for q in pts], fill=(0, 255, 255), width=1)
    bar = 1.0 / p * zoom
    d.rectangle([8, n * zoom - 14, 8 + bar, n * zoom - 10], fill=(255, 255, 255))
    d.text((8, n * zoom - 28), "1 m", fill=(255, 255, 255))
    d.text((8, 6), title[:60], fill=(255, 255, 255))
    return im


def cmd_crops(args, frame):
    rgb, geo = read_rgb(args.image)
    feats = load_features(args.layers or [], frame)
    targets = []
    if args.match:
        rx = re.compile(args.match, re.I)
        targets += [(n, *centre(pts)) for n, k, pts in feats if rx.search(n)]
    for spec in args.point or []:
        name, xy = spec.split("=", 1)
        x, y = map(float, xy.split(","))
        feats.append((name, "Point", [(x, y)]))
        targets.append((name, x, y))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ims = []
    for name, x, y in targets:
        im = crop(rgb, geo, x, y, args.half, args.zoom, feats, f"{name}  x {x:.2f} y {y:.2f}")
        im.save(out / f"{slug(name)}.png")
        ims.append(im)
    if ims:
        from PIL import Image
        cols = min(4, len(ims))
        s = ims[0].size[0]
        sheet = Image.new("RGB", (cols * s, -(-len(ims) // cols) * s))
        for i, im in enumerate(ims):
            sheet.paste(im, ((i % cols) * s, (i // cols) * s))
        sheet.save(out / "sheet.png")
    print(f"{len(ims)} crop(s) in {out}")


# ----------------------------------------------------------------------------- align

def grey_on_grid(path, xl, yt, p, nc, nr):
    """IMAGE resampled (bilinear) onto the north-up local grid; grey float32, NaN outside."""
    import rasterio
    with rasterio.open(path) as ds:
        a = ds.read().astype(np.float32)
        t = ds.transform
        alpha = a[-1] > 0 if a.shape[0] in (2, 4) else np.ones(a.shape[1:], bool)
    g = a[:3].mean(axis=0) if a.shape[0] >= 3 else a[0]
    xs = xl + (np.arange(nc) + 0.5) * p
    ys = yt - (np.arange(nr) + 0.5) * p
    gx, gy = np.meshgrid(xs, ys)
    col = (gx - t.c) / t.a - 0.5
    row = (t.f - gy) / -t.e - 0.5
    out, ok = fi.bilinear(g[None], alpha, col, row)
    o = out[0]
    o[~ok] = np.nan
    return o


def phase_correlation(ref, img):
    """(dy, dx) in pixels that moves img onto ref, and the normalised peak height."""
    m = np.isfinite(ref) & np.isfinite(img)
    a = np.where(m, ref - np.nanmean(ref[m]), 0.0)
    b = np.where(m, img - np.nanmean(img[m]), 0.0)
    win = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1]))
    fa, fb = np.fft.fft2(a * win), np.fft.fft2(b * win)
    r = fa * np.conj(fb)
    r /= np.abs(r) + 1e-12
    c = np.real(np.fft.ifft2(r))
    i, j = np.unravel_index(np.argmax(c), c.shape)

    def sub(cm, c0, cp):
        den = cm - 2 * c0 + cp
        return 0.0 if den == 0 else 0.5 * (cm - cp) / den
    h, w = c.shape
    di = i + sub(c[(i - 1) % h, j], c[i, j], c[(i + 1) % h, j])
    dj = j + sub(c[i, (j - 1) % w], c[i, j], c[i, (j + 1) % w])
    if di > h / 2:
        di -= h
    if dj > w / 2:
        dj -= w
    return di, dj, float(c[i, j])


def cmd_align(args):
    import rasterio
    with rasterio.open(args.ref) as r, rasterio.open(args.image) as s:
        rb, sb = r.bounds, s.bounds
        p = args.pixel or max(r.transform.a, s.transform.a)
    win = args.window or (max(rb.left, sb.left), max(rb.bottom, sb.bottom),
                          min(rb.right, sb.right), min(rb.top, sb.top))
    xl, yt, nc, nr = fi.output_grid(win, p)
    ref = grey_on_grid(args.ref, xl, yt, p, nc, nr)
    img = grey_on_grid(args.image, xl, yt, p, nc, nr)
    di, dj, peak = phase_correlation(ref, img)
    dx, dy = dj * p, -di * p
    print(f"window x {win[0]:.1f}..{win[2]:.1f} y {win[1]:.1f}..{win[3]:.1f} at {p:.2f} m: "
          f"move {Path(args.image).name} by dx {dx:+.2f} dy {dy:+.2f} m to match {Path(args.ref).name} "
          f"(peak {peak:.3f}; under ~0.05 is noise)")
    return dx, dy, peak


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("crops", help="crops around surveyed features")
    c.add_argument("image")
    c.add_argument("--layers", nargs="+", help="GeoJSON layers ([lon, lat, h] in our frame)")
    c.add_argument("--match", help="regex: crop around the features whose name matches")
    c.add_argument("--point", action="append", help="NAME=X,Y extra point in local metres (repeatable)")
    c.add_argument("--half", type=float, default=6.0, help="half size of a crop, metres (default 6)")
    c.add_argument("--zoom", type=int, default=6, help="enlargement (default 6)")
    c.add_argument("--out", required=True, help="output folder")
    c.add_argument("--gpkg", help="season gpkg (local frame origin)")
    c.add_argument("--config", help="site config with [site.origin] (instead of --gpkg)")
    a = sub.add_parser("align", help="offset of IMAGE against REF")
    a.add_argument("ref")
    a.add_argument("image")
    a.add_argument("--window", nargs=4, type=float, metavar=("X0", "Y0", "X1", "Y1"))
    a.add_argument("--pixel", type=float, help="comparison grid, metres (default the coarser)")
    args = ap.parse_args(argv)
    if args.cmd == "align":
        cmd_align(args)
        return 0
    cfg = fi.load_config(args.config)[0] if args.config else {}
    frame = fi.resolve_frame(cfg, fi.read_gpkg_meta(args.gpkg))
    cmd_crops(args, frame)
    return 0


if __name__ == "__main__":
    sys.exit(main())
