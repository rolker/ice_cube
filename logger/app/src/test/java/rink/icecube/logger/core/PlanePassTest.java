package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/** PlanePass's gates by hand (nodes.plane_fit's rules; the parity is in CoverageTest). */
public class PlanePassTest {
    private static final double INF = Double.POSITIVE_INFINITY;

    /** The 8 neighbours of a 0.5 m grid at their grid offsets, z on `a + gx X + gy Y` plus `bump`. */
    private static final class Nbrs {
        final double[] x = new double[8], y = new double[8], z = new double[8], w = new double[8];
        final boolean[] red = new boolean[8];
        int m;

        Nbrs(double a, double gx, double gy, double[] bump) {
            for (int dj = -1; dj <= 1; dj++) {
                for (int di = -1; di <= 1; di++) {
                    if (di == 0 && dj == 0) continue;
                    x[m] = 0.5 * di; y[m] = 0.5 * dj;
                    z[m] = a + gx * x[m] + gy * y[m] + (bump == null ? 0 : bump[m]);
                    w[m] = 2500;   // 2 cm
                    m++;
                }
            }
        }

        int fit(PlanePass.Fit f) { return fit(f, PlanePass.PLANARITY, PlanePass.MAX_SIGMA_G); }

        int fit(PlanePass.Fit f, double planarity, double maxSigmaG) {
            return PlanePass.fit(x, y, z, w, red, m, PlanePass.MIN_NEIGHBOURS, PlanePass.MIN_CLEAR, PlanePass.RED_WEIGHT,
                    planarity, maxSigmaG, f);
        }
    }

    @Test public void anExactPlaneIsRecovered() {
        PlanePass.Fit f = new PlanePass.Fit();
        assertEquals(0, new Nbrs(0.1, 0.3, -0.2, null).fit(f));
        assertEquals(0.1, f.a, 1e-12);
        assertEquals(0.3, f.gx, 1e-12);
        assertEquals(-0.2, f.gy, 1e-12);
        assertEquals(0.0, f.chi, 1e-9);
        // 8 neighbours at w = 2500 on the 3x3 ring: var(gx) = 1 / sum w X^2 = 1 / (6 * 0.25 * 2500)
        assertEquals(1.0 / 3750, f.cxx, 1e-15);
        assertEquals(0.0, f.cxy, 1e-15);
        assertEquals(Math.sqrt(1.0 / 3750), f.sigmaG, 1e-12);
        assertEquals(1.0 / 20000, f.varA, 1e-15);
    }

    @Test public void tooFewAndTooFewClear() {
        PlanePass.Fit f = new PlanePass.Fit();
        Nbrs n = new Nbrs(0, 0.3, 0, null);
        n.m = 4;
        assertEquals(PlanePass.FEW, n.fit(f));
        n.m = 8;
        for (int k = 0; k < 4; k++) n.w[k] = 0;     // a zero weight does not count
        assertEquals(PlanePass.FEW, n.fit(f));
        n = new Nbrs(0, 0.3, 0, null);
        for (int k = 0; k < 7; k++) n.red[k] = true;
        assertEquals(PlanePass.FEW_CLEAR, n.fit(f));
        n.red[6] = false;
        assertEquals(0, n.fit(f));
        // four usable is never enough for a plane with a residual, whatever minNeighbours says
        n = new Nbrs(0, 0.3, 0, null);
        n.m = 3;
        assertEquals(PlanePass.FEW, PlanePass.fit(n.x, n.y, n.z, n.w, n.red, n.m, 0, 0, 0.25, 3, 0.08, f));
    }

    @Test public void collinearCentroidsAreIllConditioned() {
        PlanePass.Fit f = new PlanePass.Fit();
        Nbrs n = new Nbrs(0, 0.3, 0, null);
        for (int k = 0; k < n.m; k++) n.y[k] = 0;     // every centroid on Y = 0: A'WA singular
        assertEquals(PlanePass.ILL_CONDITIONED, n.fit(f));
        assertEquals(INF, f.sigmaG, 0);
        assertTrue(Double.isNaN(f.chi));
        // also with the conditioning gate switched off
        assertEquals(PlanePass.ILL_CONDITIONED, n.fit(f, INF, INF));
    }

    @Test public void bunchedCentroidsFailTheBoundAndNaNFailsIt() {
        PlanePass.Fit f = new PlanePass.Fit();
        Nbrs n = new Nbrs(0, 0.3, 0, null);
        for (int k = 0; k < n.m; k++) n.y[k] *= 0.02;   // spread 1 cm in Y: sigma_g along Y large
        assertEquals(PlanePass.ILL_CONDITIONED, n.fit(f));
        assertTrue(f.sigmaG > PlanePass.MAX_SIGMA_G && Double.isFinite(f.sigmaG));
        assertEquals(0, n.fit(f, 3, INF));                                  // the gate off
        assertEquals(PlanePass.ILL_CONDITIONED, new Nbrs(0, 0.3, 0, null).fit(f, 3, Double.NaN));   // a NaN limit fails
    }

