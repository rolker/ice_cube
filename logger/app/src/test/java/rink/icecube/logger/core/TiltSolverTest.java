package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Random;

public class TiltSolverTest {
    static final double L = 1.6;
    static final double[] TIP = {12.3, -4.5, -1.2};
    /** The pole axis in phone axes, made up (not along a phone axis on purpose). */
    static final double[] AXIS = unit(new double[] {0.15, 0.95, 0.25});

    static double[] unit(double[] a) {
        double n = Math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]);
        return new double[] {a[0] / n, a[1] / n, a[2] / n};
    }

    /** Pole direction (tip to antenna) for a tilt and azimuth in degrees. */
    static double[] pole(double tiltDeg, double azDeg) {
        double t = Math.toRadians(tiltDeg), a = Math.toRadians(azDeg);
        return new double[] {Math.sin(t) * Math.sin(a), Math.sin(t) * Math.cos(a), Math.cos(t)};
    }

    /** Rotation matrix about unit axis k by angle (rad). */
    static double[][] rot(double[] k, double ang) {
        double c = Math.cos(ang), s = Math.sin(ang), v = 1 - c;
        return new double[][] {
                {k[0] * k[0] * v + c, k[0] * k[1] * v - k[2] * s, k[0] * k[2] * v + k[1] * s},
                {k[1] * k[0] * v + k[2] * s, k[1] * k[1] * v + c, k[1] * k[2] * v - k[0] * s},
                {k[2] * k[0] * v - k[1] * s, k[2] * k[1] * v + k[0] * s, k[2] * k[2] * v + c}};
    }

    static double[] mul(double[][] m, double[] x) {
        return new double[] {m[0][0] * x[0] + m[0][1] * x[1] + m[0][2] * x[2], m[1][0] * x[0] + m[1][1] * x[1] + m[1][2] * x[2],
                m[2][0] * x[0] + m[2][1] * x[1] + m[2][2] * x[2]};
    }

    static double[] mulT(double[][] m, double[] x) {
        return new double[] {m[0][0] * x[0] + m[1][0] * x[1] + m[2][0] * x[2], m[0][1] * x[0] + m[1][1] * x[1] + m[2][1] * x[2],
                m[0][2] * x[0] + m[1][2] * x[1] + m[2][2] * x[2]};
    }

    static double[][] matmul(double[][] a, double[][] b) {
        double[][] c = new double[3][3];
        for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++) for (int k = 0; k < 3; k++) c[i][j] += a[i][k] * b[k][j];
        return c;
    }

    /**
     * The phone's gravity ("up", 9.81 m/s^2) in phone axes when the pole points along u
     * (world ENU) and the phone is twisted by twistDeg about the pole: the phone-to-world
     * rotation maps AXIS to u.
     */
    static double[] gravity(double[] u, double twistDeg) {
        double[] k = TiltSolver.cross(AXIS, u);
        double s = TiltSolver.norm(k), c = TiltSolver.dot(AXIS, u);
        double[][] toU = s < 1e-12 ? rot(new double[] {1, 0, 0}, 0) : rot(unit(k), Math.atan2(s, c));
        double[][] r = matmul(toU, rot(AXIS, Math.toRadians(twistDeg)));
        assertEquals(0, TiltSolver.angleDeg(mul(r, AXIS), u), 1e-6);
        double[] g = mulT(r, new double[] {0, 0, 1});
        return new double[] {9.81 * g[0], 9.81 * g[1], 9.81 * g[2]};
    }

    /** Synthetic leans: tilts and azimuths in degrees, noise sd in metres. */
    static List<double[]> leans(double[] tilts, double[] az, double noise, Random rnd) {
        List<double[]> p = new ArrayList<>();
        for (int i = 0; i < tilts.length; i++) {
            double[] u = pole(tilts[i], az[i]);
            p.add(new double[] {TIP[0] + L * u[0] + noise * rnd.nextGaussian(), TIP[1] + L * u[1] + noise * rnd.nextGaussian(),
                    TIP[2] + L * u[2] + noise * rnd.nextGaussian()});
        }
        return p;
    }

    @Test public void recoversTheTipFromLeansAllAround() {
        Random rnd = new Random(7);
        for (int n = 4; n <= 8; n++) {
            List<Double> errs = new ArrayList<>();
            for (int trial = 0; trial < 20; trial++) {
                double[] t = new double[n], a = new double[n];
                double start = rnd.nextDouble() * 360;
                for (int i = 0; i < n; i++) {
                    t[i] = 15 + 10 * rnd.nextDouble();
                    a[i] = start + i * 360.0 / n + 20 * (rnd.nextDouble() - 0.5);
                }
                TiltSolver.Solution s = TiltSolver.solve(leans(t, a, 0.01, rnd), L);
                assertNotNull(s);
                double err = Math.sqrt(Math.pow(s.tip[0] - TIP[0], 2) + Math.pow(s.tip[1] - TIP[1], 2)
                        + Math.pow(s.tip[2] - TIP[2], 2));
                errs.add(err);
                // within 4 of its own reported sigmas
                double sd = Math.sqrt(s.sd[0] * s.sd[0] + s.sd[1] * s.sd[1] + s.sd[2] * s.sd[2]);
                assertTrue("n=" + n + " err=" + err + " sd=" + sd, err < 4 * sd);
                assertTrue(s.coverageDeg > 180);
                for (int i = 0; i < n; i++) assertEquals(t[i], s.tiltDeg[i], 3);
            }
            java.util.Collections.sort(errs);
            double median = errs.get(errs.size() / 2);
            // 1 cm noise per axis per lean: about 3 cm with 4 leans, 2 cm with 5 to 8 (each err also within 4 sigma)
            assertTrue("n=" + n + " median " + median, median < (n == 4 ? 0.035 : 0.025));
        }
    }

    @Test public void exactLeansGiveExactTipTiltsAndAzimuths() {
        double[] t = {15, 20, 25, 18}, a = {10, 100, 200, 290};
        TiltSolver.Solution s = TiltSolver.solve(leans(t, a, 0, new Random(1)), L);
        for (int k = 0; k < 3; k++) assertEquals(TIP[k], s.tip[k], 1e-6);
        for (int i = 0; i < 4; i++) {
            assertEquals(t[i], s.tiltDeg[i], 1e-5);
            assertEquals(a[i], s.azimuthDeg[i], 1e-5);
            assertEquals(0, s.resid[i], 1e-7);
        }
        assertEquals(0, s.rms, 1e-7);
        // the 1 cm floor keeps the sigmas honest even for a perfect fit
        assertTrue(s.sd[0] > 0.005 && s.sd[1] > 0.005 && s.sd[2] > 0.001);
        assertEquals(360 - 100, s.coverageDeg, 1e-4);   // biggest gap 200 -> 290 -> 10 is 100
    }

    @Test public void oneSidedLeansGiveALargerSigmaAcrossThatSide() {
        // leans only toward the north-east quarter: east and north poorly determined
        double[] t = {18, 20, 22, 19, 21}, a = {10, 30, 50, 70, 80};
        Random rnd = new Random(3);
        TiltSolver.Solution one = TiltSolver.solve(leans(t, a, 0.01, rnd), L);
        double[] a2 = {10, 80, 150, 220, 290};
        TiltSolver.Solution all = TiltSolver.solve(leans(t, a2, 0.01, rnd), L);
        assertTrue(one.coverageDeg < 90);
        assertTrue(all.coverageDeg > 270);
        double hOne = Math.hypot(one.sd[0], one.sd[1]), hAll = Math.hypot(all.sd[0], all.sd[1]);
        assertTrue("one-sided " + hOne + " all-round " + hAll, hOne > 3 * hAll);
        // the weak direction is along the mean lean (azimuth ~45 deg): sd east and north both grow
        assertTrue(one.sd[0] > 2 * all.sd[0] && one.sd[1] > 2 * all.sd[1]);
    }

    @Test public void coverage() {
        assertEquals(0, TiltSolver.coverage(new double[] {}), 0);
        assertEquals(0, TiltSolver.coverage(new double[] {42}), 0);
        assertEquals(90, TiltSolver.coverage(new double[] {0, 90}), 1e-9);
        assertEquals(90, TiltSolver.coverage(new double[] {350, 80}), 1e-9);   // across north
        assertEquals(270, TiltSolver.coverage(new double[] {0, 90, 180, 270}), 1e-9);
        assertEquals(240, TiltSolver.coverage(new double[] {-30, 30, 90, 150, 210}), 1e-9);   // gap 210 -> 330
        assertEquals(45, TiltSolver.azimuth(1, 1), 1e-9);
        assertEquals(270, TiltSolver.azimuth(-1, 0), 1e-9);
    }

    @Test public void imuAgreesForAGoodSetEvenWithTwist() {
        double[] t = {15, 20, 25, 18, 22, 17}, a = {0, 60, 130, 190, 250, 310}, twist = {0, 40, -70, 120, 10, -150};
        Random rnd = new Random(11);
        List<double[]> p = leans(t, a, 0.005, rnd), g = new ArrayList<>();
        for (int i = 0; i < t.length; i++) g.add(gravity(pole(t[i], a[i]), twist[i]));
        double[] d = TiltSolver.imuCheck(p, g, L).disagreement;
        for (double x : d) assertTrue(Arrays.toString(d), x < 1.5);
        // pairwise gravity angles do not match the pole angles once the phone is twisted:
        // the reason the check fits the pole axis instead
        double pairG = TiltSolver.angleDeg(g.get(0), g.get(3)), pairU = TiltSolver.angleDeg(pole(t[0], a[0]), pole(t[3], a[3]));
        assertTrue(Math.abs(pairG - pairU) > 3);
        // the fitted axis is the true one
        double[] tilts = TiltSolver.solve(p, L).tiltDeg;
        assertEquals(0, TiltSolver.angleDeg(TiltSolver.fitAxis(g, tilts), AXIS), 1.0);
    }

    @Test public void imuFlagsASlippedLean() {
        double[] t = {15, 20, 25, 18, 22, 17}, a = {0, 60, 130, 190, 250, 310};
        Random rnd = new Random(5);
        List<double[]> p = leans(t, a, 0.005, rnd), g = new ArrayList<>();
        for (int i = 0; i < t.length; i++) g.add(gravity(pole(t[i], a[i]), 30 * i));
        // lean 3 (index 2): the tip slipped 15 cm sideways while the antenna was read
        double[] u = pole(t[2], a[2]);
        p.set(2, new double[] {TIP[0] + 0.15 + L * u[0], TIP[1] - 0.05 + L * u[1], TIP[2] + L * u[2]});
        TiltSolver.Imu imu = TiltSolver.imuCheck(p, g, L);
        double[] d = imu.disagreement;
        assertEquals(Arrays.toString(imu.restRms), 2, imu.culprit());
        assertTrue(Arrays.toString(d), d[2] > 3);
        assertTrue(imu.max() > 3);
        // without the slipped lean the rest agree
        assertTrue(Arrays.toString(imu.restRms), imu.restRms[2] < 1);
    }

    @Test public void imuNeedsFourLeansWithGravity() {
        double[] t = {15, 20, 25, 18}, a = {0, 90, 180, 270};
        List<double[]> p = leans(t, a, 0, new Random(1));
        List<double[]> g = new ArrayList<>();
        for (int i = 0; i < 4; i++) g.add(i == 0 ? null : gravity(pole(t[i], a[i]), 0));
        for (double x : TiltSolver.imuCheck(p, g, L).disagreement) assertTrue(Double.isNaN(x));
    }

    @Test public void needsThreeLeans() {
        assertEquals(null, TiltSolver.solve(leans(new double[] {15, 20}, new double[] {0, 180}, 0, new Random(1)), L));
    }

    /**
     * The first house-corner set (local ENU metres about an arbitrary origin, not coordinates):
     * five leans with the tip on the corner, pole 1.6 m, leaning over only ~145 degrees.
     */
    @Test public void houseCornerSet() {
        List<double[]> p = Arrays.asList(new double[] {7.011, -3.085, -2.340}, new double[] {7.025, -3.100, -2.318},
                new double[] {7.304, -3.479, -2.364}, new double[] {6.553, -2.994, -2.380},
                new double[] {6.454, -3.097, -2.356});
        TiltSolver.Solution s = TiltSolver.solve(p, 1.6);
        assertNotNull(s);
        assertEquals(6.823, s.tip[0], 0.002);
        assertEquals(-3.426, s.tip[1], 0.002);
        assertEquals(-3.885, s.tip[2], 0.002);
        assertTrue(s.maxAbsResid() < 0.014);
        assertEquals(145, s.coverageDeg, 1);
        for (double t : s.tiltDeg) assertTrue(t > 13 && t < 19);
        // north is the weak axis (about 9 cm), east about 5, up about 2
        assertEquals(0.091, s.sd[1], 0.005);
        assertEquals(0.048, s.sd[0], 0.005);
        assertTrue(s.sd[2] < 0.025);
        assertTrue(s.sd[1] > s.sd[0] && s.sd[0] > s.sd[2]);
    }
}
