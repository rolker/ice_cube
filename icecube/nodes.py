"""The node estimator, after CUBE (Calder & Mayer 2003): a median-first rebuild per node
with outlier bins (issue #24).

Grid. Nodes at x = x0 + i*cell, y = y0 + j*cell. A sounding contributes to every
node within `capture_radius` of it.

Propagation. A sounding with vertical sigma sz, horizontal sigma sh, at horizontal
distance d from the node, is given at the node the variance

    v = sz^2 + (slope * (d + sh))^2,      weight w = 1.0 / v

i.e. the surface may differ from the sounding by `slope` metres per metre of
horizontal separation (one sigma), and the sounding's own horizontal uncertainty adds
to the separation. This replaces CUBE's IHO-order-based inflation, which has no meaning
for a 1 cm RTK pole. In addition each sounding carries the bias uncertainty sb of its
pass (the crossover posterior sigma, or the prior where no adjustment was possible).

Rebuild (`rebuild`, the definition the logger's Coverage.java ports). Each contribution
is an entry (z, v, w, id); `id` is unique within a node (the time-order index here, the
capture number in the logger).
1. Weighted median: sort by (z, id); W = sum of w in that order (a plain loop); walk the
   same order with cum += w; zm is the z of the first entry with 2*cum >= W. Two equal
   weights give the lower one.
2. Feed the entries in order of (abs(z - zm), id). The first starts the main hypothesis.
   Each next one is tested against the main first,

       nr = |z_i - WZ/W| / sqrt(1/W + v_i + var_bias_diff)

   and joins it when nr <= k_sigma; otherwise it joins the side hypothesis with the
   smallest nr <= k_sigma (ties: the earlier-created side) or opens a new one. A
   hypothesis keeps W += w, WZ += w*z, its member count and its own per-pass weights.
   var_bias_diff is the bias part of the difference,
   (1 - W_p/W)^2 sb_p^2 + sum_{q != p} (W_q/W)^2 sb_q^2 with W_p the hypothesis's weight
   from the sounding's own pass: a sounding from a pass the node has not seen adds its
   full sb_p^2, one from the pass that makes up the whole hypothesis adds nothing.
3. Promotion, once, after all entries: the side with the largest key (ties: the earlier
   created) becomes the main when its key is strictly greater than the main's. Key: the
   summed weight W here (batch, `promote="weight"`), so a few RTK shots are not outvoted
   by many low-weight DEM samples; the member count in the logger (`promote="count"`).

Side hypotheses are outlier bins, not surfaces: there is one ice surface per cell, and
a side bin's mean (z_alt) is reported only to say where the outliers sit.

Outputs. From the main hypothesis:

    z = WZ / W,     var_noise = 1 / W
    var_bias = sum_p (W_p / W)^2 * sb_p^2       (W_p = sum of weights from pass p)
    sigma = sqrt(var_noise + var_bias)

n counts every contribution, n_in the main's members, n_out = n - n_in (side members
and Eeg culls), n_alt / z_alt the largest side bin by count (0 / NaN when none),
max_nr the largest nr of any entry against the final main, and
strength = max(0, 5 - n_in / n_out), 0 when n_out = 0. The strength is CUBE-style, not
CUBE's ratio: CUBE divides the best hypothesis's count by the second-best's
(MAX_HYPOTHESIS_RATIO 5); here every side member is in the denominator. The logger
draws a node red while n_out > 0 and n_in < 2 * n_out (strength > 3).

Eeg's test (batch, `eeg_quotient_limit`, CUBE's cube_node_truncate in a weighted form):
on a main with N >= EEG_MIN_N (11, CUBE's median queue length; with fewer members the
quotient's F(1, N - 2) tail is too heavy for a fixed limit: at N = 4 about 3 % of clean
nodes would lose a member) members, for member k let m_k be the weighted mean of the others,
d_k = z_k - m_k, s2_k = sum_{i != k} w_i (z_i - m_k)^2 / (N - 2) (the others' scatter in
units of their stated variances), and

    q_k = d_k^2 / max(s2_k * (v_k + 1 / (W - w_k)), EEG_FLOOR^2)

For equal weights this is CUBE's quotient. The test is scale-free: it compares a member
with the others' actual scatter, so it bites where the stated sigmas are pessimistic
(after the k-sigma split it cannot fire where they are right). The floor (1 mm, the
heights' quantisation) is the denominator guard: identical heights plus one 1 mm off,
or all identical, give a finite q and are kept. All members with q_k >= limit are culled
at once, counted in n_out and n_eeg, and z, sigma and max_nr are recomputed. Noise
only, no pass-bias terms.

Plane pass (issue #29, robust mode only; `plane`, on by default). A node on a steep bank
holds 10-16 cm of relief inside its capture disc, beyond the slope allowance, so pass 1
splits its up- and down-slope soundings into bins and the node stays red. A plane fitted
to the node's own entries cannot fix that (pass 1 already split them, and fitted to all of
them it absorbs an edge outlier on flat ground), so the gradient comes from outside: the
neighbours. Each entry carries its offset from the node (dx, dy), sz^2 and sh. For every
node red after pass 1 (clear and amber nodes are never touched):
1. A weighted least-squares plane z = a + gx*X + gy*Y through the pass-1 mains of the 8
   neighbours, each placed at its grid offset plus its main's weighted centroid (where its
   z actually sits; on a bank the members of a disc are not centred), weight its main's W,
   red neighbours times `plane_red_weight`; usable: main n_in >= `plane_min_nin`. The node
   itself is excluded. The covariance is (A'WA)^-1 scaled by max(1, chi^2).
2. Gates, in order (Surface.plane_code says which failed): at least
   `plane_min_neighbours` usable spanning 2 columns and 2 rows; at least `plane_min_clear`
   of them not red; planarity, chi = sqrt(sum w r^2 / (m - 3)) <= `plane_planarity`
   (default k_sigma); significance, |g| - allowance > k_sigma * sigma_g along g (the
   node's slope allowance, as raised by slope_from_dem); and with a raw DEM slope s
   (`raw_slope`, batch slope_from_dem only) |g| <= `plane_dem_cap` * s + `plane_dem_floor`.
3. Pass 2: every entry reduced to the node centre, z' = z - (gx*dx + gy*dy), with
   v' = sz^2 + (|g| * sh)^2 + d' C_g d (C_g the gradient covariance; the slope-allowance
   term dropped), then the same rebuild as pass 1 (Eeg included).
4. Accepted only if the node leaves red or n_out strictly drops, and
   |z' - a| <= k_sigma * sqrt(var_a + sigma'^2) (the neighbour plane's own prediction at
   the node). Otherwise pass 1 stands. One sweep: fits read pass-1 results only.
Outputs: planar (accepted), gradient |g| (where a plane passed the fit gates), plane_code.

Datum. The surface as a whole has a level uncertainty from the pass biases,
datum_sigma = sqrt(sum_p (n_p / n)^2 * sb_p^2), the same formula at survey level;
growth.difference adds it to the uncertainty of the mean growth.

Detection floor: with sb = 3 cm the test cannot see a second surface closer than about
k * sqrt(2) * sb = 13 cm; the report states sb. On streamed input (4 Hz epochs) every
epoch is an independent sounding here, so a pass contributes many entries and wins a
crossing by its weight; average to about 1 s before reading n_out as a capture count.

The surface does not depend on the order of the soundings. `robust=False` keeps the
0.7-era running mean (every sounding fused in time order, a k-sigma test that only
counts; n_out then holds that count) for comparing old runs.

The Ice Cube Logger mirrors the rebuild (one pass, sb = 0, count promotion, at most CAP
stored entries per node, see `NodeStore`) in logger/.../core/Coverage.java for its live
coverage overlay; when it changes, regenerate its test vectors with `python3 -m
icecube.logger_vectors --out logger/app/src/test/resources/coverage_vectors.json` and
port the change.
"""
from __future__ import annotations

