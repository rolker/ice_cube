package rink.icecube.logger.core;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * The math of a tilt capture: the pole tip on a point, the antenna at several leans.
 *
 * Sphere: every antenna position p_i (local ENU metres) is L (the pole height) from the
 * tip c. {@link #solve} finds c by Gauss-Newton least squares on |p_i - c| - L, starting
 * from mean(p) - (0, 0, L) (so it settles on the tip below the antenna, not the mirror
 * solution above). Residuals, RMS (sqrt of the mean squared residual), each lean's tilt
 * from vertical and azimuth (the direction the pole leans toward, clockwise from north),
 * and per-axis standard deviations from the normal matrix scaled by
 * max(sum r^2 / (n - 3), (1 cm)^2), so a lucky fit of few leans does not claim mm.
 *
 * Coverage: 360 degrees minus the largest gap between lean azimuths. With leans on one
 * side only, the tip is poorly determined along that direction.
 *
 * IMU check (no calibration needed): the phone is rigid on the pole, so the pole axis is
 * a fixed unknown direction a in phone axes, and the angle between a and the phone's
 * gravity ("up") vector at lean i must equal that lean's tilt from the sphere solution.
 * a is fitted to the other leans and the disagreement of each lean is reported
 * ({@link #imuCheck}). This holds however the pole is twisted about its own axis
 * (comparing gravity vectors pairwise would not: a twist changes the angle between
 * them but not between the pole directions). A tip that slipped, or a bad fix, shows as
 * a lean that disagrees.
 */
public final class TiltSolver {
    public static final double SIGMA_FLOOR_M = 0.01;
    private static final int ITERATIONS = 50;

    private TiltSolver() { }

    /** A sphere fit. Arrays are in the order of the positions given. */
    public static final class Solution {
        public final double[] tip;          // E, N, U (m)
        public final double[] resid;        // |p_i - c| - L (m)
        public final double[] tiltDeg, azimuthDeg;
        public final double rms;            // sqrt(mean resid^2), m
        public final double[] sd;           // E, N, U standard deviations of the tip (m)
        public final double coverageDeg;
        public final int n;

        Solution(double[] tip, double[] resid, double[] tiltDeg, double[] azimuthDeg, double rms, double[] sd,
                 double coverageDeg) {
            this.tip = tip; this.resid = resid; this.tiltDeg = tiltDeg; this.azimuthDeg = azimuthDeg; this.rms = rms;
            this.sd = sd; this.coverageDeg = coverageDeg; n = resid.length;
        }

        public double maxSd() { return Math.max(sd[0], Math.max(sd[1], sd[2])); }

        public double maxAbsResid() {
            double m = 0;
            for (double r : resid) m = Math.max(m, Math.abs(r));
            return m;
        }
    }

    /** The tip for antenna positions p (ENU m, at least 3) and pole height L. Null if it does not converge. */
    public static Solution solve(List<double[]> p, double L) {
        int n = p.size();
        if (n < 3 || !(L > 0)) return null;
        double[] c = new double[3];
        for (double[] x : p) for (int k = 0; k < 3; k++) c[k] += x[k] / n;
        c[2] -= L;
        boolean converged = false;
        for (int it = 0; it < ITERATIONS; it++) {
            double[][] jtj = new double[3][3];
            double[] jtr = new double[3];
            for (double[] x : p) {
                double[] v = {x[0] - c[0], x[1] - c[1], x[2] - c[2]};
                double d = norm(v);
                if (d < 1e-9) return null;
                double r = d - L;
                double[] j = {-v[0] / d, -v[1] / d, -v[2] / d};
                for (int a = 0; a < 3; a++) {
                    jtr[a] += j[a] * r;
                    for (int b = 0; b < 3; b++) jtj[a][b] += j[a] * j[b];
                }
            }
            double[] dc = solve3(jtj, new double[] {-jtr[0], -jtr[1], -jtr[2]});
            if (dc == null) return null;
            for (int k = 0; k < 3; k++) c[k] += dc[k];
            if (norm(dc) < 1e-7) { converged = true; break; }
        }
        if (!converged) return null;
        double[] resid = new double[n], tilt = new double[n], az = new double[n];
        double[][] jtj = new double[3][3];
        double ss = 0;
        for (int i = 0; i < n; i++) {
            double[] x = p.get(i);
            double[] v = {x[0] - c[0], x[1] - c[1], x[2] - c[2]};
            double d = norm(v);
            resid[i] = d - L;
            ss += resid[i] * resid[i];
            tilt[i] = Math.toDegrees(Math.acos(Math.max(-1, Math.min(1, v[2] / d))));
            az[i] = azimuth(v[0], v[1]);
            for (int a = 0; a < 3; a++) for (int b = 0; b < 3; b++) jtj[a][b] += v[a] * v[b] / (d * d);
        }
        double s2 = Math.max(n > 3 ? ss / (n - 3) : 0, SIGMA_FLOOR_M * SIGMA_FLOOR_M);
        double[][] inv = invert3(jtj);
        double[] sd = new double[3];
        for (int k = 0; k < 3; k++) sd[k] = inv == null ? Double.POSITIVE_INFINITY : Math.sqrt(Math.max(0, s2 * inv[k][k]));
        return new Solution(c, resid, tilt, az, Math.sqrt(ss / n), sd, coverage(az));
    }

    /** Degrees clockwise from north, 0 to 360, of the horizontal direction (e, n). */
    public static double azimuth(double e, double n) {
        double a = Math.toDegrees(Math.atan2(e, n));
        return a < 0 ? a + 360 : a;
    }

    /** 360 minus the largest gap between the azimuths (degrees); 0 for fewer than 2. */
    public static double coverage(double[] azDeg) {
        if (azDeg.length < 2) return 0;
        double[] a = azDeg.clone();
        for (int i = 0; i < a.length; i++) a[i] = ((a[i] % 360) + 360) % 360;
        Arrays.sort(a);
        double gap = a[0] + 360 - a[a.length - 1];
        for (int i = 1; i < a.length; i++) gap = Math.max(gap, a[i] - a[i - 1]);
        return 360 - gap;
    }

    // ---- IMU check ----

    /**
     * The pole axis in phone axes (unit vector) that best explains the tilts: minimises the
     * sum of (angle(g_i, a) - tilt_i)^2 over the gravity vectors g (any length) and tilts
     * (degrees). Null if fewer than 2 or it does not converge.
     */
    public static double[] fitAxis(List<double[]> g, double[] tiltDeg) {
        int n = g.size();
        if (n < 2) return null;
        List<double[]> u = new ArrayList<>();
        double[] a = new double[3];
        for (double[] x : g) {
            double[] y = unit(x);
            if (y == null) return null;
            u.add(y);
            for (int k = 0; k < 3; k++) a[k] += y[k];
        }
        a = unit(a);
        if (a == null) return null;
        for (int it = 0; it < ITERATIONS; it++) {
            double[] e1 = perpendicular(a), e2 = cross(a, e1);
            double h11 = 0, h12 = 0, h22 = 0, b1 = 0, b2 = 0;
            for (int i = 0; i < n; i++) {
                double[] y = u.get(i);
                double cos = Math.max(-1, Math.min(1, dot(y, a)));
                double ang = Math.acos(cos), sin = Math.sqrt(Math.max(1e-12, 1 - cos * cos));
                double r = ang - Math.toRadians(tiltDeg[i]);
                double j1 = -dot(y, e1) / sin, j2 = -dot(y, e2) / sin;
                h11 += j1 * j1; h12 += j1 * j2; h22 += j2 * j2;
                b1 -= j1 * r; b2 -= j2 * r;
            }
            double det = h11 * h22 - h12 * h12;
            if (Math.abs(det) < 1e-18) return a;   // degenerate (e.g. two leans): keep the best so far
            double x = (h22 * b1 - h12 * b2) / det, y = (h11 * b2 - h12 * b1) / det;
            double[] next = unit(new double[] {a[0] + x * e1[0] + y * e2[0], a[1] + x * e1[1] + y * e2[1],
                    a[2] + x * e1[2] + y * e2[2]});
            if (next == null) return null;
            a = next;
            if (Math.hypot(x, y) < 1e-10) break;
        }
        return a;
    }

    /** Angle in degrees between two vectors. */
    public static double angleDeg(double[] x, double[] y) {
        double[] a = unit(x), b = unit(y);
        if (a == null || b == null) return Double.NaN;
        return Math.toDegrees(Math.acos(Math.max(-1, Math.min(1, dot(a, b)))));
    }

    /**
     * The IMU check. For each lean i (leave one out): the tip is solved without it (with it
     * if that would leave fewer than 4), the pole axis is fitted to the other leans' gravity
     * vectors and tilts; disagreement[i] = |angle(g_i, axis) - tilt_i| in degrees, and
     * restRms[i] = the RMS disagreement of the other leans with that fit. The lean whose
     * removal leaves the others most consistent (smallest restRms) is the likeliest culprit.
     * NaN for a lean without gravity, or for all if fewer than 4 leans have gravity.
     */
    public static final class Imu {
        public final double[] disagreement, restRms;
        Imu(int n) {
            disagreement = new double[n];
            restRms = new double[n];
            Arrays.fill(disagreement, Double.NaN);
            Arrays.fill(restRms, Double.NaN);
        }

        /** The largest disagreement (NaN if none). */
        public double max() {
            double m = Double.NaN;
            for (double d : disagreement) if (!Double.isNaN(d)) m = Double.isNaN(m) ? d : Math.max(m, d);
            return m;
        }

        /** Index of the likeliest culprit (smallest restRms), or -1. */
        public int culprit() {
            int k = -1;
            for (int i = 0; i < restRms.length; i++) {
                if (!Double.isNaN(restRms[i]) && (k < 0 || restRms[i] < restRms[k])) k = i;
            }
            return k;
        }
    }

    /** See {@link Imu}. gravity entries may be null. */
    public static Imu imuCheck(List<double[]> p, List<double[]> gravity, double L) {
        int n = p.size();
        Imu out = new Imu(n);
        int withG = 0;
        for (double[] x : gravity) if (x != null) withG++;
        if (withG < 4 || n < 4) return out;
        Solution all = solve(p, L);
        if (all == null) return out;
        for (int i = 0; i < n; i++) {
            if (gravity.get(i) == null) continue;
            Solution s = all;
            if (n - 1 >= 4) {
                List<double[]> others = new ArrayList<>(p);
                others.remove(i);
                Solution o = solve(others, L);
                if (o != null) s = o;
            }
            double[] c = s.tip;
            double[] tilts = new double[n];
            for (int j = 0; j < n; j++) {
                double[] x = p.get(j);
                tilts[j] = angleDeg(new double[] {x[0] - c[0], x[1] - c[1], x[2] - c[2]}, new double[] {0, 0, 1});
            }
            List<double[]> g = new ArrayList<>();
            List<Double> t = new ArrayList<>();
            for (int j = 0; j < n; j++) {
                if (j == i || gravity.get(j) == null) continue;
                g.add(gravity.get(j));
                t.add(tilts[j]);
            }
            double[] tt = new double[t.size()];
            for (int j = 0; j < tt.length; j++) tt[j] = t.get(j);
            double[] axis = fitAxis(g, tt);
            if (axis == null) continue;
            out.disagreement[i] = Math.abs(angleDeg(gravity.get(i), axis) - tilts[i]);
            double ss = 0;
            for (int j = 0; j < g.size(); j++) ss += Math.pow(angleDeg(g.get(j), axis) - tt[j], 2);
            out.restRms[i] = Math.sqrt(ss / g.size());
        }
        return out;
    }

    // ---- small linear algebra ----

    static double dot(double[] a, double[] b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
    static double norm(double[] a) { return Math.sqrt(dot(a, a)); }
    static double[] cross(double[] a, double[] b) {
        return new double[] {a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]};
    }

    static double[] unit(double[] a) {
        double n = norm(a);
        if (!(n > 1e-12)) return null;
        return new double[] {a[0] / n, a[1] / n, a[2] / n};
    }

    /** A unit vector perpendicular to the unit vector a. */
    static double[] perpendicular(double[] a) {
        double[] t = Math.abs(a[0]) < 0.9 ? new double[] {1, 0, 0} : new double[] {0, 1, 0};
        return unit(cross(a, t));
    }

    static double[][] invert3(double[][] m) {
        double a = m[0][0], b = m[0][1], c = m[0][2], d = m[1][0], e = m[1][1], f = m[1][2],
                g = m[2][0], h = m[2][1], k = m[2][2];
        double det = a * (e * k - f * h) - b * (d * k - f * g) + c * (d * h - e * g);
        if (Math.abs(det) < 1e-15) return null;
        return new double[][] {
                {(e * k - f * h) / det, (c * h - b * k) / det, (b * f - c * e) / det},
                {(f * g - d * k) / det, (a * k - c * g) / det, (c * d - a * f) / det},
                {(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det}};
    }

    static double[] solve3(double[][] m, double[] r) {
        double[][] inv = invert3(m);
        if (inv == null) return null;
        double[] x = new double[3];
        for (int i = 0; i < 3; i++) x[i] = inv[i][0] * r[0] + inv[i][1] * r[1] + inv[i][2] * r[2];
        return x;
    }
}
