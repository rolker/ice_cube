"""Crossover adjustment: one vertical bias per pass, from overlaps between passes.

Model. Point i in pass p has height z_i = h(x_i, y_i) + b_p + e_i, where h is the
surface, b_p is the pass bias (slowly varying RTK wander, taken as constant within a
pass) and e_i is per-point noise with sigma sz_i. Two points from different passes
closer than `radius` horizontally give the observation

    z_i - z_j = b_p - b_q + v,    var(v) = sz_i^2 + sz_j^2 + (slope * d_ij)^2

where d_ij is their horizontal separation and `slope` (m/m) allows for the surface
not being flat between them. To keep the number of observations bounded, the points of
each pass are first averaged in cells of `radius` metres (a pass-cell mean: mean x, y,
z, t, and a variance that is the smaller of "sigma of the mean" and the best single
point's variance, i.e. the points of a cell are not allowed to average down below one
point: 4 Hz epochs a quarter second apart are not independent), and pairs are formed
between pass-cells of different passes within `radius`.

Prior. Only differences b_p - b_q are observed, so without a prior the system has a
free datum and poorly connected passes could drift anywhere (that is how
analysis/survey_wander.py invented 15 cm of drift). Each bias gets an independent
zero-mean Gaussian prior with sigma `prior` (the expected wander). The weighted least
squares is then

    (A^T W A + I / prior^2) b = A^T W dz

with posterior covariance (A^T W A + I / prior^2)^-1. A pass with no overlaps gets
exactly b = 0 and posterior sigma = prior.

Per-survey priors and a common bias. Each survey may carry its own pass prior (home base
1-2 cm, another network more), and a survey whose passes share most of their offset (a day on
a distant network) can get one more unknown, a common bias c_s with its own prior, so that a
pass bias is c_s + b_p. A pass that is not separable on its own then takes c_s (if that is
separable) with sigma sqrt(sigma_c^2 + prior_p^2).

Anchors. Points on a repeat mark (a ref, a monument, a deck checkpoint: Points.anchor) are
paired only with points of the same mark in other passes, whatever the distance and without
the slope term, and never with ground points (a mark top is not the ground beside it).

"Not separable". A pass whose posterior sigma is still more than `separable_ratio`
(default 0.8) times the prior, or that has fewer than `min_pairs` overlaps, was not
usefully constrained by the data; its bias is reported as "not separable, held at
zero" and the estimate is set to 0 (rather than the weakly shrunk value) so the surface
does not carry an invented offset. The report also gives the chi-square of the
residuals per degree of freedom and the number of connected components among passes.

Outliers. Pairs with |dz| > `max_dz` (default 0.25 m: a deck edge, a step, a wrong
fix, not RTK wander) are dropped before the solve. After a first solve, pairs whose
normalised residual exceeds `reject_sigma` (default 4) are dropped and the system is
solved once more.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .io import Points


@dataclass
class CrossoverResult:
    surveys: list
    pass_keys: list                  # (survey, pass_id) in solution order
    bias: np.ndarray                 # applied bias per pass (0 where not separable)
    bias_raw: np.ndarray             # least-squares estimate (pass + its survey's common part) before the hold-at-zero
    sigma: np.ndarray                # posterior sigma per pass (of the applied bias)
    prior: float                     # run default pass prior
    separable: np.ndarray            # bool per pass
    n_obs: int
    chi2_dof: float                  # weighted residual chi-square / dof (NaN if no obs)
    rms_before: float
    rms_after: float
    components: int                  # connected components among passes (an isolated pass is one)
    n_pairs_per_pass: np.ndarray
    residuals: np.ndarray = field(default_factory=lambda: np.zeros(0))
    pair_dz: np.ndarray = field(default_factory=lambda: np.zeros(0))
    n_cut: int = 0                   # pairs dropped by max_dz
    n_rejected: int = 0              # pairs dropped by the residual test
    pass_prior: np.ndarray = field(default_factory=lambda: np.zeros(0))   # total prior sigma per pass
    common: dict = field(default_factory=dict)   # survey -> dict(bias, sigma, prior, separable, pairs)
    pair_anchor: np.ndarray = field(default_factory=lambda: np.zeros(0, bool))  # pair is an anchor (same mark) pair
    pair_sigma: np.ndarray = field(default_factory=lambda: np.zeros(0))          # sqrt(var) of each kept pair
    pair_pass: tuple = (np.zeros(0, int), np.zeros(0, int))                      # pass index of each side

    def bias_of(self, survey, pass_id):
        return self.bias[self.pass_keys.index((survey, int(pass_id)))]

    def sigma_of(self, survey, pass_id):
        return self.sigma[self.pass_keys.index((survey, int(pass_id)))]


def pass_cells(pts: Points, radius, anchors=False):
    """Average each pass's points in square cells of `radius` m (or, with anchors=True, per
    anchor id: the points of one pass on one repeat mark). Returns arrays x, y, z, t, var
    (variance of the mean, floored at the best point's), pass id, cell key, count."""
    if anchors:
        names = {a: i for i, a in enumerate(sorted(set(pts.anchor)))}
        ix = np.array([names[a] for a in pts.anchor], dtype=np.int64)
        iy = np.zeros(len(pts), dtype=np.int64)
    else:
        ix = np.floor(pts.x / radius).astype(np.int64)
        iy = np.floor(pts.y / radius).astype(np.int64)
    key = np.stack([pts.pass_id, ix, iy], axis=1)
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    w = 1.0 / pts.sz**2
    W = np.bincount(inv, w)
    best = np.full(len(uniq), np.inf)
    np.minimum.at(best, inv, pts.sz**2)
    x = np.bincount(inv, w * pts.x) / W
    y = np.bincount(inv, w * pts.y) / W
    z = np.bincount(inv, w * pts.z) / W
    tt = np.where(np.isfinite(pts.t), pts.t, 0.0)
    t = np.bincount(inv, w * tt) / W
    n = np.bincount(inv)
    return x, y, z, t, np.maximum(1.0 / W, best), uniq[:, 0], np.c_[uniq[:, 1], uniq[:, 2]], n


def _pairs_within(x, y, radius):
    """All index pairs (i < j) with horizontal distance <= radius. Grid hashing."""
    ix = np.floor(x / radius).astype(np.int64)
    iy = np.floor(y / radius).astype(np.int64)
    cells = {}
    for k, (a, b) in enumerate(zip(ix, iy)):
        cells.setdefault((a, b), []).append(k)
    out_i, out_j = [], []
    for (a, b), members in cells.items():
        for da in (-1, 0, 1):
            for db in (-1, 0, 1):
                other = cells.get((a + da, b + db))
                if other is None:
                    continue
                I = np.array(members)[:, None]
                J = np.array(other)[None, :]
                m = I < J
                ii, jj = I.repeat(len(other), 1)[m], J.repeat(len(members), 0)[m]
                d = np.hypot(x[ii] - x[jj], y[ii] - y[jj])
                keep = d <= radius
                out_i.append(ii[keep]); out_j.append(jj[keep])
    if not out_i:
        return np.zeros(0, int), np.zeros(0, int)
    return np.concatenate(out_i).astype(int), np.concatenate(out_j).astype(int)


def adjust(surveys: list[Points], radius=0.5, prior=0.03, slope=0.05,
           separable_ratio=0.8, min_pairs=3, max_dz=0.25, reject_sigma=4.0) -> CrossoverResult:
    """Solve one bias per (survey, pass) across all given surveys (they must be of the
    same surface and frame). See the module docstring for the model.

    Per survey: `Points.prior` (else the run's `prior`) is the pass prior, and a survey with
    `Points.common_prior` > 0 also gets one bias shared by all its passes (a survey on another
    network whose offset is common to the day). Anchor points (`Points.anchor` != "") pair
    only with points of the same anchor in other passes, at any distance (the same mark),
    and never with ground points."""
    frames = {s.frame for s in surveys}
    if len(frames) > 1:
        raise ValueError(f"crossover across frames refused: {sorted(frames)}")
    keys, kprior, ksurv = [], [], []
    commons = []                       # survey names with a common bias, in unknown order
    cx, cy, cz, cv, ck, ca, cn = [], [], [], [], [], [], []
    for s in surveys:
        if len(s) == 0:
            raise ValueError(f"survey {s.survey}: no points left after filtering")
        if np.any(s.pass_id < 0):
            raise ValueError(f"survey {s.survey}: run passes.segment first")
        if not np.all(np.isfinite(s.sz)):
            raise ValueError(f"survey {s.survey}: fill sigma defaults first")
        p_s = float(s.prior) if s.prior is not None else float(prior)
        if s.common_prior and s.survey not in commons:
            commons.append(s.survey)
        for pid in np.unique(s.pass_id):
            keys.append((s.survey, int(pid))); kprior.append(p_s); ksurv.append(s.survey)
        is_a = np.array([bool(a) for a in s.anchor])
        for part, anch in ((s.subset(~is_a), False), (s.subset(is_a), True)):
            if len(part) == 0:
                continue
            x, y, z, t, v, pidc, cell, _ = pass_cells(part, radius, anchors=anch)
            cx.append(x); cy.append(y); cz.append(z); cv.append(v)
            ck.append(np.array([keys.index((s.survey, int(p))) for p in pidc]))
            ca.append(np.full(len(x), anch))
            if anch:
                names = sorted(set(part.anchor))
                cn.append(np.array([names[c] for c in cell[:, 0]], dtype=object))
            else:
                cn.append(np.full(len(x), "", dtype=object))
    cx, cy, cz, cv, ck, ca, cn = map(np.concatenate, (cx, cy, cz, cv, ck, ca, cn))
    ck = ck.astype(int)
    npass = len(keys)
    kprior = np.array(kprior)
    # ground pairs within radius; anchor pairs on the same mark
    i, j = _pairs_within(cx, cy, radius)
    m = (ck[i] != ck[j]) & ~ca[i] & ~ca[j]
    i, j = i[m], j[m]
    ai, aj = [], []
    an_idx = np.where(ca)[0]
    by_name = {}
    for k in an_idx:
        by_name.setdefault(cn[k], []).append(k)
    for members in by_name.values():
        for u in range(len(members)):
            for w_ in range(u + 1, len(members)):
                a_, b_ = members[u], members[w_]
                if ck[a_] != ck[b_]:
                    ai.append(a_); aj.append(b_)
    i = np.r_[i, np.array(ai, int)]; j = np.r_[j, np.array(aj, int)]
    is_anchor_pair = np.r_[np.zeros(len(i) - len(ai), bool), np.ones(len(ai), bool)]
    d = np.hypot(cx[i] - cx[j], cy[i] - cy[j])
    dz = cz[i] - cz[j]
    var = cv[i] + cv[j] + np.where(is_anchor_pair, 0.0, (slope * d) ** 2)
    keep = np.abs(dz) <= max_dz
    n_cut = int((~keep).sum())
    pi, pj, dz, var, pa = ck[i][keep], ck[j][keep], dz[keep], var[keep], is_anchor_pair[keep]

    # unknowns: the pass biases, then the survey commons
    ncom = len(commons)
    nun = npass + ncom
    com_of = np.array([commons.index(sv) if sv in commons else -1 for sv in ksurv], int)
    com_prior = np.array([next(float(s.common_prior) for s in surveys if s.survey == c) for c in commons])
    pri = np.r_[kprior, com_prior]
    M = np.zeros((npass, nun))         # pass total = b_p + c_s
    M[np.arange(npass), np.arange(npass)] = 1.0
    for k in range(npass):
        if com_of[k] >= 0:
            M[k, npass + com_of[k]] = 1.0

    def solve(pi, pj, dz, w):
        A = M[pi] - M[pj]
        N = A.T @ (A * w[:, None]) + np.diag(1.0 / pri**2)
        u = A.T @ (w * dz)
        cov = np.linalg.inv(N)
        return cov @ u, cov

    w = 1.0 / var
    sol, cov = solve(pi, pj, dz, w)
    tot = M @ sol
    r = dz - (tot[pi] - tot[pj])
    ok = np.abs(r) <= reject_sigma * np.sqrt(var)
    n_rej = int((~ok).sum())
    if n_rej:
        pi, pj, dz, var, w, pa = pi[ok], pj[ok], dz[ok], var[ok], w[ok], pa[ok]
        sol, cov = solve(pi, pj, dz, w)
        tot = M @ sol
    tcov = M @ cov @ M.T
    sig_tot = np.sqrt(np.diag(tcov))
    npp = np.bincount(np.r_[pi, pj], minlength=npass)
    prior_tot = np.sqrt(kprior**2 + np.where(com_of >= 0, com_prior[np.maximum(com_of, 0)] if ncom else 0.0, 0.0) ** 2)
    sep_pass = (sig_tot < separable_ratio * prior_tot) & (npp >= min_pairs)
    common = {}
    for c, name in enumerate(commons):
        members = com_of == c
        sc = float(np.sqrt(cov[npass + c, npass + c]))
        pairs = int(npp[members].sum())
        common[name] = dict(bias=float(sol[npass + c]), sigma=sc, prior=float(com_prior[c]), pairs=pairs,
                            separable=bool(sc < separable_ratio * com_prior[c] and pairs >= min_pairs))
    b = np.zeros(npass)
    sig = prior_tot.copy()
    for k in range(npass):
        if sep_pass[k]:
            b[k], sig[k] = tot[k], sig_tot[k]
        elif com_of[k] >= 0 and common[commons[com_of[k]]]["separable"]:
            cc = common[commons[com_of[k]]]
            b[k], sig[k] = cc["bias"], float(np.hypot(cc["sigma"], kprior[k]))
    separable = sep_pass
    r = dz - (b[pi] - b[pj])
    dof = max(len(dz) - int(separable.sum()), 1)
    chi2 = float(np.sum(w * r**2) / dof) if len(dz) else float("nan")
    rms = lambda v: float(np.sqrt(np.mean(v**2))) if len(v) else float("nan")  # noqa: E731
    comps = _components(npass, pi, pj)
    return CrossoverResult(surveys=[s.survey for s in surveys], pass_keys=keys, bias=b, bias_raw=tot,
                           sigma=sig, prior=prior, separable=separable, n_obs=len(dz), chi2_dof=chi2,
                           rms_before=rms(dz), rms_after=rms(r), components=comps,
                           n_pairs_per_pass=npp, residuals=r, pair_dz=dz, n_cut=n_cut, n_rejected=n_rej,
                           pass_prior=prior_tot, common=common, pair_anchor=pa, pair_sigma=np.sqrt(var),
                           pair_pass=(pi, pj))


def _components(n, i, j):
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a, b in zip(i.tolist(), j.tolist()):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return len({find(a) for a in range(n)})


def apply(pts: Points, res: CrossoverResult) -> Points:
    """Subtract the solved pass bias from each point and record the bias uncertainty
    (sb) per point for the gridder. Returns a new Points plus the sb array."""
    b = np.zeros(len(pts))
    sb = np.zeros(len(pts))
    for pid in np.unique(pts.pass_id):
        m = pts.pass_id == pid
        b[m] = res.bias_of(pts.survey, pid)
        sb[m] = res.sigma_of(pts.survey, pid)
    out = pts.replace(z=pts.z - b)
    return out, sb