import math
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


#: Stored entries per node in the logger's store (and `NodeStore`); past it entries fold.
CAP = 32
#: Red while n_in < OUTVOTE * n_out (the logger's colour rule).
OUTVOTE = 2
#: Eeg's test: the denominator's floor, metres (the heights' 1 mm quantisation).
EEG_FLOOR = 0.001
#: Eeg's test runs on mains with at least this many members (CUBE's median_length).
EEG_MIN_N = 11


class Hyp:
    """One hypothesis of a rebuild: W, WZ, member count, per-pass weights (pass -> [W_p, sb_p^2])."""
    __slots__ = ("w", "wz", "cnt", "pw", "members")

    def __init__(self):
        self.w = 0.0
        self.wz = 0.0
        self.cnt = 0
        self.pw = {}
        self.members = []

    def join(self, k, e):
        z, v, w, _id, cnt, pk, sb2 = e[:7]
        self.w += w
        self.wz += w * z
        self.cnt += cnt
        self.members.append(k)
        old = self.pw.get(pk)
        self.pw[pk] = [w + (old[0] if old else 0.0), sb2]

    def z(self):
        return self.wz / self.w

    def nr(self, e):
        """Normalised difference of entry e from this hypothesis (the k-sigma test statistic)."""
        z, v, w, _id, cnt, pk, sb2 = e[:7]
        wp_same = self.pw[pk][0] if pk in self.pw else 0.0
        # bias part of the difference, written so it cannot go negative (0.0 without pass biases)
        vbd = (1.0 - wp_same / self.w) ** 2 * sb2 + sum(
            (wq / self.w) ** 2 * s2 for q, (wq, s2) in self.pw.items() if q != pk)
        return abs(z - self.wz / self.w) / math.sqrt(1.0 / self.w + v + vbd)

    def var(self):
        """1/W plus the bias part sum_p (W_p/W)^2 sb_p^2."""
        return 1.0 / self.w + sum((wp / self.w) ** 2 * s2 for wp, s2 in self.pw.values())


