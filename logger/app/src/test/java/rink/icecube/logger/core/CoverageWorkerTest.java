package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/** The coverage worker's ordering and cancellation, on a hand-run queue or a real thread (#6 review). */
public class CoverageWorkerTest {
    private static final LocalFrame F = new LocalFrame(0, 0, 0);   // made-up origin at the equator
    private static final Coverage.Params P = Coverage.Params.defaults();

    /** A worker queue the test runs by hand. */
    private static final class Queue implements java.util.concurrent.Executor {
        final ArrayDeque<Runnable> q = new ArrayDeque<>();
        @Override public void execute(Runnable r) { q.add(r); }
        void runAll() { while (!q.isEmpty()) q.poll().run(); }
    }

    /** The UI side: keeps frames of the current generation, as MainActivity does. */
    private static final class Ui {
        final List<CoverageRaster.Frame> got = new ArrayList<>();
        final CoverageScreen screen = new CoverageScreen();
        CoverageWorker w;
        synchronized void take(CoverageRaster.Frame f) { got.add(f); }
        synchronized void drain() {
            for (CoverageRaster.Frame f : got) if (f.gen == w.generation()) screen.apply(f);
            got.clear();
        }
    }

    private static PointRow surface(double e, double n, double z) {
        double[] g = F.toGeodetic(e, n, z);
        PointRow r = new PointRow();
        r.cls = PointRow.SURFACE;
        r.method = PointRow.SINGLE;
        r.fix = 4;
        r.lat = g[0]; r.lon = g[1]; r.hGround = g[2]; r.pole = 1.8;
        r.sdU = 0.005; r.sdE = 0.004; r.sdN = 0.004; r.age = 1; r.fixAge = 200;
        return r;
    }

    private static PointRow[] walk(int n, double z) {
        PointRow[] rows = new PointRow[n];
        for (int k = 0; k < n; k++) rows[k] = surface(k * 0.6, (k % 3) * 0.4, z + 0.01 * (k % 2));
        return rows;
    }

    private static Coverage direct(Coverage.Params p, PointRow[] rows) {
        Coverage c = new Coverage(p);
        for (PointRow r : rows) c.add(r, F);
        return c;
    }

    @Test public void rebuildThenFeedsInOrderMatchDirect() {
        Queue q = new Queue();
        Ui ui = new Ui();
        CoverageWorker w = new CoverageWorker(q, ui::take, 30, 2);   // small window: it moves while walking
        ui.w = w;
        Object session = new Object();
        PointRow[] rows = walk(80, 0.2);
        assertTrue(w.update(session, F, P, Arrays.copyOf(rows, 5)));
        for (int n = 6; n <= rows.length; n++) {
            assertFalse(w.update(session, F, P, Arrays.copyOf(rows, n)));   // a feed of one row each
            if (n % 7 == 0) { q.runAll(); ui.drain(); }   // the worker sometimes lags several rows behind
        }
        assertFalse(w.update(session, F, P, rows));   // nothing new: nothing queued
        q.runAll();
        ui.drain();
        Coverage c = direct(P, rows);
        ui.screen.assertMatches(c);
        assertTrue(ui.screen.contains(CoverageRaster.box(lastTouched(rows))));
        assertTrue(ui.screen.patches > 0);
    }

    private static List<Coverage.Node> lastTouched(PointRow[] rows) {
        Coverage c = direct(P, Arrays.copyOf(rows, rows.length - 1));
        c.drainTouched();
        c.add(rows[rows.length - 1], F);
        return c.drainTouched();
    }

    @Test public void aSettingsChangeCancelsTheQueuedRebuildAndItsFeeds() {
        Queue q = new Queue();
        Ui ui = new Ui();
        CoverageWorker w = new CoverageWorker(q, ui::take);
        ui.w = w;
        Object session = new Object();
        PointRow[] rows = walk(10, 0.2);
        w.update(session, F, P, Arrays.copyOf(rows, 8));
        w.update(session, F, P, rows);                                   // feed for the first rebuild
        Coverage.Params p2 = new Coverage.Params(0.25, 0.5, 1.0, 0.03, 0.015, 0.015);
        assertTrue(w.update(session, F, p2, rows));                      // settings change: new rebuild
        int g = w.generation();
        q.runAll();
        assertEquals(1, ui.got.size());                                  // only the new rebuild's frame
        assertEquals(g, ui.got.get(0).gen);
        assertEquals(0.25, ui.got.get(0).cell, 0);
        ui.drain();
        ui.screen.assertMatches(direct(p2, rows));
    }

