package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.ArrayList;
import java.util.List;
import java.util.Random;

public class TiltSetTest {
    static final long S = 1_000_000_000L;
    static final double L = 1.6;
    static final LocalFrame F = new LocalFrame(10, 20, 100);   // made-up origin
    static final double[] TIP = {2.0, 3.0, -1.0};
    static final long MIDNIGHT = 1_794_355_200_000L;

    /** A 1 s average of 4 epochs at the antenna position for this lean, with scatter sd (m). */
    static EpochAverage avg(double tilt, double az, double sd, double[] slip, Random rnd, int q) {
        double[] u = TiltSolverTest.pole(tilt, az);
        List<Epoch> es = new ArrayList<>();
        for (int i = 0; i < 4; i++) {
            double[] g = F.toGeodetic(TIP[0] + slip[0] + L * u[0] + sd * rnd.nextGaussian(),
                    TIP[1] + slip[1] + L * u[1] + sd * rnd.nextGaussian(), TIP[2] + L * u[2] + sd * rnd.nextGaussian());
            es.add(new Epoch(i * S / 4, 50_000 + i * 0.25, g[0], g[1], g[2], 1.0, q));
        }
        return EpochAverage.of(es);
    }

    static TiltSet.Added lean(TiltSet t, double tilt, double az, double twist, double[] slip, Random rnd, FixGuard.Stretch s) {
        EpochAverage a = avg(tilt, az, 0.002, slip, rnd, 4);
        return t.add(a, TiltSolverTest.gravity(TiltSolverTest.pole(tilt, az), twist), 100, s, 20 * S, MIDNIGHT + 50_000_000L);
    }

    static final double[] NO_SLIP = {0, 0};

    @Test public void goodSetAllAroundPassesTheGates() {
        Random rnd = new Random(2);
        TiltSet t = new TiltSet(PointRow.OBJECT, L, new TiltSet.Gates(), 0);
        double[] tilts = {18, 22, 16, 20, 24}, az = {0, 75, 150, 220, 290};
        for (int i = 0; i < 5; i++) {
            TiltSet.Added a = lean(t, tilts[i], az[i], 25 * i, NO_SLIP, rnd, null);
            assertTrue(a.text, a.ok);
            if (i < 2) assertTrue(a.text, a.text.contains("need 3"));
            else assertNotNull(t.provisional());
        }
        TiltSet.Result r = t.finish();
        assertTrue(r.ok());
        assertTrue(r.warnings.toString(), r.warnings.isEmpty());
        assertTrue(r.dropped.isEmpty());
        double[] tip = F.toEnu(r.tipGeodetic[0], r.tipGeodetic[1], r.tipGeodetic[2]);
        for (int k = 0; k < 3; k++) assertEquals(TIP[k], tip[k], 0.01);

        PointRow row = t.toRow(12, 99, r, MIDNIGHT + 50_000_000L);
        assertEquals("object", row.cls);
        assertEquals(PointRow.TILT, row.method);
        assertEquals(5, row.tiltN);
        assertEquals(L, row.pole, 0);
        assertEquals(4, row.fix);
        assertEquals(20, row.nEpochs);
        assertEquals(r.tipGeodetic[2], row.hGround, 1e-9);
        assertTrue(row.hAnt > row.hGround + 1.4);   // mean antenna height, not tip + pole
        assertTrue(row.tiltCover > 270);
        assertFalse(row.fixSuspect);
        assertEquals(PointRow.COLUMNS, PointRow.split(row.toCsv()).size());

        List<String> obs = t.obsRows(3, 12);
        assertEquals(5, obs.size());
        int cols = PointRow.split(TiltSet.OBS_HEADER).size();
        for (String o : obs) {
            List<String> f = PointRow.split(o);
            assertEquals(o, cols, f.size());
            assertEquals("3", f.get(0));
            assertEquals("12", f.get(1));
            assertEquals("1", f.get(cols - 2));   // used
        }
        assertEquals("2", PointRow.split(obs.get(1)).get(2));
    }

    @Test public void refusesMovingFloatAndOutOfRangeLeans() {
        Random rnd = new Random(4);
        TiltSet t = new TiltSet(PointRow.SURFACE, L, new TiltSet.Gates(), 0);
        TiltSet.Added moving = t.add(avg(20, 0, 0.03, NO_SLIP, rnd, 4), null, 0, null, S, MIDNIGHT);
        assertFalse(moving.ok);
        assertTrue(moving.text, moving.text.contains("moving"));
        assertFalse(t.add(avg(20, 0, 0.002, NO_SLIP, rnd, 5), null, 0, null, S, MIDNIGHT).ok);
        assertFalse(t.add(null, null, 0, null, S, MIDNIGHT).ok);
        assertEquals(0, t.count());
        assertTrue(lean(t, 20, 0, 0, NO_SLIP, rnd, null).ok);
        assertTrue(lean(t, 20, 120, 0, NO_SLIP, rnd, null).ok);
        assertTrue(lean(t, 20, 240, 0, NO_SLIP, rnd, null).ok);
        TiltSet.Added vertical = lean(t, 2, 90, 0, NO_SLIP, rnd, null);
        assertFalse(vertical.ok);
        assertTrue(vertical.text, vertical.text.contains("tilt 2°"));
        TiltSet.Added steep = lean(t, 40, 90, 0, NO_SLIP, rnd, null);
        assertFalse(steep.text, steep.ok);
        assertEquals(3, t.count());
        TiltSet.Result r = t.finish();
        assertFalse(r.ok());
        assertTrue(r.warnings.get(0).contains("need 4"));
    }