def entry(z, v, id, pk=0, sb2=0.0, w=None, cnt=1, dx=0.0, dy=0.0, sz2=None, sh=0.0):
    """An entry tuple (z, v, w, id, cnt, pass key, sb^2, dx, dy, sz^2, sh); w defaults to
    1.0 / v. dx, dy: sounding minus node centre (m); sz2: the slope-free variance (defaults
    to v); sh: the sounding's horizontal sigma. The last four are read only by the plane
    pass (`plane_pass`); the rebuild reads the first seven."""
    return (z, v, 1.0 / v if w is None else w, id, cnt, pk, sb2, dx, dy, v if sz2 is None else sz2, sh)


class Rebuilt:
    """The result of `rebuild`: hypotheses (creation order), the main's index, each
    entry's hypothesis, and the outputs."""
    __slots__ = ("hyps", "main", "hyp_of", "zm")

    def __init__(self, hyps, main, hyp_of, zm):
        self.hyps, self.main, self.hyp_of, self.zm = hyps, main, hyp_of, zm

    @property
    def z(self):
        return self.hyps[self.main].z()

    @property
    def n_in(self):
        return self.hyps[self.main].cnt

    def alt(self):
        """(n_alt, z_alt) of the largest side bin by count (ties: earlier created), or (0, nan)."""
        best = None
        for h, hy in enumerate(self.hyps):
            if h != self.main and (best is None or hy.cnt > self.hyps[best].cnt):
                best = h
        return (0, float("nan")) if best is None else (self.hyps[best].cnt, self.hyps[best].z())

    def max_nr(self, entries):
        m = self.hyps[self.main]
        out = 0.0
        for e in entries:
            r = m.nr(e)
            if r > out:
                out = r
        return out


def rebuild(entries, k_sigma=3.0, promote="count", trace=None):
    """The median-first rebuild of one node (module docstring, Rebuild). `entries` are
    tuples from `entry`. `trace`, when a list, receives ("nr", value) for every test and
    ("half", 2*cum - W, W) at the median, for the vector generator's threshold guard."""
    n = len(entries)
    srt = sorted(range(n), key=lambda i: (entries[i][0], entries[i][3]))
    W = 0.0
    for i in srt:
        W += entries[i][2]
    cum = 0.0
    zm = entries[srt[-1]][0]
    for i in srt:
        cum += entries[i][2]
        if 2.0 * cum >= W:
            zm = entries[i][0]
            if trace is not None:
                trace.append(("half", 2.0 * cum - W, W))
            break
    order = sorted(range(n), key=lambda i: (abs(entries[i][0] - zm), entries[i][3]))
    hyps = []
    hyp_of = [0] * n
    for i in order:
        e = entries[i]
        if not hyps:
            hyps.append(Hyp()); hyps[0].join(i, e); hyp_of[i] = 0
            continue
        r = hyps[0].nr(e)
        if trace is not None:
            trace.append(("nr", r))
        if r <= k_sigma:
            hyps[0].join(i, e); hyp_of[i] = 0
            continue
        best, best_r = -1, 0.0
        for h in range(1, len(hyps)):
            r = hyps[h].nr(e)
            if trace is not None:
                trace.append(("nr", r))
            if r <= k_sigma and (best < 0 or r < best_r):
                best, best_r = h, r
        if best < 0:
            hyps.append(Hyp()); best = len(hyps) - 1
        hyps[best].join(i, e); hyp_of[i] = best
    main = 0
    if len(hyps) > 1:
        key = (lambda h: h.cnt) if promote == "count" else (lambda h: h.w)
        side = 1
        for h in range(2, len(hyps)):
            if key(hyps[h]) > key(hyps[side]):
                side = h
        if key(hyps[side]) > key(hyps[0]):
            main = side
    return Rebuilt(hyps, main, hyp_of, zm)


def strength(n_in, n_out):
    """max(0, 5 - n_in / n_out), 0 when there are no outliers."""
    return 0.0 if n_out == 0 else max(0.0, 5.0 - n_in / n_out)


