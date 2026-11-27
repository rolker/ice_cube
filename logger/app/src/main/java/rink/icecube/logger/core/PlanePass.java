package rink.icecube.logger.core;

/**
 * The neighbour-plane fit of the plane pass (logger 0.10.0, issue #29): a port of
 * {@code icecube/nodes.py} {@code plane_fit}, {@code _pos_def} and {@code _sigma_g_max}, pure
 * and allocation-free. {@link Coverage} gathers the neighbours, calls {@link #fit} and does the
 * rest of the pass (significance, pass 2, the gain and agreement tests).
 *
 * Fit. A weighted least-squares plane z = a + gx*X + gy*Y through the neighbours' pass-1
 * mains, each at (X, Y) relative to the node (its grid offset plus its main's weighted
 * centroid), weight its main's W, red ones times RED_WEIGHT. Gates in order: count
 * (at least max(minNeighbours, 4) with positive weight, else FEW), non-red count (FEW_CLEAR),
 * conditioning on the unscaled (A'WA)^-1 (a Cholesky factorisation of A'WA must succeed,
 * the inverse be finite with var_a > 0 and C_g positive definite, and sigma_g, the square root
 * of C_g's larger eigenvalue, be within maxSigmaG; else ILL_CONDITIONED with sigma_g infinite
 * when not positive definite), planarity (chi, the weighted RMS residual per degree of
 * freedom, within `planarity`, else NOT_PLANAR), and conditioning again on the covariance
 * scaled by max(1, chi^2). Every continuous gate is written !(value <= limit) with the value
 * required finite: a NaN or infinite value fails, a NaN limit fails, an infinite limit
 * passes every finite value (the gate off).
 */
public final class PlanePass {
    /** Outcome codes, equal to nodes.PLANE_* (Node.planeCode). */
    public static final int NOT_TRIED = 0, ACCEPTED = 1, FEW = 2, FEW_CLEAR = 3, NOT_PLANAR = 4, NOT_STEEP = 5,
            DEM = 6, NO_GAIN = 7, OFF_PLANE = 8, ILL_CONDITIONED = 9;
    /** nodes.PLANE_DEFAULTS: usable neighbours, main members for a usable neighbour, non-red ones. */
    public static final int MIN_NEIGHBOURS = 5, MIN_NIN = 2, MIN_CLEAR = 2;
    /** nodes.PLANE_DEFAULTS: the weight factor of red neighbours and the conditioning bound (m/m). */
    public static final double RED_WEIGHT = 0.25, MAX_SIGMA_G = 0.08;
    /** The planarity bound: nodes.PLANE_DEFAULTS planarity None, i.e. k_sigma. */
    public static final double PLANARITY = Coverage.K_SIGMA;

    private PlanePass() { }

    /** One fit's result (reused by the caller): the code and what nodes.plane_fit's dict holds. */
    public static final class Fit {
        public int code;
        /** The plane at the node and its gradient. */
        public double a, gx, gy;
        /** var(a), C_g = [[cxx, cxy], [cxy, cyy]] and cov(a, g) = (cax, cay), scaled by max(1, chi^2). */
        public double varA, cxx, cxy, cyy, cax, cay;
        /** NaN where the fit stopped before computing it. */
        public double chi, sigmaG;
        /** Scratch: the 3x3 covariance, row-major. */
        private final double[] inv = new double[9];

        void reset(int c) {
            code = c;
            a = gx = gy = varA = cxx = cxy = cyy = cax = cay = Double.NaN;
            chi = sigmaG = Double.NaN;
        }
    }

    /**
     * Fits the plane through the first `m` neighbours (x, y, z, w, red) into `out` and
     * returns its code (0 when the fit passed every gate here).
     */
    public static int fit(double[] x, double[] y, double[] z, double[] w, boolean[] red, int m, int minNeighbours,
            int minClear, double redWeight, double planarity, double maxSigmaG, Fit out) {
        out.reset(0);
        int used = 0, clear = 0;
        double n00 = 0, n01 = 0, n02 = 0, n11 = 0, n12 = 0, n22 = 0, b0 = 0, b1 = 0, b2 = 0;
        for (int k = 0; k < m; k++) {
            double wk = w[k] * (red[k] ? redWeight : 1.0);
            if (!(wk > 0)) continue;
            used++;
            if (!red[k]) clear++;
        }
        if (used < Math.max(minNeighbours, 4)) { out.code = FEW; return FEW; }
        if (clear < minClear) { out.code = FEW_CLEAR; return FEW_CLEAR; }
        for (int k = 0; k < m; k++) {
            double wk = w[k] * (red[k] ? redWeight : 1.0);
            if (!(wk > 0)) continue;
            n00 += wk; n01 += wk * x[k]; n02 += wk * y[k];
            n11 += wk * x[k] * x[k]; n12 += wk * x[k] * y[k]; n22 += wk * y[k] * y[k];
            b0 += wk * z[k]; b1 += wk * z[k] * x[k]; b2 += wk * z[k] * y[k];
        }
        // Cholesky of N = A'WA (lower L), then N^-1 = L^-T L^-1
        double[] inv = out.inv;
        if (!cholInverse(n00, n01, n02, n11, n12, n22, inv)) return ill(out, Double.POSITIVE_INFINITY, Double.NaN);
        if (!posDef(inv)) return ill(out, Double.POSITIVE_INFINITY, Double.NaN);
        double sg = sigmaGMax(inv[4], inv[5], inv[8]);
        if (!(Double.isFinite(sg) && sg <= maxSigmaG)) return ill(out, sg, Double.NaN);
        double a = inv[0] * b0 + inv[1] * b1 + inv[2] * b2;
        double gx = inv[3] * b0 + inv[4] * b1 + inv[5] * b2;
        double gy = inv[6] * b0 + inv[7] * b1 + inv[8] * b2;
        double ss = 0;
        for (int k = 0; k < m; k++) {
            double wk = w[k] * (red[k] ? redWeight : 1.0);
            if (!(wk > 0)) continue;
            double r = z[k] - (a + gx * x[k] + gy * y[k]);
            ss += wk * r * r;
        }
        double chi = Math.sqrt(Math.max(ss, 0.0) / (used - 3));
        out.chi = chi;
        if (!(Double.isFinite(chi) && chi <= planarity)) {
            out.code = NOT_PLANAR;
            out.sigmaG = sg;
            return NOT_PLANAR;
        }
        double s = Math.max(1.0, chi * chi);
        for (int k = 0; k < 9; k++) inv[k] *= s;
        // defensive: scaling keeps it positive definite unless the product overflows
        if (!posDef(inv)) return ill(out, Double.POSITIVE_INFINITY, chi);
        sg = sigmaGMax(inv[4], inv[5], inv[8]);
        if (!(Double.isFinite(sg) && sg <= maxSigmaG)) return ill(out, sg, chi);
        out.a = a; out.gx = gx; out.gy = gy;
        out.varA = inv[0]; out.cxx = inv[4]; out.cxy = inv[5]; out.cyy = inv[8]; out.cax = inv[1]; out.cay = inv[2];
        out.chi = chi; out.sigmaG = sg;
        return 0;
    }