    @Test public void oneSidedSetWarnsAboutCoverageAndSigma() {
        Random rnd = new Random(8);
        TiltSet t = new TiltSet(PointRow.OBJECT, L, new TiltSet.Gates(), 0);
        double[] az = {20, 40, 70, 100, 120};
        for (int i = 0; i < az.length; i++) assertTrue(lean(t, 15 + i, az[i], 0, NO_SLIP, rnd, null).ok);
        TiltSet.Result r = t.finish();
        assertTrue(r.ok());
        String w = r.warnings.toString();
        assertTrue(w, w.contains("coverage"));
        // a tighter RMS gate also warns
        TiltSet.Gates g = new TiltSet.Gates();
        g.maxRms = 0.0001;
        g.maxSigma = 0.001;
        TiltSet t2 = new TiltSet(PointRow.OBJECT, L, g, 0);
        for (int i = 0; i < az.length; i++) lean(t2, 15 + i, az[i] * 3, 0, NO_SLIP, rnd, null);
        String w2 = t2.finish().warnings.toString();
        assertTrue(w2, w2.contains("RMS") && w2.contains("sigma"));
    }

    @Test public void slippedLeanIsDroppedAndTheSetResolved() {
        Random rnd = new Random(6);
        TiltSet t = new TiltSet(PointRow.REF, L, new TiltSet.Gates(), 0);
        double[] tilts = {18, 22, 25, 20, 24, 17}, az = {0, 60, 130, 190, 250, 310};
        for (int i = 0; i < 6; i++) {
            double[] slip = i == 2 ? new double[] {0.15, -0.05} : NO_SLIP;
            assertTrue(lean(t, tilts[i], az[i], 30 * i, slip, rnd, null).ok);
        }
        TiltSet.Result r = t.finish();
        assertTrue(r.ok());
        assertEquals(1, r.dropped.size());
        assertEquals(3, r.dropped.get(0).k);
        assertTrue(r.dropped.get(0).whyDropped.startsWith("IMU"));
        assertEquals(5, r.used.size());
        double[] tip = F.toEnu(r.tipGeodetic[0], r.tipGeodetic[1], r.tipGeodetic[2]);
        for (int k = 0; k < 3; k++) assertEquals(TIP[k], tip[k], 0.015);
        List<String> obs = t.obsRows(1, 7);
        List<String> f = PointRow.split(obs.get(2));
        assertEquals("0", f.get(f.size() - 2));
        assertTrue(f.get(f.size() - 1).startsWith("IMU"));
    }

    @Test public void suspectStretchIsCarriedToTheRowAndWarned() {
        Random rnd = new Random(9);
        FixGuard g = new FixGuard(0, 1.0);
        g.onEpoch(new Epoch(0, 0, 10, 20, 100, 1, 5));
        double[] p = F.toGeodetic(1.5, 0, 0);
        FixGuard.Stretch s = g.onEpoch(new Epoch(S / 4, 0, p[0], p[1], p[2], 1, 4));
        assertTrue(s.suspect);
        TiltSet t = new TiltSet(PointRow.SURFACE, L, new TiltSet.Gates(), 0);
        double[] az = {0, 90, 180, 270};
        for (double a : az) assertTrue(lean(t, 20, a, 0, NO_SLIP, rnd, s).ok);
        TiltSet.Result r = t.finish();
        assertTrue(r.warnings.toString(), r.warnings.toString().contains("fix may be false (jumped 1.5 m)"));
        PointRow row = t.toRow(1, 1, r, MIDNIGHT);
        assertTrue(row.fixSuspect);
        assertEquals(1.5, row.fixJump, 0.01);
    }

    @Test public void cancelledSetObsRowsHaveNoPointAndAreUnused() {
        Random rnd = new Random(1);
        TiltSet t = new TiltSet(PointRow.SURFACE, L, new TiltSet.Gates(), 0);
        lean(t, 20, 0, 0, NO_SLIP, rnd, null);
        List<String> f = PointRow.split(t.obsRows(2, 0).get(0));
        assertEquals("", f.get(1));
        assertEquals("0", f.get(f.size() - 2));
        assertEquals("", f.get(f.size() - 1));
    }
}