class NodeStore:
    """One node as the logger keeps it (Coverage.java): at most `cap` stored entries in
    arrival order plus one aggregate, rebuilt (count promotion, one pass, no bias) on
    every add. When an add makes cap + 1 entries: rebuild; if the main has stored members,
    the oldest of them is folded into the aggregate (W += w, WZ += w*z, count += 1), after
    first dropping the aggregate (its count to n_dropped) when it is not in the main;
    otherwise the oldest stored entry is dropped and counted in n_dropped. Then rebuild
    again. The aggregate enters the rebuild as one entry z = WZ/W, w = W, v = 1.0/W,
    id -1, counting `count` members. n_dropped is part of n_out."""

    def __init__(self, k_sigma=3.0, cap=CAP):
        self.k_sigma, self.cap = k_sigma, cap
        self.entries = []
        self.agg_w = 0.0
        self.agg_wz = 0.0
        self.agg_n = 0
        self.n_dropped = 0
        self.state = None

    def _all(self):
        es = list(self.entries)
        if self.agg_n:
            es.append(entry(self.agg_wz / self.agg_w, 1.0 / self.agg_w, -1, w=self.agg_w, cnt=self.agg_n))
        return es

    def rebuild(self, trace=None):
        return rebuild(self._all(), self.k_sigma, "count", trace)

    def add(self, z, v, id, trace=None):
        self.entries.append(entry(z, v, id))
        if len(self.entries) > self.cap:
            r = self.rebuild(trace)
            m = len(self.entries)
            members = [i for i in range(m) if r.hyp_of[i] == r.main]
            if members:
                if self.agg_n and r.hyp_of[m] != r.main:
                    self.n_dropped += self.agg_n
                    self.agg_w, self.agg_wz, self.agg_n = 0.0, 0.0, 0
                e = self.entries.pop(members[0])
                self.agg_w += e[2]
                self.agg_wz += e[2] * e[0]
                self.agg_n += 1
            else:
                self.entries.pop(0)
                self.n_dropped += 1
        self.state = self.rebuild(trace)
        return self.state

    @property
    def n(self):
        return len(self.entries) + self.agg_n + self.n_dropped

    def outputs(self):
        """dict(z, sigma, n, n_in, n_out, n_alt, z_alt, strength, max_nr, w) of the last add."""
        r = self.state
        h = r.hyps[r.main]
        n_alt, z_alt = r.alt()
        n_in = h.cnt
        n_out = self.n - n_in
        return dict(z=h.z(), sigma=math.sqrt(1.0 / h.w), w=h.w, n=self.n, n_in=n_in, n_out=n_out,
                    n_alt=n_alt, z_alt=z_alt, strength=strength(n_in, n_out), max_nr=r.max_nr(self._all()))


#: `plane_pass` outcome per node (Surface.plane_code).
PLANE_NOT_TRIED = 0    # plane off, robust off, or the node not red after pass 1
PLANE_ACCEPTED = 1     # pass 2 replaced pass 1
PLANE_FEW = 2          # fewer than min_neighbours usable neighbours, or < 2 distinct dx or dy
PLANE_FEW_CLEAR = 3    # fewer than min_clear non-red usable neighbours
PLANE_NOT_PLANAR = 4   # weighted RMS residual over the planarity bound
PLANE_NOT_STEEP = 5    # |g| within the node's slope allowance
PLANE_DEM = 6          # |g| over dem_cap * DEM slope + dem_floor
PLANE_NO_GAIN = 7      # pass 2 neither leaves red nor lowers n_out
PLANE_OFF_PLANE = 8    # pass 2's z disagrees with the neighbour plane at the node

#: Plane-pass thresholds (grid_surface's plane_* arguments without the prefix). planarity
#: None means k_sigma.
PLANE_DEFAULTS = dict(min_neighbours=5, min_nin=2, min_clear=2, red_weight=0.25, planarity=None,
                      dem_cap=2.0, dem_floor=0.05)


def is_red(n_in, n_out):
    """The colour rule: outliers not outvoted OUTVOTE:1."""
    return n_out > 0 and n_in < OUTVOTE * n_out


def plane_fit(nbrs, min_neighbours, min_clear, red_weight, planarity):
    """Weighted least-squares plane z = a + gx*X + gy*Y through neighbour mains.
    `nbrs`: (X, Y, z, W, red, ox, oy) per usable neighbour: (X, Y) where its z sits
    relative to the node (its grid offset plus its main's weighted centroid, m), W the
    main's weight, (ox, oy) its grid offset (the span test). Red ones are weighted by `red_weight`. Returns (code, fit) with code a PLANE_*
    gate code (0 when the fit passes the count, span and planarity gates) and fit a dict
    a, var_a, gx, gy, cov_g (2x2), chi (the weighted RMS residual per degree of freedom,
    in neighbour sigmas; on PLANE_NOT_PLANAR the dict holds chi alone). The covariance is scaled by max(1, chi^2)."""
    use = [(X, Y, z, W * (red_weight if red else 1.0), red, ox, oy) for X, Y, z, W, red, ox, oy in nbrs]
    use = [u for u in use if u[3] > 0]
    if (len(use) < max(min_neighbours, 4) or len({round(u[5], 9) for u in use}) < 2
            or len({round(u[6], 9) for u in use}) < 2):
        return PLANE_FEW, None
    if sum(1 for u in use if not u[4]) < min_clear:
        return PLANE_FEW_CLEAR, None
    A = np.array([[1.0, u[0], u[1]] for u in use])
    z = np.array([u[2] for u in use])
    w = np.array([u[3] for u in use])
    N = A.T @ (w[:, None] * A)
    try:
        Ninv = np.linalg.inv(N)
    except np.linalg.LinAlgError:
        return PLANE_FEW, None
    beta = Ninv @ (A.T @ (w * z))
    r = z - A @ beta
    chi = math.sqrt(float(np.sum(w * r * r)) / (len(use) - 3))
    if chi > planarity:
        return PLANE_NOT_PLANAR, dict(chi=chi)
    cov = Ninv * max(1.0, chi * chi)
    return 0, dict(a=float(beta[0]), gx=float(beta[1]), gy=float(beta[2]), var_a=float(cov[0, 0]),
                   cov_g=cov[1:, 1:], chi=chi)


