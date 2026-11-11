package rink.icecube.logger;

import android.os.SystemClock;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import rink.icecube.logger.core.Epoch;
import rink.icecube.logger.core.EpochAverage;
import rink.icecube.logger.core.PhotoRule;
import rink.icecube.logger.core.PointRow;
import rink.icecube.logger.core.PoleCheck;
import rink.icecube.logger.core.RefOccupation;
import rink.icecube.logger.core.SurveyMode;

/**
 * The captures of one session: MARK in Surface, Object or Ref mode and PHOTO, written to
 * points.csv in the session folder (header line, one row per capture; rewritten whole on
 * each change, which is tiny) and as "capture ..." events in events.csv. Refs are also
 * appended to refs.csv and checked against earlier visits (the pole check).
 *
 * GGA epochs arrive on the GPS thread through {@link #onEpoch}; MARK and PHOTO come from
 * the screen. Everything that touches the rows is synchronized; files are written on a
 * single background thread in order.
 */
final class Survey {
    static final long WINDOW_NS = 1_000_000_000L, MAX_AGE_NS = 2_000_000_000L, KEEP_NS = 10_000_000_000L;
    static final long NOTICE_MS = 10_000;

    /** A short message for the map, green (ok) or red, shown until untilMs (uptime). */
    static final class Notice {
        final String text;
        final boolean ok;
        final long untilMs;
        Notice(String text, boolean ok) { this.text = text; this.ok = ok; untilMs = SystemClock.uptimeMillis() + NOTICE_MS; }
    }

    /** What the map shows while a ref occupation runs. */
    static final class RefView {
        final double remainingS, progress;
        final int epochs, durationS;
        final boolean paused;
        RefView(RefOccupation r, int durationS, long nowNs) {
            remainingS = r.remainingSeconds(); progress = r.progress(); epochs = r.epochs();
            this.durationS = durationS; paused = r.pausedAt(nowNs);
        }
    }

    /** The answer to MARK or PHOTO: ok, and a short text for a toast when not. */
    static final class Outcome {
        final boolean ok;
        final String text;
        Outcome(boolean ok, String text) { this.ok = ok; this.text = text; }
    }

    /** A photo being taken: its file, and the capture it goes onto (or -1 for a row of its own). */
    static final class PhotoPlan {
        final File file;
        final int attachN;
        final long pressNs;
        PhotoPlan(File file, int attachN, long pressNs) { this.file = file; this.attachN = attachN; this.pressNs = pressNs; }
    }

    private final LoggerService svc;
    final File dir, photos;
    private final ArrayDeque<Epoch> epochs = new ArrayDeque<>();
    private final List<PointRow> rows = new ArrayList<>();
    private int counter, captures;
    private PointRow lastCapture;
    private RefOccupation ref;
    private double refPole;
    private int refSeconds;
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private volatile Notice notice;
    private volatile String poleWarning;
    private volatile int refsGeneration;

    Survey(LoggerService svc, File dir) {
        this.svc = svc;
        this.dir = dir;
        photos = new File(dir, "photos");
        writePoints(new ArrayList<String>());   // points.csv with its header from the start
    }

    // ---- GPS epochs ----

    void onEpoch(Epoch e) {
        if (e == null) return;
        PointRow finished = null;
        synchronized (this) {
            epochs.addLast(e);
            while (epochs.peekFirst().elapsedNs < e.elapsedNs - KEEP_NS) epochs.removeFirst();
            RefOccupation r = ref;
            if (r == null) return;
            switch (r.onEpoch(e)) {
                case PAUSED:
                    svc.event(String.format(Locale.US, "ref paused: not RTK fixed for 2 s (quality %d), %.0f s to go",
                            e.quality, r.remainingSeconds()));
                    notice = new Notice("Ref paused: not RTK fixed", false);
                    cue(Feedback.Cue.REF_PAUSED);
                    break;
                case RESUMED:
                    svc.event(String.format(Locale.US, "ref resumed, %.0f s to go", r.remainingSeconds()));
                    notice = new Notice("Ref resumed", true);
                    cue(Feedback.Cue.REF_RESUMED);
                    break;
                case DONE:
                    ref = null;
                    finished = add(PointRow.REF, e.elapsedNs, r.average(), refPole);
                    svc.event(String.format(Locale.US, "ref end n=%d epochs=%d pauses=%d", finished.n, r.epochs(), r.pauses()));
                    cue(Feedback.Cue.REF_END);
                    break;
                default:
                    break;
            }
        }
        if (finished != null) {
            PointRow f = finished;
            run(() -> poleCheck(f));
        }
    }