    @Test public void planarityGate() {
        PlanePass.Fit f = new PlanePass.Fit();
        double[] ridge = {0.1, -0.1, 0.1, 0.1, 0.1, 0.1, -0.1, 0.1};
        Nbrs n = new Nbrs(0, 0.3, 0, ridge);
        assertEquals(PlanePass.NOT_PLANAR, n.fit(f));
        assertTrue(f.chi > 3);
        assertTrue(Double.isFinite(f.sigmaG));      // the unscaled sigma_g it passed
        assertEquals(PlanePass.NOT_PLANAR, n.fit(f, Double.NaN, PlanePass.MAX_SIGMA_G));
        // with planarity off, chi^2 scales the covariance past the conditioning bound
        assertEquals(PlanePass.ILL_CONDITIONED, n.fit(f, INF, PlanePass.MAX_SIGMA_G));
        assertTrue(f.sigmaG > PlanePass.MAX_SIGMA_G);
        assertEquals(0, n.fit(f, INF, INF));
        assertEquals(f.cxx, 1.0 / 3750 * f.chi * f.chi, 1e-12);
    }

    @Test public void aNaNOrInfiniteChiFails() {
        PlanePass.Fit f = new PlanePass.Fit();
        double[] nan = new double[8];
        nan[3] = Double.NaN;
        assertEquals(PlanePass.NOT_PLANAR, new Nbrs(0, 0.3, 0, nan).fit(f, INF, INF));
        assertTrue(Double.isNaN(f.chi));
        double[] huge = {1e300, -1e300, 1e300, -1e300, 1e300, -1e300, 1e300, -1e300};
        assertEquals(PlanePass.NOT_PLANAR, new Nbrs(0, 0.3, 0, huge).fit(f, INF, INF));
        assertEquals(INF, f.chi, 0);
    }

    @Test public void aScaledCovarianceThatOverflowsIsNotPositiveDefinite() {
        // tiny weights (a huge unscaled covariance, passed with the gate off) times a huge but
        // finite chi^2: the scaled covariance is infinite
        PlanePass.Fit f = new PlanePass.Fit();
        double[] b = {1e200, -1e200, 1e200, -1e200, -1e200, 1e200, -1e200, 1e200};
        Nbrs n = new Nbrs(0, 0, 0, b);
        for (int k = 0; k < n.m; k++) n.w[k] = 1e-100;
        assertEquals(PlanePass.ILL_CONDITIONED, n.fit(f, INF, INF));
        assertEquals(INF, f.sigmaG, 0);
        assertTrue(Double.isFinite(f.chi));
    }

    @Test public void redNeighboursWeighQuarter() {
        PlanePass.Fit a = new PlanePass.Fit(), b = new PlanePass.Fit();
        double[] bump = {0, 0, 0.02, 0, 0, 0, 0, 0};
        Nbrs red = new Nbrs(0, 0.3, 0.1, bump), scaled = new Nbrs(0, 0.3, 0.1, bump);
        red.red[2] = true;
        scaled.w[2] *= PlanePass.RED_WEIGHT;
        assertEquals(0, red.fit(a));
        assertEquals(0, scaled.fit(b));
        assertEquals(b.a, a.a, 1e-15);
        assertEquals(b.gx, a.gx, 1e-15);
        assertEquals(b.gy, a.gy, 1e-15);
        assertEquals(b.chi, a.chi, 1e-12);
        Nbrs clear = new Nbrs(0, 0.3, 0.1, bump);
        PlanePass.Fit c = new PlanePass.Fit();
        clear.fit(c);
        assertFalse(c.gx == a.gx);    // full weight pulls the plane further toward the bump
    }

    @Test public void significanceFailsClosed() {
        PlanePass.Fit f = new PlanePass.Fit();
        assertEquals(0, new Nbrs(0, 0.3, 0, null).fit(f));
        assertEquals(0, PlanePass.significance(f, Coverage.SLOPE, Coverage.K_SIGMA));
        assertEquals(0, new Nbrs(0, 0.04, 0, null).fit(f));
        assertEquals(PlanePass.NOT_STEEP, PlanePass.significance(f, Coverage.SLOPE, Coverage.K_SIGMA));
        // a directional variance that is not positive fails as ill-conditioned, not as 0
        assertEquals(0, new Nbrs(0, 0.3, 0, null).fit(f));
        f.cxx = 0;
        assertEquals(PlanePass.ILL_CONDITIONED, PlanePass.significance(f, Coverage.SLOPE, Coverage.K_SIGMA));
        f.cxx = Double.NaN;
        assertEquals(PlanePass.ILL_CONDITIONED, PlanePass.significance(f, Coverage.SLOPE, Coverage.K_SIGMA));
    }

    @Test public void sigmaGIsTheLargerEigenvalue() {
        assertEquals(2.0, PlanePass.sigmaGMax(4, 0, 1), 1e-15);
        assertEquals(Math.sqrt(3.0), PlanePass.sigmaGMax(2, 1, 2), 1e-15);
        assertTrue(Double.isNaN(PlanePass.sigmaGMax(INF, 0, 1)));
        assertTrue(Double.isNaN(PlanePass.sigmaGMax(-1, 0, -1)));
    }
}