    @Test public void sessionOrFrameChangeOrShrinkRebuilds() {
        Queue q = new Queue();
        CoverageWorker w = new CoverageWorker(q, f -> { });
        PointRow[] rows = walk(5, 0.2);
        Object s1 = new Object(), s2 = new Object();
        assertTrue(w.update(s1, F, P, rows));
        assertFalse(w.update(s1, F, P, rows));
        assertTrue(w.update(s2, F, P, rows));
        assertTrue(w.update(s2, new LocalFrame(0, 0, 0), P, rows));      // another frame object
        LocalFrame f2 = new LocalFrame(0, 0, 0);
        w.update(s2, f2, P, rows);
        assertTrue(w.update(s2, f2, P, Arrays.copyOf(rows, 3)));
        assertEquals(5, q.q.size());   // five rebuilds queued, no feeds
    }

    @Test public void voidGenerationChangeRebuildsWithoutTheVoidedRow() {
        Queue q = new Queue();
        Ui ui = new Ui();
        CoverageWorker w = new CoverageWorker(q, ui::take, 30, 2);
        ui.w = w;
        Object session = new Object();
        PointRow[] rows = walk(8, 0.2);
        rows[3] = surface(1.8, 0.0, 0.5);                         // an outlier on row 3's spot
        assertTrue(w.update(session, F, P, rows, 0));
        assertFalse(w.update(session, F, P, rows, 0));
        q.runAll();
        ui.drain();
        ui.screen.assertMatches(direct(P, rows));
        rows[3].voided = true;                                       // flagged
        assertTrue(w.update(session, F, P, rows, 1));                // new void generation: rebuild
        assertFalse(w.update(session, F, P, rows));                  // the 4-argument form keeps it
        q.runAll();
        ui.drain();
        PointRow[] live = new PointRow[rows.length - 1];
        for (int k = 0, j = 0; k < rows.length; k++) if (k != 3) live[j++] = rows[k];
        ui.screen.assertMatches(direct(P, live));
        rows[3].voided = false;                                      // unflagged: back in
        assertTrue(w.update(session, F, P, rows, 2));
        q.runAll();
        ui.drain();
        ui.screen.assertMatches(direct(P, rows));
    }

    @Test public void clearDropsQueuedWorkAndTheNextUpdateRebuilds() {
        Queue q = new Queue();
        Ui ui = new Ui();
        CoverageWorker w = new CoverageWorker(q, ui::take);
        ui.w = w;
        Object session = new Object();
        PointRow[] rows = walk(6, 0.2);
        w.update(session, F, P, rows);
        w.clear();
        assertFalse(w.active());
        q.runAll();
        assertTrue(ui.got.isEmpty());
        assertTrue(w.update(session, F, P, rows));
        q.runAll();
        assertEquals(1, ui.got.size());
        assertTrue(ui.got.get(0).isFull());
    }

    @Test public void closeCancelsQueuedWork() throws Exception {
        ExecutorService ex = Executors.newSingleThreadExecutor();
        List<CoverageRaster.Frame> got = new ArrayList<>();
        CoverageWorker w = new CoverageWorker(ex, f -> { synchronized (got) { got.add(f); } });
        CountDownLatch started = new CountDownLatch(1);
        ex.execute(() -> { started.countDown(); try { Thread.sleep(50); } catch (InterruptedException ignored) { } });
        w.update(new Object(), F, P, walk(20000, 0.2));   // queued behind the sleeper
        started.await();
        w.close();
        ex.shutdown();
        assertTrue(ex.awaitTermination(10, TimeUnit.SECONDS));
        assertTrue(got.isEmpty());
    }

    @Test public void updateAndClearAfterCloseDoNothing() {
        ExecutorService ex = Executors.newSingleThreadExecutor();
        CoverageWorker w = new CoverageWorker(ex, f -> { });
        Object session = new Object();
        PointRow[] rows = walk(5, 0.2);
        w.update(session, F, P, rows);
        w.close();
        ex.shutdownNow();
        assertFalse(w.update(session, F, P, rows));   // no RejectedExecutionException
        assertFalse(w.update(new Object(), F, P, rows));
        w.clear();
        assertFalse(w.active());
    }