    // ---- MARK ----

    synchronized Outcome mark(SurveyMode mode) {
        long now = SystemClock.elapsedRealtimeNanos();
        if (ref != null) return cancelRef("cancelled by MARK");
        EpochAverage a = EpochAverage.recent(new ArrayList<>(epochs), now, WINDOW_NS, MAX_AGE_NS);
        if (a == null) {
            cue(Feedback.Cue.ERROR);
            svc.event("capture refused class=" + mode.key + ": no fix");
            return new Outcome(false, "No fix: nothing captured");
        }
        if (mode == SurveyMode.REF) {
            refSeconds = Settings.refSeconds(Settings.prefs(svc));
            refPole = svc.poleHeight();
            ref = new RefOccupation(now, refSeconds * 1_000_000_000L);
            svc.event(String.format(Locale.US, "ref start n=%d duration_s=%d pole_height=%.3f quality=%d",
                    counter + 1, refSeconds, refPole, a.quality));
            cue(Feedback.Cue.REF_START);
            notice = new Notice(a.quality == 4 ? "Ref started: hold the pole still"
                    : "Ref started: waiting for RTK fixed", a.quality == 4);
            return new Outcome(true, null);
        }
        PointRow r = add(mode.key, now, a, svc.poleHeight());
        cue(mode == SurveyMode.SURFACE ? Feedback.Cue.SURFACE : Feedback.Cue.OBJECT);
        notice = new Notice(String.format(Locale.US, "%s%d  %s  %d ep  ±%.1f cm", label(r.cls), r.n,
                fixName(r.fix), r.nEpochs, 100 * Math.max(r.sdE, r.sdN)), r.fix == 4);
        return new Outcome(true, null);
    }

    private Outcome cancelRef(String why) {
        RefOccupation r = ref;
        ref = null;
        svc.event(String.format(Locale.US, "ref %s after %.0f s, %d fixed epochs", why, r.activeSeconds(), r.epochs()));
        cue(Feedback.Cue.REF_CANCEL);
        notice = new Notice("Ref cancelled", false);
        return new Outcome(true, null);
    }

    /** Adds a capture row (called with the lock held) and writes points.csv. */
    private PointRow add(String cls, long elapsedNs, EpochAverage a, double pole) {
        PointRow r = PointRow.of(++counter, cls, elapsedNs, a, pole, System.currentTimeMillis());
        rows.add(r);
        if (r.isCapture()) { lastCapture = r; captures++; }
        svc.event("capture " + r.toCsv());
        savePoints();
        return r;
    }

    // ---- ref: pole check (background thread) ----

    private void poleCheck(PointRow r) {
        r.session = dir.getName();
        List<PointRow> earlier = RefsStore.readAll(svc);
        PoleCheck c = PoleCheck.check(r, earlier, Settings.poleTolerance(Settings.prefs(svc)));
        try {
            File f = RefsStore.append(svc, r);
            svc.event("ref n=" + r.n + " appended to " + f);
        } catch (IOException e) {
            svc.event("ref n=" + r.n + " not appended to refs.csv: " + e.getMessage());
        }
        refsGeneration++;
        String text;
        if (c == null) {
            text = Double.isNaN(r.hGround) ? "Ref R" + r.n + " saved (no ground height: no pole check)"
                    : "Ref R" + r.n + " saved: first visit to this mark";
            notice = new Notice(text, true);
            poleWarning = null;
            svc.event("pole check n=" + r.n + ": " + (Double.isNaN(r.hGround) ? "no ground height" : "no earlier ref within 0.30 m"));
            return;
        }
        text = c.describe();
        svc.event(String.format(Locale.US, "pole check n=%d: %s; diff_last_m=%.4f diff_mean_m=%.4f visits=%d last=%s/%d ok=%b",
                r.n, text, c.diffLast, c.diffMean, c.visits, c.last.session, c.last.n, c.ok));
        if (c.ok) {
            poleWarning = null;
            notice = new Notice(text, true);
            cue(Feedback.Cue.POLE_OK);
        } else {
            poleWarning = text + " (R" + r.n + "; check the pole height)";
            notice = new Notice(text, false);
            cue(Feedback.Cue.POLE_ALARM);
        }
    }

    // ---- PHOTO ----

