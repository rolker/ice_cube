"""How much each survey wanders: height differences between points within 0.9 m,
grouped by the time between them. Also tries a crossover drift adjustment on October."""
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from config import X0, Y0, SH, ZB, CONTROL_HOME
from ground import clean, points, fit
from surveys import nov, oct_topo, feb, mar

pd.set_option("display.width", 200)
BINS = [0, 3, 8, 15, 25, 40, 70]


def tm(s):
    return pd.to_datetime(s.str[:23], format="%m/%d/%Y %H:%M:%S.%f")


def pairs(q, r=.9):
    q = q.reset_index(drop=True)
    P = cKDTree(q[["x", "y"]].values).query_pairs(r, output_type="ndarray")
    dt = abs((q.t.values[P[:, 0]] - q.t.values[P[:, 1]]) / np.timedelta64(60, "s"))
    dz = q.z.values[P[:, 0]] - q.z.values[P[:, 1]]
    return P, dt, dz


def vario(q, lab):
    P, dt, dz = pairs(q)
    k = abs(dz - np.median(dz)) < .35
    t = pd.DataFrame({"dt": dt[k], "dz": dz[k]})
    g = t.groupby(pd.cut(t.dt, BINS), observed=True).dz.agg(
        n="count",
        robust_sd=lambda v: 1.4826 * np.median(abs(v)) / np.sqrt(2)).round(3)
    print(lab, "pairs", k.sum())
    print(g.T.to_string())


def adjust(q, knot=4.0, lam=.5):
    """Piecewise-linear height bias in time, solved from crossovers. Not reliable: it
    invented about 15 cm of drift on the stable Nov session."""
    q = q.sort_values("t").reset_index(drop=True)
    m = (q.t - q.t.iloc[0]).dt.total_seconds().values / 60
    P, dt, dz = pairs(q)
    k = (dt > 1.5) & (abs(dz - np.median(dz)) < .35)
    P = P[k]; dz = dz[k]
    K = np.arange(0, m[-1] + knot, knot)

    def B(mm):
        i = np.clip(np.searchsorted(K, mm) - 1, 0, len(K) - 2)
        w = (mm - K[i]) / knot
        R = np.zeros((len(mm), len(K)))
        R[np.arange(len(mm)), i] = 1 - w
        R[np.arange(len(mm)), i + 1] = w
        return R

    A = B(m[P[:, 0]]) - B(m[P[:, 1]])
    D2 = lam * (np.eye(len(K), k=0)[:-1] - np.eye(len(K), k=1)[:-1])
    b = np.linalg.lstsq(np.vstack([A, D2, 1e-3 * np.ones((1, len(K)))]), np.r_[dz, np.zeros(len(K) - 1), 0], rcond=None)[0]
    q["b"] = B(m) @ b
    q["zc"] = q.z - q.b
    return q, b, len(dz)


if __name__ == "__main__":
    o = oct_topo()
    o = o[o.Remarks.isna() & (o.Elevation > 15.7)].copy()
    o["t"] = tm(o.Time); o["x"] = o.X - X0; o["y"] = o.Y - Y0; o["z"] = o.Elevation
    n = nov()
    c = (abs(n.X - X0 - CONTROL_HOME[0]) < .1) & (abs(n.Y - Y0 - CONTROL_HOME[1]) < .1)
    n = n[~c & (n["Fix ID"] == 4) & (n.Elevation > 14.5) & (n.Elevation < 15.3)].copy()
    n["t"] = tm(n.Time); n["x"] = n.X + SH[0] - X0; n["y"] = n.Y + SH[1] - Y0; n["z"] = n.Elevation + SH[2] + ZB
    n = n[n.z > 15.7]
    for day, q in o.groupby(o.t.dt.date):
        vario(q, f"Oct {day}")
    vario(n, "Nov 4")
    for lab, fr in [("Feb 14 ice", feb()), ("Mar 2", mar())]:
        fr = fr[fr.rem == ""].iloc[:-1 if lab.startswith("Feb") else None].reset_index(drop=True)
        P = cKDTree(fr[["X", "Y"]].values).query_pairs(1.0, output_type="ndarray")
        gap = abs(P[:, 0] - P[:, 1])
        dz = fr.Z.values[P[:, 0]] - fr.Z.values[P[:, 1]]
        t = pd.DataFrame({"g": gap, "dz": dz})
        print(lab, "by shot-order gap, points", len(fr))
        print(t.groupby(pd.cut(t.g, [0, 5, 15, 40, 80, 200]), observed=True).dz.agg(
            n="count", robust_sd=lambda v: 1.4826 * np.median(abs(v)) / np.sqrt(2)).round(3).T.to_string())
    ref, _ = clean(points("nov"))
    ref = ref[ref.z > 15.7]
    for day, q in o.groupby(o.t.dt.date):
        a, b, np_ = adjust(q)
        z = fit(ref, a.x.values, a.y.values)
        r0 = a.z.values - z; r1 = a.zc.values - z
        ok = ~np.isnan(r0) & (abs(r0) < .3)
        print(day, "crossovers", np_, "bias range cm", round((b.max() - b.min()) * 100, 1),
              "Oct-Nov SD before", round(r0[ok].std(), 3), "after", round(r1[ok].std(), 3))
    a, b, np_ = adjust(n)
    print("Nov control: crossovers", np_, "bias range cm", round((b.max() - b.min()) * 100, 1))