def reduce_to_plane(es, gx, gy, cov_g):
    """Pass-2 entries: each reduced to the node centre along the gradient,
    z' = z - (gx*dx + gy*dy), with variance v' = sz2 + (|g|*sh)^2 + d^T C_g d (C_g the
    gradient covariance, d = (dx, dy)); the slope-allowance term is dropped."""
    g = math.hypot(gx, gy)
    out = []
    for e in es:
        z, v, w, id, cnt, pk, sb2, dx, dy, sz2, sh = e
        vg = dx * dx * cov_g[0, 0] + 2.0 * dx * dy * cov_g[0, 1] + dy * dy * cov_g[1, 1]
        out.append(entry(z - gx * dx - gy * dy, sz2 + (g * sh) ** 2 + float(vg), id, pk, sb2, cnt=cnt,
                         dx=dx, dy=dy, sz2=sz2, sh=sh))
    return out


def plane_pass(stores, out1, nx, ny, cell, k_sigma, run, allowance, raw_slope=None, trace=None, **prm):
    """The neighbourhood-gradient pass (issue #29) over pass-1 results.

    `stores`: node index (a*nx + b) -> entries; `out1`: node index -> pass-1 output dict
    (z, sigma, w, n_out, ...) as returned by `run`; `run(entries)` rebuilds one node;
    `allowance(a, b)` the node's slope allowance; `raw_slope(a, b)` the raw DEM slope or
    None (no DEM gate). `prm`: PLANE_DEFAULTS keys. Only nodes red after pass 1 are tried;
    each fit uses the pass-1 mains of the 8 neighbours (one sweep, nothing cleared here
    feeds a later fit). Returns node index -> (code, pass-2 output or None, |g| or NaN)
    for every tried node. `trace`, when a list, receives (name, value, threshold) for each
    continuous gate comparison (the vector generator's threshold guard)."""
    p = dict(PLANE_DEFAULTS, **{k: v for k, v in prm.items() if v is not None})
    planarity = k_sigma if p["planarity"] is None else float(p["planarity"])
    red = {i: is_red(len(stores[i]) - o["n_out"], o["n_out"]) for i, o in out1.items()}
    res = {}
    for idx, o in out1.items():
        if not red[idx]:
            continue
        a, b = divmod(idx, nx)
        nbrs = []
        for da in (-1, 0, 1):
            for db in (-1, 0, 1):
                aa, bb = a + da, b + db
                if (da == 0 and db == 0) or not (0 <= aa < ny and 0 <= bb < nx):
                    continue
                j = aa * nx + bb
                if j not in out1 or len(stores[j]) - out1[j]["n_out"] < p["min_nin"]:
                    continue
                o_j = out1[j]
                nbrs.append((db * cell + o_j.get("cx", 0.0), da * cell + o_j.get("cy", 0.0), o_j["z"], o_j["w"],
                             red[j], db * cell, da * cell))
        code, fit = plane_fit(nbrs, p["min_neighbours"], p["min_clear"], p["red_weight"], planarity)
        if trace is not None and fit is not None:
            trace.append(("planarity", fit["chi"], planarity))
        if code:
            res[idx] = (code, None, float("nan"))
            continue
        g = math.hypot(fit["gx"], fit["gy"])
        ux, uy = (fit["gx"] / g, fit["gy"] / g) if g > 0 else (1.0, 0.0)
        cg = fit["cov_g"]
        sg = math.sqrt(max(ux * ux * cg[0, 0] + 2.0 * ux * uy * cg[0, 1] + uy * uy * cg[1, 1], 0.0))
        if trace is not None:
            trace.append(("significance", g - allowance(a, b), k_sigma * sg))
        if g - allowance(a, b) <= k_sigma * sg:
            res[idx] = (PLANE_NOT_STEEP, None, g)
            continue
        if raw_slope is not None:
            s = raw_slope(a, b)
            s = 0.0 if not np.isfinite(s) else float(s)
            if trace is not None:
                trace.append(("dem", g, p["dem_cap"] * s + p["dem_floor"]))
            if g > p["dem_cap"] * s + p["dem_floor"]:
                res[idx] = (PLANE_DEM, None, g)
                continue
        es2 = reduce_to_plane(stores[idx], fit["gx"], fit["gy"], fit["cov_g"])
        o2 = run(es2)
        if is_red(len(es2) - o2["n_out"], o2["n_out"]) and not o2["n_out"] < o["n_out"]:
            res[idx] = (PLANE_NO_GAIN, None, g)
            continue
        if trace is not None:
            trace.append(("agreement", abs(o2["z"] - fit["a"]), k_sigma * math.sqrt(fit["var_a"] + o2["sigma"] ** 2)))
        if abs(o2["z"] - fit["a"]) > k_sigma * math.sqrt(fit["var_a"] + o2["sigma"] ** 2):
            res[idx] = (PLANE_OFF_PLANE, None, g)
            continue
        res[idx] = (PLANE_ACCEPTED, o2, g)
    return res