    /** Chooses the file and the capture for a photo about to be taken. Null if the folder cannot be made. */
    synchronized PhotoPlan planPhoto() {
        long now = SystemClock.elapsedRealtimeNanos();
        PointRow last = lastCapture;
        int attach = last == null ? -1 : PhotoRule.attachTo(last.n, last.elapsedNs, now);
        Epoch e = epochs.peekLast();
        String utc = PointRow.utcCompact(e == null ? Double.NaN : e.tod, System.currentTimeMillis());
        if (!photos.isDirectory() && !photos.mkdirs()) return null;
        File f = new File(photos, PhotoRule.fileName(attach > 0 ? attach : counter + 1, utc));
        for (int i = 2; f.exists(); i++) f = new File(photos, f.getName().replace(".jpg", "") + "_" + i + ".jpg");
        return new PhotoPlan(f, attach, now);
    }

    synchronized void photoTaken(PhotoPlan p) {
        String name = p.file.getName();
        PointRow target = null;
        if (p.attachN > 0) for (PointRow r : rows) if (r.n == p.attachN) target = r;
        if (target != null) {
            target.photo = PhotoRule.add(target.photo, name);
            svc.event("photo file=photos/" + name + " bytes=" + p.file.length() + " attached to n=" + target.n);
            savePoints();
            notice = new Notice("Photo on " + label(target.cls) + target.n, true);
        } else {
            EpochAverage a = EpochAverage.recent(new ArrayList<>(epochs), SystemClock.elapsedRealtimeNanos(), WINDOW_NS, MAX_AGE_NS);
            PointRow r = PointRow.of(++counter, PointRow.PHOTO, p.pressNs, a, svc.poleHeight(), System.currentTimeMillis());
            r.photo = name;
            rows.add(r);
            svc.event("photo file=photos/" + name + " bytes=" + p.file.length() + " own row n=" + r.n);
            svc.event("capture " + r.toCsv());
            savePoints();
            notice = new Notice("Photo saved" + (a == null ? " (no fix)" : ""), a != null);
        }
        cue(Feedback.Cue.PHOTO);
    }

    void photoCancelled(PhotoPlan p, String why) {
        //noinspection ResultOfMethodCallIgnored
        p.file.delete();
        svc.event("photo " + why + ": " + p.file.getName() + " not kept");
    }

    // ---- state for the screen ----

    synchronized PointRow[] rows() { return rows.toArray(new PointRow[0]); }
    synchronized int captures() { return captures; }
    synchronized RefView refView() {
        return ref == null ? null : new RefView(ref, refSeconds, SystemClock.elapsedRealtimeNanos());
    }
    synchronized boolean refRunning() { return ref != null; }
    Notice notice() { Notice n = notice; return n != null && SystemClock.uptimeMillis() < n.untilMs ? n : null; }
    String poleWarning() { return poleWarning; }
    int refsGeneration() { return refsGeneration; }

    static String label(String cls) {
        switch (cls) {
            case PointRow.SURFACE: return "S";
            case PointRow.OBJECT: return "O";
            case PointRow.REF: return "R";
            default: return "P";
        }
    }

    static String fixName(int q) {
        switch (q) {
            case 4: return "FIXED";
            case 5: return "FLOAT";
            case 2: return "DGPS";
            case 1: return "SINGLE";
            case 0: return "NO FIX";
            default: return "Q" + q;
        }
    }

    // ---- files ----

    private void savePoints() {
        List<String> lines = new ArrayList<>();
        for (PointRow r : rows) lines.add(r.toCsv());
        writePoints(lines);
    }

    private void writePoints(List<String> lines) {
        run(() -> {
            File tmp = new File(dir, "points.csv.tmp"), out = new File(dir, "points.csv");
            try (Writer w = new OutputStreamWriter(new FileOutputStream(tmp), StandardCharsets.UTF_8)) {
                w.write(PointRow.HEADER + "\n");
                for (String l : lines) w.write(l + "\n");
            } catch (IOException e) {
                svc.event("points.csv write failed: " + e.getMessage());
                return;
            }
            if (!tmp.renameTo(out)) svc.event("points.csv rename failed");
        });
    }

    private void run(Runnable r) {
        try { io.execute(r); } catch (java.util.concurrent.RejectedExecutionException ignored) { }
    }

    private void cue(Feedback.Cue c) { Feedback.play(svc, c); }

    /** Ends a running ref and finishes the file writes (session stop). */
    void close() {
        synchronized (this) { if (ref != null) cancelRef("cancelled (session stop)"); }
        io.shutdown();
        try { io.awaitTermination(5, TimeUnit.SECONDS); } catch (InterruptedException ignored) { }
    }
}
