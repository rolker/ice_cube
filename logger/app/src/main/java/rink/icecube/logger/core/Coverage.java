package rink.icecube.logger.core;

import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * The live coverage estimator (logger 0.7, issue #6): a one-pass port of icecube's node
 * estimator ({@code icecube/nodes.py}, {@code grid_surface}) over a sparse grid, fed one
 * surface capture at a time, plus the area rule and the red / amber / clear status the map
 * draws.
 *
 * Grid. Node (i, j) sits at (i * cell, j * cell) in the map frame (metres east, north of
 * the map origin), so a node keeps its place as the walked area grows. Nodes are created
 * on demand, up to MAX_NODES.
 *
 * Update (nodes.py with one pass and no pass bias). A sounding z with vertical sigma sz and
 * horizontal sigma sh feeds every node within the capture radius, at distance d, with
 * variance v = sz^2 + (SLOPE * (d + sh))^2. Before it is fused, a node that already has an
 * estimate counts a disagreement when |z - z_node| > K_SIGMA * sqrt(1/W + v). Then
 * W += 1/v, WZ += z/v, so z_node = WZ / W and its sigma is sqrt(1/W).
 *
 * Area. Each sounding also marks every node within `reach` as in the surveyed area. Only
 * in-area nodes are drawn; one with no sounding within the capture radius is NO_DATA.
 *
 * Status. NO_DATA (no sounding) and DISAGREE (flags > 0) are red; THIN (sigma above the
 * target) amber; OK clear. The sigma tested is sqrt(1/W + wander^2): the pass wander the
 * live view cannot solve (icecube's crossover adjustment does), added only here so the
 * fusion itself stays a plain nodes.py comparison (CoverageTest checks it against vectors
 * from {@code python3 -m icecube.logger_vectors}).
 */
public final class Coverage {
    /** nodes.py defaults (icecube.example.toml): slope allowance m/m and the disagreement test. */
    public static final double SLOPE = 0.05, K_SIGMA = 3.0;
    /** About 6 ha at 0.5 m spacing. Past it no new node is created; existing ones still update. */
    public static final int MAX_NODES = 250_000;

    public enum Status { NONE, NO_DATA, DISAGREE, THIN, OK }

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
    }

    /** One grid node. */
    public static final class Node {
        public final int i, j;
        /** Sum of weights 1/v and of z/v. */
        public double w, wz;
        public int n, flags;
        /** Largest normalised disagreement seen. */
        public double maxNr;
        public boolean inArea;
        private boolean touched;

        Node(int i, int j) { this.i = i; this.j = j; }

        public double z() { return n == 0 ? Double.NaN : wz / w; }

        /** sqrt(1/W): the fused sigma without the pass wander (nodes.py's sigma with sb = 0). */
        public double sigma() { return n == 0 ? Double.NaN : Math.sqrt(1.0 / w); }
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

    /** Nodes changed since the last call (for repainting only those). */
    public List<Node> drainTouched() {
        List<Node> out = new ArrayList<>(touched);
        for (Node nd : touched) nd.touched = false;
        touched.clear();
        return out;
    }

    /** Feeds one sounding at map position (x, y), height z, with sigmas sz and sh (metres). */
    public void add(double x, double y, double z, double sz, double sh) {
        double cell = params.cell, rad = params.captureRadius, reach = params.reach;
        double r = Math.max(rad, reach);
        int i0 = (int) Math.ceil((x - r) / cell), i1 = (int) Math.floor((x + r) / cell);
        int j0 = (int) Math.ceil((y - r) / cell), j1 = (int) Math.floor((y + r) / cell);
        fed++;
        for (int j = j0; j <= j1; j++) {
            for (int i = i0; i <= i1; i++) {
                double d = Math.hypot(i * cell - x, j * cell - y);
                boolean feeds = d <= rad, marks = d <= reach;
                if (!feeds && !marks) continue;
                Node nd = getOrCreate(i, j);
                if (nd == null) continue;
                if (feeds) {
                    double s = SLOPE * (d + sh);
                    double v = sz * sz + s * s;
                    if (nd.n > 0) {
                        double nr = Math.abs(z - nd.wz / nd.w) / Math.sqrt(1.0 / nd.w + v);
                        if (nr > K_SIGMA) nd.flags++;
                        if (nr > nd.maxNr) nd.maxNr = nr;
                    }
                    nd.w += 1.0 / v;
                    nd.wz += z / v;
                    nd.n++;
                }
                if (marks && !nd.inArea) {
                    nd.inArea = true;
                    if (i < minI) minI = i;
                    if (i > maxI) maxI = i;
                    if (j < minJ) minJ = j;
                    if (j > maxJ) maxJ = j;
                }
                touch(nd);
            }
        }
    }

    /** Surface captures, RTK fixed, not suspect, single or tilt, with a position and a ground height. */
    public static boolean feeds(PointRow r) {
        return PointRow.SURFACE.equals(r.cls) && r.fix == 4 && !r.fixSuspect
                && (PointRow.SINGLE.equals(r.method) || PointRow.TILT.equals(r.method))
                && r.hasPosition() && !Double.isNaN(r.hGround);
    }

    /**
     * Feeds a capture row when {@link #feeds} takes it: position and ground height in the
     * map frame, sigmas from {@link SoundingSigma} with the ground setting. Returns whether it was fed.
     */
    public boolean add(PointRow r, LocalFrame frame) {
        if (!feeds(r)) return false;
        double[] enu = frame.toEnu(r.lat, r.lon, r.hGround);
        SoundingSigma.Terms t = SoundingSigma.of(r, params.ground);
        add(enu[0], enu[1], enu[2], t.sigmaZ, t.sigmaXy);
        return true;
    }

    /** sqrt(1/W + wander^2), the sigma compared with the target; NaN with no sounding. */
    public double displaySigma(Node nd) {
        if (nd == null || nd.n == 0) return Double.NaN;
        return Math.sqrt(1.0 / nd.w + params.wander * params.wander);
    }

    public Status status(Node nd) {
        if (nd == null || !nd.inArea) return Status.NONE;
        if (nd.n == 0) return Status.NO_DATA;
        if (nd.flags > 0) return Status.DISAGREE;
        return displaySigma(nd) > params.target ? Status.THIN : Status.OK;
    }
}
