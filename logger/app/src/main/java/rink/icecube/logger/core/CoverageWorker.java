package rink.icecube.logger.core;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.Executor;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Runs the coverage overlay off the UI thread (logger 0.7, issue #6), without Android so its
 * ordering and cancellation are JVM-tested.
 *
 * Threads. {@link #update}, {@link #clear}, {@link #close} and {@link #generation} are called
 * on one thread (the UI thread). Everything else runs as tasks on `worker`, which must run
 * them one at a time in order (a single-thread executor): only those tasks touch the
 * {@link Coverage} and the {@link CoverageRaster}. Results go to `sink` on the worker
 * thread as immutable {@link CoverageRaster.Frame}s; the app posts them to the UI thread
 * and draws a frame only when its gen equals {@link #generation()} at that moment.
 *
 * Order. update() posts a rebuild (all the session's rows) when the session, the map frame
 * or the params changed, or the row list shrank; otherwise a feed of the rows past the last
 * ones posted. Feeds queue behind their rebuild, so each frame applies to the one before it.
 *
 * Cancellation. Each rebuild, and clear() or close(), starts a new generation. A task of an
 * older generation does nothing, a rebuild in flight stops at its next row, and no frame of
 * an older generation is sent.
 *
 * Failure. An exception or error in a task is caught on the worker (it never reaches the
 * thread's uncaught handler, which would end the app and its LoggerService): the coverage
 * is dropped, `sink` gets {@link Sink#failed} once, and the worker stops for good: no more
 * frames, update() queues nothing. The app shows "coverage stopped: error" until the screen
 * is recreated.
 */
public final class CoverageWorker {
    /** Receives each frame, on the worker thread. */
    public interface Sink {
        void frame(CoverageRaster.Frame f);

        /** The worker stopped on `t` (on the worker thread, once); no frame follows. */
        default void failed(Throwable t) { }
    }

    private final Executor worker;
    private final Sink sink;
    private final int maxSide, margin;
    private final AtomicInteger gen = new AtomicInteger();
    /** Set on the worker thread when a task failed; read on any thread. */
    private volatile boolean failed;

    // Caller (UI) thread: what the queued tasks are for.
    private boolean active, closed;
    private Object session;
    private LocalFrame frame;
    private Coverage.Params params;
    private int rowsPosted;

    // Worker thread only.
    private Coverage coverage;
    private CoverageRaster raster;
    private int coverageGen = -1;
    /** The newest touched box sent, and Coverage.full() at the last frame sent. */
    private int[] lastNewest;
    private boolean lastFull;

    public CoverageWorker(Executor worker, Sink sink) { this(worker, sink, CoverageRaster.MAX_SIDE, CoverageRaster.MARGIN); }

    /** Smaller raster sides are for tests. */
    public CoverageWorker(Executor worker, Sink sink, int maxSide, int margin) {
        this.worker = worker; this.sink = sink; this.maxSide = maxSide; this.margin = margin;
    }

    /** The current generation: a frame with another gen is stale. Safe from any thread. */
    public int generation() { return gen.get(); }

    /** True once a task failed: the worker has stopped (see Failure above). */
    public boolean failed() { return failed; }

    /** True between an update() and a clear(). */
    public boolean active() { return active; }

    /**
     * Brings the overlay up to `rows` of `session` (any object, compared by identity) in
     * `frame` with `p`. Returns true when this started a rebuild (the drawn overlay is then out
     * of date until its first frame arrives), false for a feed or nothing to do.
     */
    public boolean update(Object session, LocalFrame frame, Coverage.Params p, PointRow[] rows) {
        if (closed || failed) return false;   // the executor may be shut down; or stopped on an error
        if (!active || session != this.session || frame != this.frame || !p.equals(params) || rows.length < rowsPosted) {
            int g = gen.incrementAndGet();
            active = true;
            this.session = session;
            this.frame = frame;
            params = p;
            rowsPosted = rows.length;
            PointRow[] all = Arrays.copyOf(rows, rows.length);
            post(() -> rebuild(g, p, frame, all));
            return true;
        }
        if (rows.length > rowsPosted) {
            int g = gen.get();
            PointRow[] add = Arrays.copyOfRange(rows, rowsPosted, rows.length);
            rowsPosted = rows.length;
            post(() -> feed(g, frame, add));
        }
        return false;
    }

    /** Overlay off or no session: drops queued work and, on the worker, the coverage. */
    public void clear() {
        if (closed || !active) return;
        active = false;
        session = null;
        frame = null;
        params = null;
        int g = gen.incrementAndGet();
        post(() -> { if (gen.get() == g) drop(); });
    }

    /**
     * For onDestroy: cancels everything; the caller then shuts the executor down. update()
     * and clear() do nothing afterwards.
     */
    public void close() {
        closed = true;
        active = false;
        gen.incrementAndGet();
    }

    /** Queues `task` on the worker, catching what it throws (see Failure above). */
    private void post(Runnable task) {
        worker.execute(() -> {
            if (failed) return;
            try {
                task.run();
            } catch (RuntimeException | Error e) {
                failed = true;
                drop();
                sink.failed(e);
            }
        });
    }

    private void drop() { coverage = null; raster = null; coverageGen = -1; lastNewest = null; lastFull = false; }

    private void rebuild(int g, Coverage.Params p, LocalFrame f, PointRow[] rows) {
        if (gen.get() != g) return;
        drop();
        Coverage c = new Coverage(p);
        int[] newest = null;
        for (PointRow r : rows) {
            if (gen.get() != g) return;   // a newer rebuild or a clear is queued
            if (c.add(r, f)) {
                int[] b = CoverageRaster.box(c.drainTouched());
                if (b != null) newest = b;
            }
        }
        CoverageRaster ras = new CoverageRaster(maxSide, margin);
        CoverageRaster.Frame fr = ras.repaint(g, c, newest);
        coverage = c;
        raster = ras;
        coverageGen = g;
        lastNewest = newest;
        lastFull = fr.areaLimit;
        if (gen.get() == g) sink.frame(fr);
    }

    private void feed(int g, LocalFrame f, PointRow[] rows) {
        if (gen.get() != g || coverageGen != g) return;
        List<Coverage.Node> touched = new ArrayList<>();
        int[] newest = null;
        for (PointRow r : rows) {
            if (!coverage.add(r, f)) continue;
            List<Coverage.Node> t = coverage.drainTouched();
            int[] b = CoverageRaster.box(t);
            if (b != null) { newest = b; touched.addAll(t); }
        }
        // A capture that first meets MAX_NODES may touch nothing; the frame still carries
        // the area-limit line for the legend.
        if (touched.isEmpty() && coverage.full() == lastFull) return;
        if (newest != null) lastNewest = newest;
        CoverageRaster.Frame fr = raster.update(g, coverage, touched, lastNewest);
        lastFull = fr.areaLimit;
        if (gen.get() == g) sink.frame(fr);
    }
}