def eeg_cull(entries, members, limit):
    """Eeg's leave-one-out test (module docstring) on `members` (indices into entries).
    Returns the culled indices."""
    N = len(members)
    if limit <= 0 or N < EEG_MIN_N:
        return []
    z = np.array([entries[i][0] for i in members])
    v = np.array([entries[i][1] for i in members])
    w = np.array([entries[i][2] for i in members])
    c = float(np.sum(w * z) / np.sum(w))
    zc = z - c                       # centred, to keep the sums of squares well conditioned
    Wt, S1, S2 = np.sum(w), np.sum(w * zc), np.sum(w * zc * zc)
    Wk = Wt - w
    mk = (S1 - w * zc) / Wk
    d = zc - mk
    ssd = np.maximum((S2 - w * zc * zc) - Wk * mk * mk, 0.0)    # sum_{i != k} w_i (z_i - m_k)^2
    q = d * d / np.maximum(ssd / (N - 2) * (v + 1.0 / Wk), EEG_FLOOR ** 2)
    return [members[j] for j in np.where(q >= limit)[0]]


@dataclass
class Surface:
    grid: Grid
    z: np.ndarray        # (ny, nx), NaN where no sounding
    sigma: np.ndarray
    n: np.ndarray        # soundings per node
    n_out: np.ndarray    # outliers per node: side-bin members and Eeg culls (robust=False: the running flag count)
    last_t: np.ndarray   # time of the last sounding (NaN when untimed)
    npass: np.ndarray    # passes contributing per node (all soundings)
    maxnr: np.ndarray = None   # largest normalised difference of a sounding from the node's final estimate
    n_alt: np.ndarray = None   # members of the largest side bin (an outlier bin, not a surface)
    z_alt: np.ndarray = None   # mean of that bin (NaN when none)
    strength: np.ndarray = None  # max(0, 5 - n_in / n_out), 0 with no outliers
    n_eeg: np.ndarray = None   # soundings culled by Eeg's test
    planar: np.ndarray = None  # True where the plane pass replaced pass 1 (issue #29)
    gradient: np.ndarray = None  # |g| of the neighbour plane where one was fitted (m/m), NaN elsewhere
    plane_code: np.ndarray = None  # PLANE_* outcome per node (0: not tried)
    datum_sigma: float = 0.0   # survey-level bias sigma, sqrt(sum_p (n_p/n)^2 sb_p^2)
    n_outside: int = 0         # soundings that fed no node (outside the grid)
    nodes_per_sounding: float = 1.0   # mean nodes fed per sounding (neighbouring nodes share soundings)
    name: str = ""
    date: str = ""
    frame: str = ""

    @property
    def n_in(self):
        return self.n - self.n_out

    def inside_mask(self, outline):
        """True for nodes inside the (x, y) ring `outline`."""
        from .growth import point_in_polygon
        X, Y = np.meshgrid(self.grid.xs, self.grid.ys)
        return point_in_polygon(X.ravel(), Y.ravel(), outline).reshape(X.shape)


