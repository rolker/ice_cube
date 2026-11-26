package rink.icecube.logger.core;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * The live coverage estimator (logger 0.7, issue #6; 0.8, #24): a port of icecube's
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
 * Area. Each sounding also marks every node within `reach` as in the surveyed area, and
 * sets the node's quadrant bit (capture minus node: x >= 0 bit 0, y >= 0 bit 1).
 *
 * Status. NONE outside the area. With no sounding: NO_DATA (red) when all four quadrants
 * are marked (an enclosed gap), else FRINGE (the area's edge, drawn neutral). DISAGREE
 * (red) while nOut > 0 and nIn < OUTVOTE * nOut; else THIN (amber) when
 * sqrt(1/W + wander^2) is above the target, else OK. The wander term is the pass wander the
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
         */
        public String eventText(LocalFrame frame, String originNote) {
            String o = frame == null ? "origin=none"
                    : String.format(Locale.US, "origin_lat=%.9f origin_lon=%.9f origin_h=%.4f", frame.lat0, frame.lon0,
                    frame.h0);
            return String.format(Locale.US, "coverage cell=%s radius=%s reach=%s target=%s wander=%s ground=%s "
                    + "slope=%s k_sigma=%s cap=%d outvote=%d %s origin_source=\"%s\"", cell, captureRadius, reach,
                    target, wander, ground, SLOPE, K_SIGMA, CAP, OUTVOTE, o,
                    originNote == null ? "" : originNote.replace('"', '\''));
        }
    }

    /** One grid node. */
    public static final class Node {
        public final int i, j;
        public boolean inArea;
        /** Quadrant bits of the captures that marked it (see Area). */
        public int quad;
        /** Contributions (stored, folded and dropped), main members, outliers, largest side bin. */
        public int n, nIn, nOut, nAlt;
        /** Sum of weights 1/v and of w*z of the main hypothesis. */
        public double w, wz;
        /** Mean of the largest side bin (NaN when none). */
        public double zAlt = Double.NaN;
        /** Largest normalised difference of any entry from the main, at the last rebuild. */
        public double maxNr;
        private boolean touched;
        // the store: entries in arrival order, the aggregate, the dropped count
        private double[] ez, ev, ew;
        private int[] eid;
        private int m;
        private double aggW, aggWz;
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
    private final Map<Long, Node> nodes = new HashMap<>();
    private final List<Node> touched = new ArrayList<>();
    private boolean full;
    private int fed;
    /** Bounding box of the in-area nodes (min > max while there are none). */
    public int minI = Integer.MAX_VALUE, maxI = Integer.MIN_VALUE, minJ = Integer.MAX_VALUE, maxJ = Integer.MIN_VALUE;

    public Coverage(Params p) { params = p; }

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
     * area, or a node with no sounding whose quadrant bits changed (fringe to gap). A node
     * within reach that was already in the area and is otherwise unchanged is not listed.
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
                        feed(nd, z, sz * sz + s * s, id);
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
    }

    // ---- the store and the rebuild (nodes.NodeStore / nodes.rebuild); one thread only

    private final double[] sz = new double[CAP + 2], sv = new double[CAP + 2], sw = new double[CAP + 2];
    private final int[] sid = new int[CAP + 2], scnt = new int[CAP + 2], byZ = new int[CAP + 2],
            order = new int[CAP + 2], hypOf = new int[CAP + 2], hCnt = new int[CAP + 2];
    private final double[] hW = new double[CAP + 2], hWz = new double[CAP + 2];
    private int ne, nh, mainH;

    /** Appends an entry to the node's store, folds past CAP, and rebuilds (class comment, Fold). */
    private void feed(Node nd, double z, double v, int id) {
        if (nd.ez == null) {
            nd.ez = new double[4]; nd.ev = new double[4]; nd.ew = new double[4]; nd.eid = new int[4];
        } else if (nd.m == nd.ez.length) {
            int c = Math.min(2 * nd.m, CAP + 1);
            nd.ez = Arrays.copyOf(nd.ez, c); nd.ev = Arrays.copyOf(nd.ev, c); nd.ew = Arrays.copyOf(nd.ew, c);
            nd.eid = Arrays.copyOf(nd.eid, c);
        }
        nd.ez[nd.m] = z; nd.ev[nd.m] = v; nd.ew[nd.m] = 1.0 / v; nd.eid[nd.m] = id;
        nd.m++;
        if (nd.m > CAP) {
            rebuild(nd);
            int oldest = -1;
            for (int k = 0; k < nd.m; k++) if (hypOf[k] == mainH) { oldest = k; break; }
            if (oldest >= 0) {
                if (nd.aggN > 0 && hypOf[nd.m] != mainH) {   // the aggregate is the entry after the stored ones
                    nd.nDropped += nd.aggN;
                    nd.aggW = 0; nd.aggWz = 0; nd.aggN = 0;
                }
                nd.aggW += nd.ew[oldest];
                nd.aggWz += nd.ew[oldest] * nd.ez[oldest];
                nd.aggN++;
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
    }

    private static void remove(Node nd, int k) {
        int tail = nd.m - k - 1;
        System.arraycopy(nd.ez, k + 1, nd.ez, k, tail);
        System.arraycopy(nd.ev, k + 1, nd.ev, k, tail);
        System.arraycopy(nd.ew, k + 1, nd.ew, k, tail);
        System.arraycopy(nd.eid, k + 1, nd.eid, k, tail);
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
        for (int k = 0; k < nd.m; k++) { sz[ne] = nd.ez[k]; sv[ne] = nd.ev[k]; sw[ne] = nd.ew[k]; sid[ne] = nd.eid[k]; scnt[ne] = 1; ne++; }
        if (nd.aggN > 0) {
            sz[ne] = nd.aggWz / nd.aggW; sv[ne] = 1.0 / nd.aggW; sw[ne] = nd.aggW; sid[ne] = -1; scnt[ne] = nd.aggN; ne++;
        }
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

    /** sqrt(1/W + wander^2), the sigma compared with the target; NaN with no sounding. */
    public double displaySigma(Node nd) {
        if (nd == null || nd.n == 0) return Double.NaN;
        return Math.sqrt(1.0 / nd.w + params.wander * params.wander);
    }

    public Status status(Node nd) {
        if (nd == null || !nd.inArea) return Status.NONE;
        if (nd.n == 0) return nd.quad == 15 ? Status.NO_DATA : Status.FRINGE;
        if (nd.nOut > 0 && nd.nIn < OUTVOTE * nd.nOut) return Status.DISAGREE;
        return displaySigma(nd) > params.target ? Status.THIN : Status.OK;
    }
}
