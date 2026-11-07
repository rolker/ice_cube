"""Loaders for the SW Maps exports. Coordinates are projected to UTM 19N."""
import json

import numpy as np
import pandas as pd
import pyproj

from config import EPSG, f

T = pyproj.Transformer.from_crs(4326, EPSG, always_xy=True)


def gj_pts(path):
    d = json.load(open(path))
    r = []
    for ft in d["features"]:
        g = ft["geometry"]
        p = ft["properties"]
        if g["type"] == "Point":
            r.append((*T.transform(*g["coordinates"][:2]), g["coordinates"][2], p.get("REMARKS") or "", p.get("NAME") or ""))
    return pd.DataFrame(r, columns=["X", "Y", "Z", "rem", "name"])


def gj_polys(path):
    d = json.load(open(path))
    r = {}
    for ft in d["features"]:
        g = ft["geometry"]
        if g["type"] == "Polygon":
            c = np.array(g["coordinates"][0])
            x, y = T.transform(c[:, 0], c[:, 1])
            r[ft["properties"].get("NAME")] = np.c_[x, y, c[:, 2]]
    return r


def nov():
    """Nov 4 points, home-base frame."""
    return pd.read_csv(f("nov4_points"))


def oct_topo():
    """Oct 18 and 26 ground points, MassDOT frame."""
    return pd.read_csv(f("oct_topo"))


def oct_things():
    """Oct features: swing set, arbor, stone wall, deck step."""
    return pd.read_csv(f("oct_things"))


def feb():
    return gj_pts(f("feb_ice"))


def mar():
    return gj_pts(f("mar_rink"))


def jan():
    """Jan 9 outlines: rink, deck, sheds, arbor, driveway."""
    return gj_polys(f("jan_outlines"))