    @Test public void anExceptionInATaskStopsTheWorkerWithoutThrowing() {
        Queue q = new Queue();
        List<CoverageRaster.Frame> got = new ArrayList<>();
        List<Throwable> errors = new ArrayList<>();
        CoverageWorker w = new CoverageWorker(q, new CoverageWorker.Sink() {
            @Override public void frame(CoverageRaster.Frame f) { got.add(f); }
            @Override public void failed(Throwable t) { errors.add(t); }
        });
        Object session = new Object();
        PointRow[] rows = walk(6, 0.2);
        assertTrue(w.update(session, null, P, rows));   // no map frame: Coverage.add throws in the rebuild
        q.runAll();                                      // caught on the worker, not thrown here
        assertTrue(w.failed());
        assertEquals(1, errors.size());
        assertTrue(errors.get(0) instanceof NullPointerException);
        assertTrue(got.isEmpty());
        assertFalse(w.update(session, F, P, rows));     // stopped: nothing more is queued
        assertTrue(q.q.isEmpty());
        w.clear();
        q.runAll();
        assertEquals(1, errors.size());
    }

    @Test public void aSinkThatThrowsWhileReportingAFailureDoesNotEscape() {
        Queue q = new Queue();
        CoverageWorker w = new CoverageWorker(q, new CoverageWorker.Sink() {
            @Override public void frame(CoverageRaster.Frame f) { }
            @Override public void failed(Throwable t) { throw new OutOfMemoryError("while reporting"); }
        });
        assertTrue(w.update(new Object(), null, P, walk(6, 0.2)));   // the rebuild throws
        q.runAll();                                                  // and so does the report
        assertTrue(w.failed());
        assertTrue(q.q.isEmpty());
    }

    @Test public void anExceptionInAFeedStopsTheWorker() {
        Queue q = new Queue();
        List<CoverageRaster.Frame> got = new ArrayList<>();
        List<Throwable> errors = new ArrayList<>();
        CoverageWorker w = new CoverageWorker(q, new CoverageWorker.Sink() {
            @Override public void frame(CoverageRaster.Frame f) { got.add(f); }
            @Override public void failed(Throwable t) { errors.add(t); }
        });
        Object session = new Object();
        PointRow[] rows = walk(6, 0.2);
        w.update(session, F, P, Arrays.copyOf(rows, 5));
        q.runAll();
        assertEquals(1, got.size());
        rows[5] = null;                                  // a feed that throws
        assertFalse(w.update(session, F, P, rows));
        q.runAll();
        assertTrue(w.failed());
        assertEquals(1, errors.size());
        assertEquals(1, got.size());                     // no frame after the failure
    }

    @Test public void aWorkerThreadErrorDoesNotReachTheUncaughtHandler() throws Exception {
        List<Throwable> uncaught = new ArrayList<>();
        ExecutorService ex = Executors.newSingleThreadExecutor(r -> {
            Thread t = new Thread(r, "coverage-test");
            t.setUncaughtExceptionHandler((th, e) -> { synchronized (uncaught) { uncaught.add(e); } });
            return t;
        });
        CountDownLatch failed = new CountDownLatch(1);
        CoverageWorker w = new CoverageWorker(ex, new CoverageWorker.Sink() {
            @Override public void frame(CoverageRaster.Frame f) { }
            @Override public void failed(Throwable t) { failed.countDown(); }
        });
        w.update(new Object(), null, P, walk(3, 0.2));
        assertTrue(failed.await(10, TimeUnit.SECONDS));
        ex.shutdown();
        assertTrue(ex.awaitTermination(10, TimeUnit.SECONDS));
        synchronized (uncaught) { assertTrue(uncaught.isEmpty()); }
    }

    @Test public void aFeedThatOnlyHitsTheNodeCapStillSendsTheAreaLimit() {
        // 1 m spacing, radius and reach 1.2 m: an isolated capture on a node makes exactly 5
        // nodes, so MAX_NODES / 5 captures 3 m apart fill the grid without setting full()
        Coverage.Params p = new Coverage.Params(1.0, 1.2, 1.2, 0.03, 0.015, 0.015);
        int n = Coverage.MAX_NODES / 5, side = (int) Math.ceil(Math.sqrt(n));
        PointRow[] rows = new PointRow[n + 1];
        for (int k = 0; k < n; k++) rows[k] = surface(3.0 * (k % side), 3.0 * (k / side), 0.2);
        rows[n] = surface(-30, -30, 0.2);   // fresh ground: no node can be made, none touched
        Queue q = new Queue();
        List<CoverageRaster.Frame> got = new ArrayList<>();
        CoverageWorker w = new CoverageWorker(q, got::add);
        Object session = new Object();
        w.update(session, F, p, Arrays.copyOf(rows, n));
        q.runAll();
        assertEquals(1, got.size());
        assertFalse(got.get(0).areaLimit);
        assertFalse(w.update(session, F, p, rows));
        q.runAll();
        assertEquals(2, got.size());
        CoverageRaster.Frame last = got.get(1);
        assertTrue(last.areaLimit);
        assertFalse(last.isFull());
        assertEquals(0, last.index.length);   // an empty patch: only the legend changes
    }

