package rink.icecube.logger.core;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * The live coverage estimator (logger 0.7, issue #6; 0.8, #24; 0.10, #29): a port of icecube's
 * median-first node rebuild ({@code icecube/nodes.py}, {@code rebuild} and {@code NodeStore})
 * over a sparse grid, fed one surface capture at a time, plus the area rule and the status
 * the map draws.
 *
 * Grid. Node (i, j) sits at (i * cell, j * cell) in the map frame (metres east, north of
 * the map origin), so a node keeps its place as the walked area grows. Nodes are created
 * on demand, up to MAX_NODES.
 *
 * Entries. A sounding z with vertical sigma sz and horizontal sigma sh feeds every node
 * within the capture radius, at distance d, as an entry (z, v, w, id) with
 * v = sz^2 + (SLOPE * (d + sh))^2, w = 1.0 / v and id the capture number (unique per node).
 * A node stores at most CAP entries in arrival order plus one aggregate, in primitive
 * arrays allocated on its first feed (fringe nodes allocate nothing).
 *
 * Rebuild (on every feed, nodes.py's definition, one pass, no pass bias). Sort by (z, id);
 * W = sum of w in that order; the weighted median zm is the z of the first entry with
 * 2*cum >= W. Feed in order of (|z - zm|, id): the first starts the main hypothesis; each
 * next one joins the main when |z - WZ/W| / sqrt(1/W + v) <= K_SIGMA, else the side
 * hypothesis with the smallest such statistic (ties: the earlier created), else a new side.
 * Then the side with the most members (ties: the earlier created) becomes the main when it
 * has strictly more than the main. Side hypotheses are outlier bins, not surfaces.
 *
 * Fold. When a feed makes CAP + 1 stored entries: rebuild; if the main has stored members,
 * the oldest of them goes into the aggregate (W, WZ, count), after first dropping the
 * aggregate when it is not in the main; otherwise the oldest stored entry is dropped. A
 * dropped entry stays counted as an outlier. The aggregate enters the rebuild as one entry
 * z = WZ/W, w = W, v = 1.0/W, id -1, counting its members. Then rebuild again.
 *
 * Geometry (0.10.0, issue #29). Each stored entry also keeps dx, dy (capture minus node
 * centre), sz^2 and sh; the aggregate keeps WDX = sum w dx, WDY = sum w dy, WS = sum w^2 sz^2
 * and WH = sum w^2 sh^2 (zeroed when it is dropped) and enters the plane pass at
 * (WDX/W, WDY/W) with sz^2 = WS/W^2, sh = sqrt(WH)/W. Pass 1 also keeps the main's weighted
 * centroid (cx, cy), the aggregate counted at (WDX/W, WDY/W) with weight W.
 *
 * Plane pass (nodes.py's plane_pass, with the constant SLOPE allowance and no DEM gate). A
 * node red after pass 1 is tried: a plane through its 8 neighbours' pass-1 mains
 * ({@link PlanePass#fit}; usable: main nIn >= MIN_NIN), the gradient significant beyond SLOPE,
 * then pass 2: every entry reduced to the node centre along the gradient,
 * z' = z - (gx*dx + gy*dy), v' = sz^2 + (|g|*sh)^2 + d'C_g d, rebuilt as pass 1, sigma'^2 =
 * 1/W' + d_bar'C_g d_bar. Accepted when the node leaves red or nOut drops, and z' agrees with
 * the plane at the node; then the final values (zFinal, sigmaFinal, nInFinal, nOutFinal) are
 * pass 2's, else pass 1's. A node's outcome depends only on its own pass-1 state and its 8
 * neighbours', so after each capture the pass is redone for the fed nodes and every node
 * within one cell of them (the ring, about 25 nodes at the defaults, up to 26), each from its
 * neighbours' pass-1 state (one sweep: a neighbour cleared by its plane stays red for the
 * others' fits). That equals a recompute over the whole grid (CoverageTest, and the vectors'
 * plane_sequences come from one).
 *
 * Area. Each sounding also marks every node within `reach` as in the surveyed area, and
 * sets the node's quadrant bit (capture minus node: x >= 0 bit 0, y >= 0 bit 1).
 *
 * Status (from the final values). NONE outside the area. With no sounding: NO_DATA (red)
 * when all four quadrants are marked (an enclosed gap), else FRINGE (the area's edge, drawn
 * neutral). DISAGREE (red) while nOutFinal > 0 and nInFinal < OUTVOTE * nOutFinal; else THIN
 * (amber) when sqrt(sigmaFinal^2 + wander^2) is above the target, else OK. A node the plane
 * pass cleared draws as OK or THIN like any other. The wander term is the pass wander the
 * live view cannot solve (icecube's crossover adjustment does), added only here. CoverageTest
 * checks all of it against vectors from {@code python3 -m icecube.logger_vectors}.
 */
public final class Coverage {
    /** nodes.py defaults (icecube.example.toml): slope allowance m/m and the k-sigma test. */
    public static final double SLOPE = 0.05, K_SIGMA = 3.0;
    /** Stored entries per node (nodes.CAP) and the outvote factor of the colour rule (nodes.OUTVOTE). */
    public static final int CAP = 32, OUTVOTE = 2;
    /** About 6 ha at 0.5 m spacing. Past it no new node is created; existing ones still update. */
    public static final int MAX_NODES = 250_000;

    public enum Status { NONE, NO_DATA, FRINGE, DISAGREE, THIN, OK }

    /** The Settings > Coverage values, in metres. */
    public static final class Params {
        public final double cell, captureRadius, reach, target, wander, ground;

        public Params(double cell, double captureRadius, double reach, double target, double wander, double ground) {
            this.cell = cell; this.captureRadius = captureRadius; this.reach = reach; this.target = target;
            this.wander = wander; this.ground = ground;
        }

        /** The defaults: 0.5 m grid, 0.75 m radius, 2 m reach, 3 cm target, 1.5 cm wander, 1.5 cm ground. */
        public static Params defaults() { return new Params(0.5, 0.75, 2.0, 0.03, 0.015, 0.015); }

        @Override public boolean equals(Object o) {
            if (!(o instanceof Params)) return false;
            Params p = (Params) o;
            return cell == p.cell && captureRadius == p.captureRadius && reach == p.reach && target == p.target
                    && wander == p.wander && ground == p.ground;
        }

        @Override public int hashCode() { return toString().hashCode(); }

        @Override public String toString() {
            return String.format(Locale.US, "cell %s radius %s reach %s target %s wander %s ground %s", cell,
                    captureRadius, reach, target, wander, ground);
        }

        /**
         * The events.csv line for a rebuild with these settings in `frame` (origin values, and
         * `originNote` saying where the origin came from), so a session can be replayed exactly.
         * The app always runs the plane pass (plane=1) with PlanePass's thresholds.
         */
        public String eventText(LocalFrame frame, String originNote) {
            String o = frame == null ? "origin=none"
                    : String.format(Locale.US, "origin_lat=%.9f origin_lon=%.9f origin_h=%.4f", frame.lat0, frame.lon0,
                    frame.h0);
            return String.format(Locale.US, "coverage cell=%s radius=%s reach=%s target=%s wander=%s ground=%s "
                    + "slope=%s k_sigma=%s cap=%d outvote=%d plane=1 plane_min_neighbours=%d plane_min_nin=%d "
                    + "plane_min_clear=%d plane_red_weight=%s plane_planarity=%s plane_max_sigma_g=%s %s "
                    + "origin_source=\"%s\"", cell, captureRadius, reach,
                    target, wander, ground, SLOPE, K_SIGMA, CAP, OUTVOTE, PlanePass.MIN_NEIGHBOURS, PlanePass.MIN_NIN,
                    PlanePass.MIN_CLEAR, PlanePass.RED_WEIGHT, PlanePass.PLANARITY, PlanePass.MAX_SIGMA_G, o,
                    originNote == null ? "" : originNote.replace('"', '\''));
        }
    }

    /** One grid node. */
    public static final class Node {
        public final int i, j;
        public boolean inArea;
        /** Quadrant bits of the captures that marked it (see Area). */
        public int quad;
        /** Contributions (stored, folded and dropped), and pass 1's main members, outliers, largest side bin. */
        public int n, nIn, nOut, nAlt;
        /** Sum of weights 1/v and of w*z of the main hypothesis. */
        public double w, wz;
        /** Mean of the largest side bin (NaN when none). */
        public double zAlt = Double.NaN;
        /** Largest normalised difference of any entry from the main, at the last rebuild. */
        public double maxNr;
        /** Pass 1's main: weighted centroid (dx, dy) of its members relative to the node, metres. */
        public double cx, cy;
        /** The plane pass: accepted, its PlanePass code, and |g| (NaN before the significance gate). */
        public boolean planar;
        public int planeCode;
        public double gradient = Double.NaN;
        /** Final values: pass 2's when planar, else pass 1's (sigmaFinal includes d_bar'C_g d_bar). */
        public double zFinal = Double.NaN, wFinal, sigmaFinal = Double.NaN;
        public int nInFinal, nOutFinal;
        /** 1/W' + d_bar'C_g d_bar when planar, else 1/W: the variance compared with the target. */
        private double varFinal;
        private boolean touched;
        private int ringMark = -1;
        // the store: entries in arrival order (z, v, w, id, dx, dy, sz^2, sh), the aggregate, the dropped count
        private double[] ez, ev, ew, edx, edy, esz2, esh;
        private int[] eid;
        private int m;
        private double aggW, aggWz, aggWdx, aggWdy, aggWs, aggWh;
        private int aggN, nDropped;

        Node(int i, int j) { this.i = i; this.j = j; }

        public double z() { return n == 0 ? Double.NaN : wz / w; }

        /** sqrt(1/W) of the main: the fused sigma without the pass wander (nodes.py's sigma with sb = 0). */
        public double sigma() { return n == 0 ? Double.NaN : Math.sqrt(1.0 / w); }

        /** max(0, 5 - nIn / nOut), 0 with no outliers (CUBE-style; every side member counts). */
        public double strength() { return nOut == 0 ? 0.0 : Math.max(0.0, 5.0 - (double) nIn / nOut); }

        /** Entries stored now (at most CAP), the aggregate's count and the dropped count. */
        public int stored() { return m; }

        public int folded() { return aggN; }

        public int dropped() { return nDropped; }
    }

    public final Params params;
    /** Whether the plane pass runs (always in the app; off in tests that pin pass-1 status). */
    public final boolean plane;
    private final Map<Long, Node> nodes = new HashMap<>();
    private final List<Node> touched = new ArrayList<>();
    private boolean full;
    private int fed;
    /** Bounding box of the in-area nodes (min > max while there are none). */
    public int minI = Integer.MAX_VALUE, maxI = Integer.MIN_VALUE, minJ = Integer.MAX_VALUE, maxJ = Integer.MIN_VALUE;

    /** With the plane pass. */
    public Coverage(Params p) { this(p, true); }

    public Coverage(Params p, boolean plane) { params = p; this.plane = plane; }

    static long key(int i, int j) { return ((long) i << 32) | (j & 0xFFFFFFFFL); }

    public Node node(int i, int j) { return nodes.get(key(i, j)); }

    public Collection<Node> nodes() { return nodes.values(); }

    public int size() { return nodes.size(); }

    /** True once MAX_NODES stopped the grid from growing. */
    public boolean full() { return full; }

    /** Soundings fed so far. */
    public int fed() { return fed; }

    private Node getOrCreate(int i, int j) {
        long k = key(i, j);
        Node nd = nodes.get(k);
        if (nd == null) {
            if (nodes.size() >= MAX_NODES) { full = true; return null; }
            nd = new Node(i, j);
            nodes.put(k, nd);
        }
        return nd;
    }

    private void touch(Node nd) {
        if (!nd.touched) { nd.touched = true; touched.add(nd); }
    }

    /**
     * Nodes changed since the last call (for repainting only those): fed, newly in the
     * area, a node with no sounding whose quadrant bits changed (fringe to gap), or a ring
     * node whose status the plane pass changed. A node within reach that was already in the
     * area and is otherwise unchanged is not listed.
     */
    public List<Node> drainTouched() {
        List<Node> out = new ArrayList<>(touched);
        for (Node nd : touched) nd.touched = false;
        touched.clear();
        return out;
    }

    /** Feeds one sounding with the feed counter as its id; see {@link #add(double, double, double, double, double, int)}. */
    public void add(double x, double y, double z, double sz, double sh) { add(x, y, z, sz, sh, fed); }

    /**
     * Feeds one sounding at map position (x, y), height z, with sigmas sz and sh (metres)
     * and capture id `id` (unique within the session). The nodes within the capture radius
     * (the ones the sounding feeds) are visited first, then the rest of the reach, so near
     * MAX_NODES the remaining budget goes to the nodes that take the sounding, not to the
     * area fringe. The order changes nothing else: each node gets at most one update per
     * sounding.
     */
    public void add(double x, double y, double z, double sz, double sh, int id) {
        double cell = params.cell, rad = params.captureRadius, reach = params.reach;
        double r = Math.max(rad, reach);
        int i0 = (int) Math.ceil((x - r) / cell), i1 = (int) Math.floor((x + r) / cell);
        int j0 = (int) Math.ceil((y - r) / cell), j1 = (int) Math.floor((y + r) / cell);
        fed++;
        nFed = 0;
        for (int pass = 0; pass < 2; pass++) {
            for (int j = j0; j <= j1; j++) {
                for (int i = i0; i <= i1; i++) {
                    double d = Math.hypot(i * cell - x, j * cell - y);
                    boolean feeds = d <= rad, marks = d <= reach;
                    if (pass == 0 ? !feeds : feeds || !marks) continue;
                    Node nd = getOrCreate(i, j);
                    if (nd == null) continue;
                    boolean changed = feeds;
                    if (feeds) {
                        double s = SLOPE * (d + sh);
                        feed(nd, z, sz * sz + s * s, id, x - i * cell, y - j * cell, sz * sz, sh);
                        if (nFed == fedNodes.length) fedNodes = Arrays.copyOf(fedNodes, 2 * nFed);
                        fedNodes[nFed++] = nd;
                    }
                    if (marks) {
                        int q = nd.quad | 1 << ((x - i * cell >= 0 ? 1 : 0) | (y - j * cell >= 0 ? 2 : 0));
                        if (q != nd.quad && nd.n == 0) changed = true;   // may turn fringe into a gap
                        nd.quad = q;
                        if (!nd.inArea) {
                            nd.inArea = true;
                            changed = true;
                            if (i < minI) minI = i;
                            if (i > maxI) maxI = i;
                            if (j < minJ) minJ = j;
                            if (j > maxJ) maxJ = j;
                        }
                    }
                    if (changed) touch(nd);
                }
            }
        }
        if (plane && nFed > 0) ring();
    }

    // ---- the plane pass over the ring (class comment, Plane pass)

    private Node[] fedNodes = new Node[16], ringNodes = new Node[64];
    private int nFed, ringStamp;

    /** Redoes the plane pass for the fed nodes and their 8 neighbours. */
    private void ring() {
        int stamp = ++ringStamp, nr = 0;
        for (int f = 0; f < nFed; f++) {
            Node c = fedNodes[f];
            for (int dj = -1; dj <= 1; dj++) {
                for (int di = -1; di <= 1; di++) {
                    Node q = node(c.i + di, c.j + dj);
                    if (q == null || q.n == 0 || q.ringMark == stamp) continue;
                    q.ringMark = stamp;
                    if (nr == ringNodes.length) ringNodes = Arrays.copyOf(ringNodes, 2 * nr);
                    ringNodes[nr++] = q;
                }
            }
        }
        for (int k = 0; k < nr; k++) {
            Node q = ringNodes[k];
            Status before = status(q);
            planeNode(q);
            if (status(q) != before) touch(q);
            ringNodes[k] = null;
        }
        for (int f = 0; f < nFed; f++) fedNodes[f] = null;
    }

    private static boolean red(int nIn, int nOut) { return nOut > 0 && nIn < OUTVOTE * nOut; }

    /** The plane pass redone for every node from scratch (tests: the ring must equal it). */
    void replaneAll() {
        if (!plane) return;
        for (Node nd : nodes.values()) if (nd.n > 0) planeNode(nd);
    }

    private static void finalIsPass1(Node nd, int code, double gradient) {
        nd.planar = false;
        nd.planeCode = code;
        nd.gradient = gradient;
        nd.zFinal = nd.z();
        nd.wFinal = nd.w;
        nd.varFinal = 1.0 / nd.w;
        nd.sigmaFinal = nd.sigma();
        nd.nInFinal = nd.nIn;
        nd.nOutFinal = nd.nOut;
    }

    private final double[] nbX = new double[8], nbY = new double[8], nbZ = new double[8], nbW = new double[8];
    private final boolean[] nbRed = new boolean[8];
    private final PlanePass.Fit fit = new PlanePass.Fit();

    /** nodes.plane_pass for one node from its neighbours' pass-1 state; sets the final values. */
    private void planeNode(Node nd) {
        if (!red(nd.nIn, nd.nOut)) { finalIsPass1(nd, PlanePass.NOT_TRIED, Double.NaN); return; }
        double cell = params.cell;
        int m = 0;
        for (int dj = -1; dj <= 1; dj++) {
            for (int di = -1; di <= 1; di++) {
                if (di == 0 && dj == 0) continue;
                Node q = node(nd.i + di, nd.j + dj);
                if (q == null || q.n == 0 || q.nIn < PlanePass.MIN_NIN) continue;
                nbX[m] = di * cell + q.cx;
                nbY[m] = dj * cell + q.cy;
                nbZ[m] = q.z();
                nbW[m] = q.w;
                nbRed[m] = red(q.nIn, q.nOut);
                m++;
            }
        }
        int code = PlanePass.fit(nbX, nbY, nbZ, nbW, nbRed, m, PlanePass.MIN_NEIGHBOURS, PlanePass.MIN_CLEAR,
                PlanePass.RED_WEIGHT, PlanePass.PLANARITY, PlanePass.MAX_SIGMA_G, fit);
        if (code != 0) { finalIsPass1(nd, code, Double.NaN); return; }
        double gx = fit.gx, gy = fit.gy, cxx = fit.cxx, cxy = fit.cxy, cyy = fit.cyy;
        double g = Math.hypot(gx, gy);
        code = PlanePass.significance(fit, SLOPE, K_SIGMA);   // the phone has no DEM: no DEM gate
        if (code == PlanePass.ILL_CONDITIONED) { finalIsPass1(nd, code, Double.NaN); return; }
        if (code != 0) { finalIsPass1(nd, code, g); return; }
        // pass 2: the entries reduced to the node centre into the scratch arrays (as rebuild loads them)
        ne = 0;
        for (int k = 0; k < nd.m; k++) {
            reduced(nd.ez[k], nd.eid[k], 1, nd.edx[k], nd.edy[k], nd.esz2[k], nd.esh[k], gx, gy, g, cxx, cxy, cyy);
        }
        if (nd.aggN > 0) {
            double wa = nd.aggW;
            reduced(nd.aggWz / wa, -1, nd.aggN, nd.aggWdx / wa, nd.aggWdy / wa, nd.aggWs / (wa * wa),
                    Math.sqrt(nd.aggWh) / wa, gx, gy, g, cxx, cxy, cyy);
        }
        rebuildScratch();
        double w2 = hW[mainH], z2 = hWz[mainH] / w2;
        int nIn2 = hCnt[mainH], nOut2 = nd.n - nIn2;
        double bx = 0, by = 0;
        for (int k = 0; k < ne; k++) if (hypOf[k] == mainH) { bx += sw[k] * sdx[k]; by += sw[k] * sdy[k]; }
        bx /= w2;
        by /= w2;
        double vbar = Math.max(bx * bx * cxx + 2.0 * bx * by * cxy + by * by * cyy, 0.0);
        if (red(nIn2, nOut2) && !(nOut2 < nd.nOut)) { finalIsPass1(nd, PlanePass.NO_GAIN, g); return; }
        double vagree = fit.varA + 1.0 / w2 + vbar + 2.0 * (bx * fit.cax + by * fit.cay);
        double tol = Double.isFinite(vagree) ? K_SIGMA * Math.sqrt(Math.max(vagree, 0.0)) : Double.NaN;
        if (!(Double.isFinite(tol) && Math.abs(z2 - fit.a) <= tol)) { finalIsPass1(nd, PlanePass.OFF_PLANE, g); return; }
        nd.planar = true;
        nd.planeCode = PlanePass.ACCEPTED;
        nd.gradient = g;
        nd.zFinal = z2;
        nd.wFinal = w2;
        nd.varFinal = 1.0 / w2 + vbar;
        nd.sigmaFinal = Math.sqrt(nd.varFinal);
        nd.nInFinal = nIn2;
        nd.nOutFinal = nOut2;
    }

    /** One pass-2 entry into the scratch arrays (nodes.reduce_to_plane). */
    private void reduced(double z, int id, int cnt, double dx, double dy, double sz2, double sh, double gx, double gy,
            double g, double cxx, double cxy, double cyy) {
        double vg = dx * dx * cxx + 2.0 * dx * dy * cxy + dy * dy * cyy;
        double v = sz2 + (g * sh) * (g * sh) + vg;
        sz[ne] = z - gx * dx - gy * dy;
        sv[ne] = v;
        sw[ne] = 1.0 / v;
        sid[ne] = id;
        scnt[ne] = cnt;
        sdx[ne] = dx;
        sdy[ne] = dy;
        ne++;
    }

    // ---- the store and the rebuild (nodes.NodeStore / nodes.rebuild); one thread only

    private final double[] sz = new double[CAP + 2], sv = new double[CAP + 2], sw = new double[CAP + 2],
            sdx = new double[CAP + 2], sdy = new double[CAP + 2];
    private final int[] sid = new int[CAP + 2], scnt = new int[CAP + 2], byZ = new int[CAP + 2],
            order = new int[CAP + 2], hypOf = new int[CAP + 2], hCnt = new int[CAP + 2];
    private final double[] hW = new double[CAP + 2], hWz = new double[CAP + 2];
    private int ne, nh, mainH;

    /** Appends an entry to the node's store, folds past CAP, and rebuilds (class comment, Fold). */
    private void feed(Node nd, double z, double v, int id, double dx, double dy, double sz2, double sh) {
        if (nd.ez == null) {
            nd.ez = new double[4]; nd.ev = new double[4]; nd.ew = new double[4]; nd.eid = new int[4];
            nd.edx = new double[4]; nd.edy = new double[4]; nd.esz2 = new double[4]; nd.esh = new double[4];
        } else if (nd.m == nd.ez.length) {
            int c = Math.min(2 * nd.m, CAP + 1);
            nd.ez = Arrays.copyOf(nd.ez, c); nd.ev = Arrays.copyOf(nd.ev, c); nd.ew = Arrays.copyOf(nd.ew, c);
            nd.eid = Arrays.copyOf(nd.eid, c);
            nd.edx = Arrays.copyOf(nd.edx, c); nd.edy = Arrays.copyOf(nd.edy, c);
            nd.esz2 = Arrays.copyOf(nd.esz2, c); nd.esh = Arrays.copyOf(nd.esh, c);
        }
        int e = nd.m;
        nd.ez[e] = z; nd.ev[e] = v; nd.ew[e] = 1.0 / v; nd.eid[e] = id;
        nd.edx[e] = dx; nd.edy[e] = dy; nd.esz2[e] = sz2; nd.esh[e] = sh;
        nd.m++;
        if (nd.m > CAP) {
            rebuild(nd);
            int oldest = -1;
            for (int k = 0; k < nd.m; k++) if (hypOf[k] == mainH) { oldest = k; break; }
            if (oldest >= 0) {
                if (nd.aggN > 0 && hypOf[nd.m] != mainH) {   // the aggregate is the entry after the stored ones
                    nd.nDropped += nd.aggN;
                    nd.aggW = 0; nd.aggWz = 0; nd.aggN = 0;
                    nd.aggWdx = 0; nd.aggWdy = 0; nd.aggWs = 0; nd.aggWh = 0;
                }
                double w = nd.ew[oldest];
                nd.aggW += w;
                nd.aggWz += w * nd.ez[oldest];
                nd.aggN++;
                nd.aggWdx += w * nd.edx[oldest];
                nd.aggWdy += w * nd.edy[oldest];
                nd.aggWs += w * w * nd.esz2[oldest];
                nd.aggWh += w * w * nd.esh[oldest] * nd.esh[oldest];
                remove(nd, oldest);
            } else {
                remove(nd, 0);
                nd.nDropped++;
            }
        }
        rebuild(nd);
        int nAll = nd.m + nd.aggN + nd.nDropped;
        nd.n = nAll;
        nd.nIn = hCnt[mainH];
        nd.nOut = nAll - nd.nIn;
        nd.w = hW[mainH];
        nd.wz = hWz[mainH];
        int alt = -1;
        for (int h = 0; h < nh; h++) if (h != mainH && (alt < 0 || hCnt[h] > hCnt[alt])) alt = h;
        nd.nAlt = alt < 0 ? 0 : hCnt[alt];
        nd.zAlt = alt < 0 ? Double.NaN : hWz[alt] / hW[alt];
        double mr = 0, zMain = hWz[mainH] / hW[mainH];
        for (int k = 0; k < ne; k++) {
            double q = Math.abs(sz[k] - zMain) / Math.sqrt(1.0 / hW[mainH] + sv[k]);
            if (q > mr) mr = q;
        }
        nd.maxNr = mr;
        double cx = 0, cy = 0;
        for (int k = 0; k < ne; k++) if (hypOf[k] == mainH) { cx += sw[k] * sdx[k]; cy += sw[k] * sdy[k]; }
        nd.cx = cx / hW[mainH];
        nd.cy = cy / hW[mainH];
        finalIsPass1(nd, PlanePass.NOT_TRIED, Double.NaN);   // the ring (plane on) recomputes it
    }

    private static void remove(Node nd, int k) {
        int tail = nd.m - k - 1;
        System.arraycopy(nd.ez, k + 1, nd.ez, k, tail);
        System.arraycopy(nd.ev, k + 1, nd.ev, k, tail);
        System.arraycopy(nd.ew, k + 1, nd.ew, k, tail);
        System.arraycopy(nd.eid, k + 1, nd.eid, k, tail);
        System.arraycopy(nd.edx, k + 1, nd.edx, k, tail);
        System.arraycopy(nd.edy, k + 1, nd.edy, k, tail);
        System.arraycopy(nd.esz2, k + 1, nd.esz2, k, tail);
        System.arraycopy(nd.esh, k + 1, nd.esh, k, tail);
        nd.m--;
    }

    private boolean zLess(int a, int b) {
        return sz[a] < sz[b] || (sz[a] == sz[b] && sid[a] < sid[b]);
    }

    private boolean distLess(int a, int b, double zm) {
        double da = Math.abs(sz[a] - zm), db = Math.abs(sz[b] - zm);
        return da < db || (da == db && sid[a] < sid[b]);
    }

    /** The node's entries (stored, then the aggregate) into the scratch arrays, rebuilt. */
    private void rebuild(Node nd) {
        ne = 0;
        for (int k = 0; k < nd.m; k++) {
            sz[ne] = nd.ez[k]; sv[ne] = nd.ev[k]; sw[ne] = nd.ew[k]; sid[ne] = nd.eid[k]; scnt[ne] = 1;
            sdx[ne] = nd.edx[k]; sdy[ne] = nd.edy[k]; ne++;
        }
        if (nd.aggN > 0) {
            sz[ne] = nd.aggWz / nd.aggW; sv[ne] = 1.0 / nd.aggW; sw[ne] = nd.aggW; sid[ne] = -1; scnt[ne] = nd.aggN;
            sdx[ne] = nd.aggWdx / nd.aggW; sdy[ne] = nd.aggWdy / nd.aggW; ne++;
        }
        rebuildScratch();
    }

    /** The rebuild of the ne entries in the scratch arrays. */
    private void rebuildScratch() {
        for (int k = 0; k < ne; k++) {   // insertion sort: stable, n <= CAP + 1
            int x = k, p = k - 1;
            while (p >= 0 && zLess(x, byZ[p])) { byZ[p + 1] = byZ[p]; p--; }
            byZ[p + 1] = x;
        }
        double wAll = 0;
        for (int k = 0; k < ne; k++) wAll += sw[byZ[k]];
        double cum = 0, zm = sz[byZ[ne - 1]];
        for (int k = 0; k < ne; k++) {
            cum += sw[byZ[k]];
            if (2.0 * cum >= wAll) { zm = sz[byZ[k]]; break; }
        }
        for (int k = 0; k < ne; k++) {
            int x = k, p = k - 1;
            while (p >= 0 && distLess(x, order[p], zm)) { order[p + 1] = order[p]; p--; }
            order[p + 1] = x;
        }
        nh = 0;
        for (int k = 0; k < ne; k++) {
            int e = order[k];
            int h;
            if (nh == 0) {
                h = nh++;
                hW[h] = 0; hWz[h] = 0; hCnt[h] = 0;
            } else if (nr(0, e) <= K_SIGMA) {
                h = 0;
            } else {
                h = -1;
                double best = 0;
                for (int g = 1; g < nh; g++) {
                    double q = nr(g, e);
                    if (q <= K_SIGMA && (h < 0 || q < best)) { h = g; best = q; }
                }
                if (h < 0) { h = nh++; hW[h] = 0; hWz[h] = 0; hCnt[h] = 0; }
            }
            hW[h] += sw[e];
            hWz[h] += sw[e] * sz[e];
            hCnt[h] += scnt[e];
            hypOf[e] = h;
        }
        mainH = 0;
        if (nh > 1) {
            int side = 1;
            for (int h = 2; h < nh; h++) if (hCnt[h] > hCnt[side]) side = h;
            if (hCnt[side] > hCnt[0]) mainH = side;
        }
    }

    private double nr(int h, int e) {
        return Math.abs(sz[e] - hWz[h] / hW[h]) / Math.sqrt(1.0 / hW[h] + sv[e]);
    }

    /**
     * Surface captures, RTK fixed, not suspect, not voided, single or tilt, with a position
     * and a ground height.
     */
    public static boolean feeds(PointRow r) {
        return PointRow.SURFACE.equals(r.cls) && r.fix == 4 && !r.fixSuspect && !r.voided
                && (PointRow.SINGLE.equals(r.method) || PointRow.TILT.equals(r.method))
                && r.hasPosition() && !Double.isNaN(r.hGround);
    }

    /**
     * Feeds a capture row when {@link #feeds} takes it: position and ground height in the
     * map frame, sigmas from {@link SoundingSigma} with the ground setting, the capture number
     * as id. Returns whether it was fed.
     */
    public boolean add(PointRow r, LocalFrame frame) {
        if (!feeds(r)) return false;
        double[] enu = frame.toEnu(r.lat, r.lon, r.hGround);
        SoundingSigma.Terms t = SoundingSigma.of(r, params.ground);
        add(enu[0], enu[1], enu[2], t.sigmaZ, t.sigmaXy, r.n);
        return true;
    }

    /**
     * sqrt(sigmaFinal^2 + wander^2) (sqrt(1/W + wander^2) when not planar), the sigma compared
     * with the target; NaN with no sounding.
     */
    public double displaySigma(Node nd) {
        if (nd == null || nd.n == 0) return Double.NaN;
        return Math.sqrt(nd.varFinal + params.wander * params.wander);
    }

    public Status status(Node nd) {
        if (nd == null || !nd.inArea) return Status.NONE;
        if (nd.n == 0) return nd.quad == 15 ? Status.NO_DATA : Status.FRINGE;
        if (red(nd.nInFinal, nd.nOutFinal)) return Status.DISAGREE;
        return displaySigma(nd) > params.target ? Status.THIN : Status.OK;
    }
}
