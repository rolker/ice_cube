package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.util.ArrayList;
import java.util.List;
import java.util.Random;

import org.junit.Test;

/**
 * Synthetic calibrations: a 1.8 m pole on a tip at a made-up origin on the equator, the
 * phone mounted with its pole axis 3° off its own +y axis, epochs at 1 Hz (#35).
 */
public class PoleCalibrationTest {
    private static final long S = 1_000_000_000L;
    private static final double L = 1.8;
    private static final LocalFrame F = new LocalFrame(0, 0, 0);
    /** Phone to pole frame: the pole's z axis is the phone's +y axis tipped 3° toward +z. */
    private static final double[][] MOUNT = mul(rotX(Math.toRadians(-90 + 3)), rotZ(Math.toRadians(20)));
    /** The true pole axis in phone axes: MOUNT^T z. */
    private static final double[] AXIS = mulT(MOUNT, new double[] {0, 0, 1});

    // ---- small rotations ----

    private static double[][] rotX(double a) {
        double c = Math.cos(a), s = Math.sin(a);
        return new double[][] {{1, 0, 0}, {0, c, -s}, {0, s, c}};
    }

    private static double[][] rotZ(double a) {
        double c = Math.cos(a), s = Math.sin(a);
        return new double[][] {{c, -s, 0}, {s, c, 0}, {0, 0, 1}};
    }