    @Test public void aNewerRebuildOnARealThreadWins() throws Exception {
        ExecutorService ex = Executors.newSingleThreadExecutor();
        List<CoverageRaster.Frame> got = new ArrayList<>();
        CoverageWorker w = new CoverageWorker(ex, f -> { synchronized (got) { got.add(f); } });
        Object session = new Object();
        PointRow[] big = walk(20000, 0.2);
        w.update(session, F, P, big);
        Coverage.Params p2 = new Coverage.Params(1.0, 1.0, 2.0, 0.03, 0.015, 0.015);
        w.update(session, F, p2, big);
        ex.shutdown();
        assertTrue(ex.awaitTermination(30, TimeUnit.SECONDS));
        synchronized (got) {
            for (CoverageRaster.Frame f : got) if (f.gen == w.generation()) assertEquals(1.0, f.cell, 0);
            assertEquals(w.generation(), got.get(got.size() - 1).gen);
        }
    }
    /** A 0.3 m/m bank (z = 0.3 e + 2 cm noise) over a w by h metre patch, captures in random order. */
    private static PointRow[] bank(int n, double w, double h, long seed) {
        java.util.Random rnd = new java.util.Random(seed);
        PointRow[] rows = new PointRow[n];
        for (int k = 0; k < n; k++) {
            double e = w * rnd.nextDouble(), no = h * rnd.nextDouble();
            rows[k] = surface(e, no, 0.3 * e + 0.02 * rnd.nextGaussian());
            rows[k].n = k;
        }
        return rows;
    }

    /** Feeds on a bank: a node the plane pass clears or reddens without being fed is repainted by the patch. */
    @Test public void ringTouchedNodesArePatched() {
        Queue q = new Queue();
        Ui ui = new Ui();
        CoverageWorker w = new CoverageWorker(q, ui::take, 64, 4);
        ui.w = w;
        Object session = new Object();
        PointRow[] rows = bank(400, 6, 6, 7);
        assertTrue(w.update(session, F, P, Arrays.copyOf(rows, 20)));
        q.runAll();
        ui.drain();
        Coverage shadow = direct(P, Arrays.copyOf(rows, 20));
        shadow.drainTouched();
        int ringOnly = 0;
        for (int n = 21; n <= rows.length; n++) {
            assertFalse(w.update(session, F, P, Arrays.copyOf(rows, n)));
            q.runAll();
            ui.drain();
            ui.screen.assertMatches(direct(P, Arrays.copyOf(rows, n)));
            shadow.add(rows[n - 1], F);
            double[] enu = F.toEnu(rows[n - 1].lat, rows[n - 1].lon, rows[n - 1].hGround);
            for (Coverage.Node nd : shadow.drainTouched()) {
                if (nd.n > 0 && Math.hypot(nd.i * P.cell - enu[0], nd.j * P.cell - enu[1]) > P.captureRadius) ringOnly++;
            }
        }
        assertTrue("ring-only repaints " + ringOnly, ringOnly > 0);
        int planar = 0;
        for (Coverage.Node nd : direct(P, rows).nodes()) if (nd.planar) planar++;
        assertTrue("planar " + planar, planar > 5);
    }

    /**
     * A dense bank session of 5000 captures rebuilt with the plane pass, against the same
     * rebuild without it in the same run (a ratio, not wall seconds, so a slow runner does
     * not fail it). Prints the per-capture cost.
     */
    @Test public void planePassCostIsBounded() {
        PointRow[] rows = bank(5000, 20, 10, 11);
        Coverage.Params p = P;
        long[] best = {Long.MAX_VALUE, Long.MAX_VALUE};
        for (int rep = 0; rep < 3; rep++) {   // the first round warms the JIT up
            for (int plane = 0; plane < 2; plane++) {
                long t0 = System.nanoTime();
                Coverage c = new Coverage(p, plane == 1);
                for (PointRow r : rows) { c.add(r, F); c.drainTouched(); }
                long t = System.nanoTime() - t0;
                if (rep > 0) best[plane] = Math.min(best[plane], t);
            }
        }
        double ratio = (double) best[1] / best[0];
        System.out.printf(java.util.Locale.US, "coverage rebuild of %d bank captures: %.1f us/capture without the plane "
                + "pass, %.1f us/capture with it (ratio %.2f)%n", rows.length, best[0] / 1e3 / rows.length,
                best[1] / 1e3 / rows.length, ratio);
        assertTrue("plane pass costs " + ratio + " x the pass-1 rebuild", ratio < 6.0);
    }
}