def grid_surface(surveys: list[Points], sb_list: list[np.ndarray], grid: Grid,
                 capture_radius=0.75, slope=0.05, k_sigma=3.0, name="", date="", frame="",
                 robust=True, eeg_quotient_limit=30.0, plane=True, raw_slope=None,
                 plane_min_neighbours=5, plane_min_nin=2, plane_min_clear=2, plane_red_weight=0.25,
                 plane_planarity=None, plane_dem_cap=2.0, plane_dem_floor=0.05) -> Surface:
    """Fuse the (bias-corrected) points of one or more surveys into node estimates.
    `sb_list[k]` is the per-point bias sigma of `surveys[k]` (from crossover.apply).
    `slope` is one allowance (m/m) or an array of the grid's shape (ny, nx), one per node
    (e.g. from a DEM's local slope, so steep ground gets a larger allowance).
    `capture_radius` is one value or one per survey (a sparse DEM can reach further than
    the RTK soundings). `robust` selects the median-first rebuild (default) or the 0.7-era
    running mean; `eeg_quotient_limit` (0 = off) applies Eeg's test in the robust mode.

    `plane` (robust mode only; ignored with robust=False) runs the neighbourhood-gradient
    pass on nodes red after pass 1 (module docstring, Plane pass); the `plane_*` arguments
    are its thresholds (`plane_planarity` None: k_sigma). `raw_slope`, an array of the
    grid's shape with the raw DEM slope (m/m) per node, turns on its DEM gate; None skips it."""
    frames = {s.frame for s in surveys}
    if len(frames) > 1:
        raise ValueError(f"gridding across frames refused: {sorted(frames)}")
    slope_grid = None
    if np.ndim(slope):
        slope_grid = np.asarray(slope, float)
        if slope_grid.shape != (grid.ny, grid.nx):
            raise ValueError("slope array must have the grid's shape (ny, nx)")
    if raw_slope is not None:
        raw_slope = np.asarray(raw_slope, float)
        if raw_slope.shape != (grid.ny, grid.nx):
            raise ValueError("raw_slope must have the grid's shape (ny, nx)")
    xs, ys, zs, vs, sbs, ts, pks, rrs = [], [], [], [], [], [], [], []
    pass_offset = 0
    radii = capture_radius if np.ndim(capture_radius) else [capture_radius] * len(surveys)
    if len(radii) != len(surveys):
        raise ValueError("capture_radius: one value, or one per survey")
    for s, sb, rad in zip(surveys, sb_list, radii):
        rrs.append(np.full(len(s), float(rad)))
        if len(sb) != len(s):
            raise ValueError("sb length mismatch")
        if not np.all(np.isfinite(s.sz)) or not np.all(np.isfinite(s.sh)):
            raise ValueError(f"survey {s.survey}: fill sigma defaults first")
        xs.append(s.x); ys.append(s.y); zs.append(s.z); ts.append(s.t)
        vs.append(np.c_[s.sz, s.sh]); sbs.append(sb)
        pks.append(s.pass_id + pass_offset)
        pass_offset += int(s.pass_id.max()) + 1 if len(s) else 0
    x, y, z, t, sb, pk, rr = map(np.concatenate, (xs, ys, zs, ts, sbs, pks, rrs))
    szsh = np.concatenate(vs)
    npts = len(z)
    # time order; untimed points keep file order after the timed ones of the same survey
    order = np.lexsort((np.arange(npts), np.where(np.isfinite(t), t, np.inf)))
    # contributions: for each point, nodes within capture radius
    rc = int(np.ceil(float(np.max(rr)) / grid.cell)) if len(rr) else 1
    i0 = np.round((x - grid.x0) / grid.cell).astype(int)
    j0 = np.round((y - grid.y0) / grid.cell).astype(int)
    ny, nx = grid.ny, grid.nx
    di, dj = np.meshgrid(np.arange(-rc, rc + 1), np.arange(-rc, rc + 1))
    di, dj = di.ravel(), dj.ravel()
    n_outside = 0
    n_contrib = 0
    store = {}     # node index -> list of entries, in time order
    rank = 0
    for k in order:
        ii = i0[k] + di
        jj = j0[k] + dj
        ok = (ii >= 0) & (ii < nx) & (jj >= 0) & (jj < ny)
        if not ok.any():
            n_outside += 1
            continue
        ii, jj = ii[ok], jj[ok]
        d = np.hypot(grid.x0 + ii * grid.cell - x[k], grid.y0 + jj * grid.cell - y[k])
        ok = d <= rr[k]
        if not ok.any():
            n_outside += 1
            continue
        ii, jj, d = ii[ok], jj[ok], d[ok]
        n_contrib += len(ii)
        sz, sh = szsh[k]
        sl = slope_grid[jj, ii] if slope_grid is not None else slope
        v = sz**2 + (sl * (d + sh))**2
        sb2 = float(sb[k]) ** 2
        zk, pkk, tk = float(z[k]), int(pk[k]), float(t[k])
        ddx = x[k] - (grid.x0 + ii * grid.cell)
        ddy = y[k] - (grid.y0 + jj * grid.cell)
        sz2, shf = float(sz) ** 2, float(sh)
        for a, b, vk, ex, ey in zip(jj, ii, v, ddx, ddy):
            store.setdefault(int(a) * nx + int(b), []).append(
                (entry(zk, float(vk), rank, pkk, sb2, dx=float(ex), dy=float(ey), sz2=sz2, sh=shf), tk))
        rank += 1
    base = _robust if robust else _running
    run = lambda es: base(es, k_sigma, eeg_quotient_limit)  # noqa: E731
    Z = np.full((ny, nx), np.nan)
    S = np.full((ny, nx), np.nan)
    N = np.zeros((ny, nx), int)
    OUT = np.zeros((ny, nx), int)
    ALT = np.zeros((ny, nx), int)
    ZALT = np.full((ny, nx), np.nan)
    ST = np.zeros((ny, nx))
    EEG = np.zeros((ny, nx), int)
    MR = np.zeros((ny, nx))
    LT = np.full((ny, nx), np.nan)
    NP = np.zeros((ny, nx), int)
    PL = np.zeros((ny, nx), bool)
    GR = np.full((ny, nx), np.nan)
    PC = np.zeros((ny, nx), int)
    stores = {idx: [e for e, _ in lst] for idx, lst in store.items()}
    out1 = {idx: run(es) for idx, es in stores.items()}
    if plane and robust:
        allow = (lambda a, b: float(slope_grid[a, b])) if slope_grid is not None else (lambda a, b: float(slope))
        raw = (lambda a, b: float(raw_slope[a, b])) if raw_slope is not None else None
        tried = plane_pass(stores, out1, nx, ny, grid.cell, k_sigma, run, allow, raw,
                           min_neighbours=plane_min_neighbours, min_nin=plane_min_nin,
                           min_clear=plane_min_clear, red_weight=plane_red_weight,
                           planarity=plane_planarity, dem_cap=plane_dem_cap, dem_floor=plane_dem_floor)
        for idx, (code, o2, g) in tried.items():
            a, b = divmod(idx, nx)
            PC[a, b], GR[a, b] = code, g
            if code == PLANE_ACCEPTED:
                PL[a, b] = True
                out1[idx] = o2
    for idx, lst in store.items():
        a, b = divmod(idx, nx)
        es = stores[idx]
        o = out1[idx]
        Z[a, b], S[a, b], MR[a, b] = o["z"], o["sigma"], o["max_nr"]
        N[a, b], OUT[a, b], ALT[a, b], ZALT[a, b], EEG[a, b] = len(es), o["n_out"], o["n_alt"], o["z_alt"], o["n_eeg"]
        ST[a, b] = strength(len(es) - o["n_out"], o["n_out"])
        NP[a, b] = len({e[5] for e in es})
        ts = [tt for _, tt in lst if np.isfinite(tt)]
        if ts:
            LT[a, b] = ts[-1]
    # survey-level datum sigma: the pass biases weighted by their share of the points
    ds = 0.0
    if npts:
        for pkey in np.unique(pk):
            m = pk == pkey
            ds += (m.sum() / npts) ** 2 * float(sb[m][0]) ** 2
    return Surface(grid=grid, z=Z, sigma=S, n=N, n_out=OUT, last_t=LT, npass=NP, maxnr=MR, n_alt=ALT, z_alt=ZALT,
                   strength=ST, n_eeg=EEG, planar=PL, gradient=GR, plane_code=PC, datum_sigma=float(np.sqrt(ds)), n_outside=n_outside,
                   nodes_per_sounding=float(n_contrib / max(npts - n_outside, 1)),
                   name=name, date=date, frame=frame)