    private static double[][] mul(double[][] a, double[][] b) {
        double[][] c = new double[3][3];
        for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++) for (int k = 0; k < 3; k++) c[i][j] += a[i][k] * b[k][j];
        return c;
    }

    private static double[] mul(double[][] a, double[] v) {
        double[] r = new double[3];
        for (int i = 0; i < 3; i++) for (int k = 0; k < 3; k++) r[i] += a[i][k] * v[k];
        return r;
    }

    private static double[] mulT(double[][] a, double[] v) {
        double[] r = new double[3];
        for (int i = 0; i < 3; i++) for (int k = 0; k < 3; k++) r[i] += a[k][i] * v[k];
        return r;
    }

    /** Pole frame to ENU for a pole leaning tiltDeg toward azDeg (clockwise from north), twisted twistDeg. */
    private static double[][] pole(double tiltDeg, double azDeg, double twistDeg) {
        return mul(rotZ(-Math.toRadians(azDeg)), mul(rotX(-Math.toRadians(tiltDeg)), rotZ(Math.toRadians(twistDeg))));
    }

    /** One pose: the epoch at t s and the phone's gravity ("up", 9.81 m/s², phone axes). */
    private static final class Pose {
        final Epoch epoch;
        final double[] gravity;
        Pose(Epoch epoch, double[] gravity) { this.epoch = epoch; this.gravity = gravity; }
        Pose(double t, double tiltDeg, double azDeg, double twistDeg, Random noise, boolean fixed) {
            double[][] pr = pole(tiltDeg, azDeg, twistDeg);
            double[] u = mul(pr, new double[] {0, 0, 1});
            double mm = noise == null ? 0 : 0.003;
            double[] ant = {L * u[0] + gauss(noise) * mm, L * u[1] + gauss(noise) * mm, L * u[2] + gauss(noise) * mm};
            double[] geo = F.toGeodetic(ant[0], ant[1], ant[2]);
            epoch = new Epoch(Math.round(t * S), 50_000 + t, geo[0], geo[1], geo[2], 1.0, fixed ? 4 : 5);
            // phone to ENU is pr * MOUNT; up in phone axes is its transpose times z
            double[] up = mulT(mul(pr, MOUNT), new double[] {0, 0, 9.81});
            double gn = noise == null ? 0 : 0.01;   // about 0.06°
            gravity = new double[] {up[0] + gauss(noise) * gn, up[1] + gauss(noise) * gn, up[2] + gauss(noise) * gn};
        }
    }

    private static double gauss(Random r) { return r == null ? 0 : r.nextGaussian(); }

    /** The owner's routine: still 0-3 s (at 2° toward 90°), leans 4-12 s all round, twists 13-21 s. */
    private static List<Pose> routine(Random noise, double leanArcDeg) {
        List<Pose> out = new ArrayList<>();
        for (int t = 0; t <= 3; t++) out.add(new Pose(t, 2, 90, 0, noise, true));
        for (int t = 4; t <= 12; t++) {
            double az = (t - 4) * leanArcDeg / 8;
            out.add(new Pose(t, 12 + 3 * Math.sin(t), az, 15 * t, noise, true));
        }
        for (int t = 13; t <= 21; t++) out.add(new Pose(t, 3, 0, (t - 13) * 90, noise, true));
        return out;
    }

    private static PoleCalibration run(List<Pose> poses) {
        PoleCalibration c = new PoleCalibration(L, new TiltSet.Gates(), 0);
        for (Pose p : poses) c.onEpoch(p.epoch, p.gravity);
        return c;
    }

    @Test public void recoversTheMountedAxis() {
        PoleCalibration c = run(routine(new Random(7), 330));
        assertEquals(c.failure(), PoleCalibration.Phase.DONE, c.phase());
        PoleAxis a = c.result();
        assertNotNull(a);
        assertTrue(TiltSolver.angleDeg(a.axis, AXIS) < 0.2);
        assertEquals(10, a.samples);   // the still sample and 9 leans
        assertEquals(9, a.twistSamples);
        assertTrue(a.residualDeg < 0.5);
        assertTrue(a.extras.isEmpty());   // #38's outputs: none yet
    }

    /** The tip auto capture latches the calibration point at: the point the pole stood on. */
    @Test public void tipIsTheCalibrationPoint() {
        PoleCalibration c = run(routine(null, 330));
        double[] tip = c.tipGeodetic();
        assertNotNull(tip);
        double[] enu = F.toEnu(tip[0], tip[1], tip[2]);
        assertEquals(0, Math.hypot(enu[0], enu[1]), 0.001);
        assertNull(new PoleCalibration(L, new TiltSet.Gates(), 0).tipGeodetic());   // not done
    }

    /** The sign convention auto capture relies on: plumb reads 0°, a 5° lean reads 5° (not 175°). */
    @Test public void plumbAngleSign() {
        PoleAxis a = run(routine(null, 330)).result();
        assertNotNull(a);
        assertEquals(0, a.plumbDeg(new Pose(0, 0, 0, 77, null, true).gravity), 0.05);
        assertEquals(5, a.plumbDeg(new Pose(0, 5, 200, 10, null, true).gravity), 0.05);
        assertTrue(Double.isNaN(a.plumbDeg(null)));
    }

    @Test public void phasesRunInOrder() {
        PoleCalibration c = new PoleCalibration(L, new TiltSet.Gates(), 0);
        List<Pose> poses = routine(null, 330);
        List<PoleCalibration.Change> seen = new ArrayList<>();
        for (Pose p : poses) {
            PoleCalibration.Change ch = c.onEpoch(p.epoch, p.gravity);
            if (ch != PoleCalibration.Change.NONE) seen.add(ch);
        }
        assertEquals("[LEAN, TWIST, DONE]", seen.toString());
    }

    @Test public void movingInTheStillPhaseStartsItAgain() {
        PoleCalibration c = new PoleCalibration(L, new TiltSet.Gates(), 0);
        c.onEpoch(new Pose(0, 2, 90, 0, null, true).epoch, new Pose(0, 2, 90, 0, null, true).gravity);
        c.onEpoch(new Pose(1, 2, 90, 0, null, true).epoch, new Pose(1, 2, 90, 0, null, true).gravity);
        Pose moved = new Pose(2, 8, 90, 0, null, true);
        assertEquals(PoleCalibration.Change.RESTART, c.onEpoch(moved.epoch, moved.gravity));
        assertEquals(PoleCalibration.STILL_S, c.remainingS(moved.epoch.elapsedNs), 1e-9);
        assertEquals(PoleCalibration.Phase.STILL, c.phase());
    }

    @Test public void neverStillFails() {
        PoleCalibration c = new PoleCalibration(L, new TiltSet.Gates(), 0);
        PoleCalibration.Change last = PoleCalibration.Change.NONE;
        for (int t = 0; t <= 40 && last != PoleCalibration.Change.FAILED; t++) {
            Pose p = new Pose(t, 10, t * 40, 0, null, true);
            last = c.onEpoch(p.epoch, p.gravity);
        }
        assertEquals(PoleCalibration.Phase.FAILED, c.phase());
        assertTrue(c.failure(), c.failure().contains("still"));
    }

    @Test public void tooFewFixedSamplesIsRefused() {
        List<Pose> poses = routine(null, 330);
        List<Pose> some = new ArrayList<>();
        for (Pose p : poses) {
            double t = p.epoch.elapsedNs / 1e9;
            boolean floatLean = t >= 6 && t <= 12;   // only leans at 4 and 5 s are fixed
            some.add(floatLean ? new Pose(t, 12, 0, 0, null, false) : p);
        }
        PoleCalibration c = run(some);
        assertEquals(PoleCalibration.Phase.FAILED, c.phase());
        assertTrue(c.failure(), c.failure().contains("3 RTK fixed samples"));
        assertNull(c.result());
    }

    @Test public void leansOnOneSideAreRefused() {
        PoleCalibration c = run(routine(null, 90));   // azimuths 0 to 90°
        assertEquals(PoleCalibration.Phase.FAILED, c.phase());
        assertTrue(c.failure(), c.failure().contains("cover"));
    }

    @Test public void aLoosePhoneIsRefused() {
        List<Pose> poses = routine(null, 330);
        List<Pose> bad = new ArrayList<>();
        for (Pose p : poses) {
            double t = p.epoch.elapsedNs / 1e9;
            if (t >= 4 && t <= 12 && ((int) t) % 2 == 0) {
                // the phone slipped 4° about its x axis for every other lean
                bad.add(new Pose(p.epoch, mul(rotX(Math.toRadians(4)), p.gravity)));
            } else {
                bad.add(p);
            }
        }
        PoleCalibration c = run(bad);
        assertEquals(PoleCalibration.Phase.FAILED, c.phase());
        assertTrue(c.failure(), c.failure().contains("loose"));
    }

    @Test public void twistSamplesAreNotUsedForTheAxis() {
        List<Pose> poses = routine(new Random(3), 330);
        PoleCalibration ok = run(poses);
        List<Pose> junk = new ArrayList<>();
        for (Pose p : poses) {
            double t = p.epoch.elapsedNs / 1e9;
            junk.add(t >= 13 ? new Pose(p.epoch, new double[] {9.81, 0, 0}) : p);
        }
        PoleCalibration c = run(junk);
        assertEquals(PoleCalibration.Phase.DONE, c.phase());
        assertEquals(0, TiltSolver.angleDeg(ok.result().axis, c.result().axis), 1e-9);
    }
}