    private static int ill(Fit out, double sigmaG, double chi) {
        out.code = ILL_CONDITIONED;
        out.sigmaG = sigmaG;
        out.chi = chi;
        return ILL_CONDITIONED;
    }

    /**
     * The inverse of the symmetric 3x3 N (upper triangle given) through its Cholesky factor,
     * row-major into `inv`; false when N is not finite or the factorisation fails (a pivot
     * not positive), as numpy.linalg.cholesky raises.
     */
    static boolean cholInverse(double n00, double n01, double n02, double n11, double n12, double n22, double[] inv) {
        if (!(Double.isFinite(n00) && Double.isFinite(n01) && Double.isFinite(n02) && Double.isFinite(n11)
                && Double.isFinite(n12) && Double.isFinite(n22))) return false;
        if (!(n00 > 0)) return false;
        double l00 = Math.sqrt(n00), l10 = n01 / l00, l20 = n02 / l00;
        double d1 = n11 - l10 * l10;
        if (!(d1 > 0)) return false;
        double l11 = Math.sqrt(d1), l21 = (n12 - l20 * l10) / l11;
        double d2 = n22 - l20 * l20 - l21 * l21;
        if (!(d2 > 0)) return false;
        double l22 = Math.sqrt(d2);
        // M = L^-1 (lower)
        double m00 = 1.0 / l00, m11 = 1.0 / l11, m22 = 1.0 / l22;
        double m10 = -l10 * m00 / l11;
        double m21 = -l21 * m11 / l22;
        double m20 = -(l20 * m00 + l21 * m10) / l22;
        // N^-1 = M' M
        inv[0] = m00 * m00 + m10 * m10 + m20 * m20;
        inv[1] = m10 * m11 + m20 * m21;
        inv[2] = m20 * m22;
        inv[4] = m11 * m11 + m21 * m21;
        inv[5] = m21 * m22;
        inv[8] = m22 * m22;
        inv[3] = inv[1]; inv[6] = inv[2]; inv[7] = inv[5];
        return true;
    }

    /**
     * The significance gate after a passing fit (nodes.plane_pass): sigma_g along the
     * gradient, sqrt(u'C_g u) with u = g/|g| (x when g = 0), fails closed (ILL_CONDITIONED)
     * unless u'C_g u > 0; then NOT_STEEP unless |g| - allowance > kSigma * that sigma;
     * else 0.
     */
    public static int significance(Fit f, double allowance, double kSigma) {
        double g = Math.hypot(f.gx, f.gy);
        double ux = g > 0 ? f.gx / g : 1.0, uy = g > 0 ? f.gy / g : 0.0;
        double sg2 = ux * ux * f.cxx + 2.0 * ux * uy * f.cxy + uy * uy * f.cyy;
        if (!(sg2 > 0.0)) return ILL_CONDITIONED;
        return g - allowance > kSigma * Math.sqrt(sg2) ? 0 : NOT_STEEP;
    }

    /**
     * nodes._pos_def: the 3x3 covariance (row-major) is finite, var_a > 0 and C_g (its lower
     * 2x2) positive definite.
     */
    static boolean posDef(double[] c) {
        for (int k = 0; k < 9; k++) if (!Double.isFinite(c[k])) return false;
        double cxx = c[4], cxy = c[5], cyy = c[8];
        return c[0] > 0.0 && cxx > 0.0 && cyy > 0.0 && cxx * cyy - cxy * cxy > 0.0;
    }

    /** sqrt of the larger eigenvalue of [[cxx, cxy], [cxy, cyy]] (closed form); NaN when not finite or negative. */
    static double sigmaGMax(double cxx, double cxy, double cyy) {
        double lam = 0.5 * (cxx + cyy) + Math.hypot(0.5 * (cxx - cyy), cxy);
        return Double.isFinite(lam) && lam >= 0.0 ? Math.sqrt(lam) : Double.NaN;
    }
}