def _robust(es, k_sigma, eeg_limit, promote="weight"):
    """One node, batch: rebuild once (weight promotion), then Eeg on the main."""
    r = rebuild(es, k_sigma, promote)
    main = r.hyps[r.main]
    culled = eeg_cull(es, main.members, eeg_limit)
    if culled:
        gone = set(culled)
        main = Hyp()
        for i in r.hyps[r.main].members:
            if i not in gone:
                main.join(i, es[i])
        r.hyps[r.main] = main
    n_alt, z_alt = r.alt()
    mr = 0.0
    for e in es:
        q = main.nr(e)
        if q > mr:
            mr = q
    cx, cy = _centroid(es, main)
    return dict(z=main.z(), sigma=math.sqrt(main.var()), w=main.w, cx=cx, cy=cy, max_nr=mr, n_out=len(es) - main.cnt,
                n_alt=n_alt, z_alt=z_alt, n_eeg=len(culled))


def _centroid(es, h):
    """Weighted mean (dx, dy) of hypothesis h's members: where its z sits relative to the node."""
    sx = sy = 0.0
    for i in h.members:
        e = es[i]
        sx += e[2] * e[7]
        sy += e[2] * e[8]
    return sx / h.w, sy / h.w


def _running(es, k_sigma, eeg_limit):
    """One node, the 0.7-era rule: every entry fused in time order; a k-sigma test before
    each fusion counts a flag (returned as n_out) and tracks the running maximum nr."""
    h = Hyp()
    flags, mr = 0, 0.0
    for i, e in enumerate(es):
        if h.cnt:
            q = h.nr(e)
            if q > k_sigma:
                flags += 1
            if q > mr:
                mr = q
        h.join(i, e)
    cx, cy = _centroid(es, h)
    return dict(z=h.z(), sigma=math.sqrt(h.var()), w=h.w, cx=cx, cy=cy, max_nr=mr, n_out=flags, n_alt=0, z_alt=float("nan"), n_eeg=0)
