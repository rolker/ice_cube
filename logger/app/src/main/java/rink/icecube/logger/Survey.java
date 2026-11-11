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
import rink.icecube.logger.core.FixGuard;
import rink.icecube.logger.core.PhotoRule;
import rink.icecube.logger.core.PointRow;
import rink.icecube.logger.core.PoleCheck;
import rink.icecube.logger.core.RefOccupation;
import rink.icecube.logger.core.SurveyMode;
import rink.icecube.logger.core.TiltSet;
import rink.icecube.logger.core.TiltSolver;

/**
 * The captures of one session: MARK in Surface, Object or Ref mode and PHOTO, written to
 * points.csv in the session folder (header line, one row per capture; rewritten whole on
 * each change, which is tiny) and as "capture ..." events in events.csv. Refs are also
 * appended to refs.csv and checked against earlier visits (the pole check).
 *
 * The false-fix guard ({@link FixGuard}) follows the RTK fixed stretches: captures, ref
 * counting and tilt leans are held back while a new fix settles, and rows from a stretch
 * that began with a large jump are flagged (fix_suspect) with an amber notice.
 *
 * Tilt capture ({@link TiltSet}): startTilt, then lean() per lean, finishTilt (MARK) or
 * cancelTilt; every lean goes to tilt_obs.csv.
 *
 * GGA epochs arrive on the GPS thread through {@link #onEpoch}; MARK and PHOTO come from
 * the screen. Everything that touches the rows is synchronized; files are written on a
 * single background thread in order.
 */
final class Survey {
    static final long WINDOW_NS = 1_000_000_000L, MAX_AGE_NS = 2_000_000_000L, KEEP_NS = 10_000_000_000L;
    static final long NOTICE_MS = 10_000;

    /**
     * A short message for the map, green (ok), amber (warn) or red, shown until untilMs
     * (uptime). at is the capture it reports, or null; the map adds its x/y/z in the map frame.
     */
    static final class Notice {
        final String text;
        final boolean ok, warn;
        final long untilMs;
        final PointRow at;
        Notice(String text, boolean ok) { this(text, ok, false); }
        Notice(String text, boolean ok, boolean warn) { this(text, ok, warn, null); }
        Notice(String text, boolean ok, boolean warn, PointRow at) {
            this.text = text; this.ok = ok; this.warn = warn; this.at = at; untilMs = SystemClock.uptimeMillis() + NOTICE_MS;
        }
    }

    /** The fix chip's guard state: settling seconds left (0: not settling), and a suspect stretch's jump. */
    static final class FixState {
        final int settleS;
        final boolean suspect;
        final double jump;
        FixState(int settleS, boolean suspect, double jump) { this.settleS = settleS; this.suspect = suspect; this.jump = jump; }
    }

    /** What the map's tilt overlay shows. Arrays per lean; azimuths and tilts NaN before 3 leans. */
    static final class TiltView {
        final String cls;
        final int count;
        final double[] azimuth, tilt, imu;
        final boolean[] imuBad;
        final double coverage, rms, maxImu, nowAzimuth, nowTilt, pole;
        final double[] sd;
        final int settleS;
        TiltView(TiltSet t, double nowAzimuth, double nowTilt, int settleS) {
            List<TiltSet.Lean> ls = t.leans();
            cls = t.cls;
            pole = t.pole;
            count = ls.size();
            azimuth = new double[count]; tilt = new double[count]; imu = new double[count]; imuBad = new boolean[count];
            for (int i = 0; i < count; i++) {
                TiltSet.Lean l = ls.get(i);
                azimuth[i] = l.azimuthDeg; tilt[i] = l.tiltDeg; imu[i] = l.imuDeg;
                imuBad[i] = l.imuDeg > t.gates.imuDeg;
            }
            TiltSet.Result r = t.provisional();
            TiltSolver.Solution s = r == null ? null : r.solution;
            coverage = s == null ? Double.NaN : s.coverageDeg;
            rms = s == null ? Double.NaN : s.rms;
            sd = s == null ? null : s.sd;
            maxImu = r == null ? Double.NaN : r.maxImuDeg;
            this.nowAzimuth = nowAzimuth;
            this.nowTilt = nowTilt;
            this.settleS = settleS;
        }
    }

    /** What the map shows while a ref occupation runs. */
    static final class RefView {
        final double remainingS, progress;
        final int epochs, durationS;
        final boolean paused;
        final boolean settling;
        RefView(RefOccupation r, int durationS, long nowNs, boolean settling) {
            remainingS = r.remainingSeconds(); progress = r.progress(); epochs = r.epochs();
            this.durationS = durationS; this.settling = settling; paused = r.pausedAt(nowNs) || settling;
        }
    }

    /**
     * The answer to MARK, PHOTO or a tilt action: ok, and a short text for a toast (shown
     * when not ok, or when ok with a text). confirm: a tilt finish waits for the user to
     * accept the warnings in text (call finishTilt(true)).
     */
    static final class Outcome {
        final boolean ok, confirm;
        final String text;
        Outcome(boolean ok, String text) { this(ok, text, false); }
        Outcome(boolean ok, String text, boolean confirm) { this.ok = ok; this.text = text; this.confirm = confirm; }
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
    private final FixGuard guard;
    private double refJump = Double.NaN;
    private boolean refSuspect;
    private TiltSet tilt;
    private int tiltSets;

    Survey(LoggerService svc, File dir) {
        this.svc = svc;
        this.dir = dir;
        photos = new File(dir, "photos");
        guard = new FixGuard(Settings.settleNs(Settings.prefs(svc)), Settings.jumpM(Settings.prefs(svc)));
        writePoints(new ArrayList<String>());   // points.csv with its header from the start
    }

    /** Settings changed: the settle time applies at once, the jump threshold from the next stretch. */
    synchronized void applyGuardSettings() {
        guard.setLimits(Settings.settleNs(Settings.prefs(svc)), Settings.jumpM(Settings.prefs(svc)));
    }

    // ---- GPS epochs ----

    void onEpoch(Epoch e) {
        if (e == null) return;
        PointRow finished = null;
        synchronized (this) {
            epochs.addLast(e);
            while (epochs.peekFirst().elapsedNs < e.elapsedNs - KEEP_NS) epochs.removeFirst();
            FixGuard.Stretch st = guard.onEpoch(e);
            if (st != null) svc.event(st.eventText());
            RefOccupation r = ref;
            if (r == null) return;
            boolean settling = guard.settling(e.elapsedNs);
            if (e.fixed() && !settling) {
                FixGuard.Stretch cur = guard.current();
                if (cur != null) {
                    if (!Double.isNaN(cur.jump)) refJump = Double.isNaN(refJump) ? cur.jump : Math.max(refJump, cur.jump);
                    refSuspect |= cur.suspect;
                }
            }
            switch (r.onEpoch(e, !settling)) {
                case PAUSED:
                    if (e.fixed() && settling) {
                        svc.event(String.format(Locale.US, "ref paused: fix settling, %.0f s to go", r.remainingSeconds()));
                        notice = new Notice("Ref paused: fix settling", false);
                    } else {
                        svc.event(String.format(Locale.US, "ref paused: not RTK fixed for 2 s (quality %d), %.0f s to go",
                                e.quality, r.remainingSeconds()));
                        notice = new Notice("Ref paused: not RTK fixed", false);
                    }
                    cue(Feedback.Cue.REF_PAUSED);
                    break;
                case RESUMED:
                    svc.event(String.format(Locale.US, "ref resumed, %.0f s to go", r.remainingSeconds()));
                    notice = new Notice("Ref resumed", true);
                    cue(Feedback.Cue.REF_RESUMED);
                    break;
                case DONE:
                    ref = null;
                    finished = PointRow.of(++counter, PointRow.REF, e.elapsedNs, r.average(), refPole,
                            System.currentTimeMillis());
                    finished.method = PointRow.OCCUPATION;
                    FixGuard.Stretch cur = guard.current();
                    finished.fixAge = cur == null ? Double.NaN : cur.ageSeconds(e.elapsedNs);
                    finished.fixJump = refJump;
                    finished.fixSuspect = refSuspect;
                    addRow(finished);
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

    /**
     * The best epochs of the last second (see EpochAverage.recent), leaving out fixed epochs
     * from before the latest fix stretch began, so one average never mixes two stretches.
     */
    private EpochAverage average(long now) {
        List<Epoch> list = new ArrayList<>(epochs);
        FixGuard.Stretch last = guard.last();
        if (last != null) {
            List<Epoch> keep = new ArrayList<>();
            for (Epoch x : list) if (!(x.fixed() && x.elapsedNs < last.startNs)) keep.add(x);
            list = keep;
        }
        return EpochAverage.recent(list, now, WINDOW_NS, MAX_AGE_NS);
    }

    /** The stretch an RTK fixed average belongs to (the latest one), or null if a is not fixed. */
    private FixGuard.Stretch stretchOf(EpochAverage a) { return a != null && a.quality == 4 ? guard.last() : null; }

    /** Seconds of settling left for a capture from stretch s at now (0 if none). */
    private int settleLeft(FixGuard.Stretch s, long now) {
        if (s == null || !guard.settling(s, now)) return 0;
        return Math.max(1, (int) Math.ceil((guard.settleNs() - (now - s.startNs)) / 1e9));
    }

    private static String jumpText(double j) { return Double.isNaN(j) ? "?" : String.format(Locale.US, "%.1f m", j); }

    synchronized Outcome mark(SurveyMode mode) {
        long now = SystemClock.elapsedRealtimeNanos();
        if (tilt != null) return finishTilt(false);
        if (ref != null) return cancelRef("cancelled by MARK");
        EpochAverage a = average(now);
        if (a == null) {
            cue(Feedback.Cue.ERROR);
            svc.event("capture refused class=" + mode.key + ": no fix");
            return new Outcome(false, "No fix: nothing captured");
        }
        FixGuard.Stretch s = stretchOf(a);
        int left = settleLeft(s, now);
        if (left > 0) {
            cue(Feedback.Cue.ERROR);
            svc.event("capture refused class=" + mode.key + ": fix settling " + left + " s");
            return new Outcome(false, "fix settling: " + left + " s");
        }
        if (mode == SurveyMode.REF) {
            refSeconds = Settings.refSeconds(Settings.prefs(svc));
            refPole = svc.poleHeight();
            refJump = Double.NaN;
            refSuspect = false;
            ref = new RefOccupation(now, refSeconds * 1_000_000_000L);
            svc.event(String.format(Locale.US, "ref start n=%d duration_s=%d pole_height=%.3f quality=%d",
                    counter + 1, refSeconds, refPole, a.quality));
            cue(Feedback.Cue.REF_START);
            notice = new Notice(a.quality == 4 ? "Ref started: hold the pole still"
                    : "Ref started: waiting for RTK fixed", a.quality == 4);
            return new Outcome(true, null);
        }
        PointRow r = PointRow.of(counter + 1, mode.key, now, a, svc.poleHeight(), System.currentTimeMillis());
        r.method = PointRow.SINGLE;
        if (s != null) {
            r.fixAge = s.ageSeconds(a.lastNs);
            r.fixJump = s.jump;
            r.fixSuspect = s.suspect;
        }
        counter++;
        addRow(r);
        String text = String.format(Locale.US, "%s%d  %s  %d ep  ±%.1f cm", label(r.cls), r.n,
                fixName(r.fix), r.nEpochs, 100 * Math.max(r.sdE, r.sdN));
        if (r.fixSuspect) {
            cue(Feedback.Cue.SUSPECT);
            notice = new Notice(text + "  fix may be false (jumped " + jumpText(r.fixJump) + ")", false, true, r);
        } else {
            cue(mode == SurveyMode.SURFACE ? Feedback.Cue.SURFACE : Feedback.Cue.OBJECT);
            notice = new Notice(text, r.fix == 4, false, r);
        }
        return new Outcome(true, null);
    }

    // ---- tilt capture ----

    /** Starts a tilt set in the given mode (its class). */
    synchronized Outcome startTilt(SurveyMode mode) {
        if (tilt != null) return new Outcome(false, "Tilt capture already running");
        if (ref != null) {
            cue(Feedback.Cue.ERROR);
            return new Outcome(false, "Ref running: MARK cancels it");
        }
        long now = SystemClock.elapsedRealtimeNanos();
        tilt = new TiltSet(mode.key, svc.poleHeight(), Settings.tiltGates(Settings.prefs(svc)), now);
        tiltSets++;
        svc.event(String.format(Locale.US, "tilt start set=%d class=%s pole_height=%.3f", tiltSets, mode.key, tilt.pole));
        cue(Feedback.Cue.TILT_START);
        notice = new Notice("Tilt " + mode.label + ": tip on the point, lean 15-25°, hold still, LEAN", true);
        return new Outcome(true, null);
    }

    synchronized boolean tiltRunning() { return tilt != null; }

    /** Takes one lean: the last second of epochs and gravity. */
    synchronized Outcome lean() {
        TiltSet t = tilt;
        if (t == null) return new Outcome(false, "No tilt capture running");
        long now = SystemClock.elapsedRealtimeNanos();
        EpochAverage a = average(now);
        FixGuard.Stretch s = stretchOf(a);
        int left = settleLeft(s, now);
        if (left > 0) {
            cue(Feedback.Cue.ERROR);
            svc.event("tilt lean refused set=" + tiltSets + ": fix settling " + left + " s");
            return new Outcome(false, "fix settling: " + left + " s");
        }
        double[] g = svc.gravityMean(now - WINDOW_NS, now);
        double[] g3 = g == null ? null : new double[] {g[0], g[1], g[2]};
        TiltSet.Added added = t.add(a, g3, g == null ? 0 : (int) g[3], s, now, System.currentTimeMillis());
        if (!added.ok) {
            cue(Feedback.Cue.ERROR);
            svc.event("tilt lean refused set=" + tiltSets + ": " + added.text);
            return new Outcome(false, added.text);
        }
        TiltSet.Lean l = added.lean;
        svc.event(String.format(Locale.US, "tilt lean set=%d k=%d n_epochs=%d sd_h_m=%.4f tilt_deg=%.1f grav=%s",
                tiltSets, l.k, l.avg.n, Math.hypot(l.avg.sdE, l.avg.sdN), l.tiltDeg,
                g3 == null ? "none" : String.format(Locale.US, "%.3f;%.3f;%.3f", g3[0], g3[1], g3[2])));
        if (l.fixSuspect) {
            cue(Feedback.Cue.SUSPECT);
            notice = new Notice(added.text + "  fix may be false (jumped " + jumpText(l.fixJump) + ")", false, true);
        } else {
            cue(Feedback.Cue.LEAN);
            notice = new Notice(added.text, true);
        }
        return new Outcome(true, null);
    }

    /**
     * Finishes the tilt set (MARK): solves, and if the gates warn and not confirmed, returns
     * an Outcome with confirm set and the warnings; otherwise writes the point (and for a
     * ref, refs.csv and the pole check) and every lean to tilt_obs.csv.
     */
    synchronized Outcome finishTilt(boolean confirmed) {
        TiltSet t = tilt;
        if (t == null) return new Outcome(false, "No tilt capture running");
        if (t.count() < TiltSet.MIN_LEANS) {
            cue(Feedback.Cue.ERROR);
            return new Outcome(false, String.format(Locale.US, "Tilt: %d lean%s, need %d (MENU cancels)", t.count(),
                    t.count() == 1 ? "" : "s", TiltSet.MIN_LEANS));
        }
        TiltSet.Result r = t.finish();
        if (!r.ok()) {
            cue(Feedback.Cue.ERROR);
            return new Outcome(false, "Tilt: " + (r.warnings.isEmpty() ? "no solution" : r.warnings.get(0)));
        }
        StringBuilder drops = new StringBuilder();
        for (TiltSet.Lean l : r.dropped) {
            drops.append(drops.length() == 0 ? "dropped " : ", ").append("lean ").append(l.k).append(" (")
                    .append(l.whyDropped.replace(" deg", "°")).append(')');
        }
        if (!r.warnings.isEmpty() && !confirmed) {
            cue(Feedback.Cue.SUSPECT);
            StringBuilder w = new StringBuilder();
            for (String x : r.warnings) w.append("• ").append(x).append('\n');
            if (drops.length() > 0) w.append("(").append(drops).append(")\n");
            return new Outcome(false, w.toString().trim(), true);
        }
        long now = SystemClock.elapsedRealtimeNanos();
        PointRow row = t.toRow(++counter, now, r, System.currentTimeMillis());
        addRow(row);
        int set = tiltSets;
        tilt = null;
        TiltSolver.Solution s = r.solution;
        svc.event(String.format(Locale.US, "tilt end set=%d n=%d class=%s leans=%d used=%d rms_m=%.4f cover_deg=%.0f"
                        + " sd_m=%.4f;%.4f;%.4f imu_max_deg=%s warnings=%s%s", set, row.n, row.cls, t.count(),
                r.used.size(), s.rms, s.coverageDeg, s.sd[0], s.sd[1], s.sd[2],
                Double.isNaN(r.maxImuDeg) ? "none" : String.format(Locale.US, "%.2f", r.maxImuDeg),
                r.warnings.isEmpty() ? "none" : android.text.TextUtils.join(" | ", r.warnings),
                drops.length() > 0 ? " " + drops : ""));
        writeTiltObs(t.obsRows(set, row.n));
        String text = String.format(Locale.US, "%s%d tilt  %d leans  rms %.1f cm  cover %.0f°  σ %.1f cm", label(row.cls),
                row.n, r.used.size(), s.rms * 100, s.coverageDeg, s.maxSd() * 100);
        boolean warn = !r.warnings.isEmpty() || row.fixSuspect;
        if (row.fixSuspect) text += "  fix may be false (jumped " + jumpText(row.fixJump) + ")";
        else if (!r.warnings.isEmpty()) text += "  (accepted with warnings)";
        notice = new Notice(text, !warn, warn, row);
        if (PointRow.REF.equals(row.cls)) {
            PointRow f = row;
            run(() -> poleCheck(f));
        }
        // a suspect ref gets its SUSPECT cue from the pole check
        cue(row.fixSuspect && !PointRow.REF.equals(row.cls) ? Feedback.Cue.SUSPECT : Feedback.Cue.TILT_END);
        return new Outcome(true, drops.length() > 0 ? "Tilt: " + drops + ", re-solved with " + r.used.size() : null);
    }

    /** Cancels the tilt set; its leans still go to tilt_obs.csv (n empty, used 0). */
    synchronized Outcome cancelTilt(String why) {
        TiltSet t = tilt;
        if (t == null) return new Outcome(false, null);
        tilt = null;
        svc.event("tilt " + why + " set=" + tiltSets + " leans=" + t.count());
        if (t.count() > 0) writeTiltObs(t.obsRows(tiltSets, 0));
        cue(Feedback.Cue.REF_CANCEL);
        notice = new Notice("Tilt capture cancelled", false);
        return new Outcome(true, null);
    }

    synchronized TiltView tiltView() {
        TiltSet t = tilt;
        if (t == null) return null;
        long now = SystemClock.elapsedRealtimeNanos();
        double az = Double.NaN, tl = Double.NaN;
        TiltSet.Result r = t.provisional();
        Epoch e = epochs.peekLast();
        if (r != null && r.solution != null && e != null && e.hasPosition() && !Double.isNaN(e.h) && t.frame() != null) {
            double[] p = t.frame().toEnu(e.lat, e.lon, e.h);
            double[] c = r.solution.tip;
            double[] v = {p[0] - c[0], p[1] - c[1], p[2] - c[2]};
            az = TiltSolver.azimuth(v[0], v[1]);
            tl = TiltSolver.angleDeg(v, new double[] {0, 0, 1});
        }
        return new TiltView(t, az, tl, guard.settleRemainingSeconds(now));
    }

    /** The guard's state for the fix chip, at now. */
    synchronized FixState fixState() {
        long now = SystemClock.elapsedRealtimeNanos();
        FixGuard.Stretch c = guard.current();
        return new FixState(guard.settleRemainingSeconds(now), c != null && c.suspect, c == null ? Double.NaN : c.jump);
    }

    private Outcome cancelRef(String why) {
        RefOccupation r = ref;
        ref = null;
        svc.event(String.format(Locale.US, "ref %s after %.0f s, %d fixed epochs", why, r.activeSeconds(), r.epochs()));
        cue(Feedback.Cue.REF_CANCEL);
        notice = new Notice("Ref cancelled", false);
        return new Outcome(true, null);
    }

    /** Adds a row already numbered (called with the lock held) and writes points.csv. */
    private PointRow addRow(PointRow r) {
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
        String sus = r.fixSuspect ? "  fix may be false (jumped " + jumpText(r.fixJump) + ")" : "";
        if (r.fixSuspect) cue(Feedback.Cue.SUSPECT);
        if (c == null) {
            text = Double.isNaN(r.hGround) ? "Ref R" + r.n + " saved (no ground height: no pole check)"
                    : "Ref R" + r.n + " saved: first visit to this mark";
            notice = new Notice(text + sus, !r.fixSuspect, r.fixSuspect, r);
            poleWarning = null;
            svc.event("pole check n=" + r.n + ": " + (Double.isNaN(r.hGround) ? "no ground height" : "no earlier ref within 0.30 m"));
            return;
        }
        text = c.describe();
        svc.event(String.format(Locale.US, "pole check n=%d: %s; diff_last_m=%.4f diff_mean_m=%.4f visits=%d last=%s/%d ok=%b",
                r.n, text, c.diffLast, c.diffMean, c.visits, c.last.session, c.last.n, c.ok));
        if (c.ok) {
            poleWarning = null;
            notice = new Notice(text + sus, !r.fixSuspect, r.fixSuspect, r);
            if (!r.fixSuspect) cue(Feedback.Cue.POLE_OK);
        } else {
            poleWarning = text + " (R" + r.n + "; check the pole height)";
            notice = new Notice(text + sus, false, false, r);
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
            notice = new Notice("Photo on " + label(target.cls) + target.n, true, false, target);
        } else {
            EpochAverage a = average(SystemClock.elapsedRealtimeNanos());
            PointRow r = PointRow.of(++counter, PointRow.PHOTO, p.pressNs, a, svc.poleHeight(), System.currentTimeMillis());
            r.photo = name;
            rows.add(r);
            svc.event("photo file=photos/" + name + " bytes=" + p.file.length() + " own row n=" + r.n);
            svc.event("capture " + r.toCsv());
            savePoints();
            notice = new Notice("Photo saved" + (a == null ? " (no fix)" : ""), a != null, false, r);
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
        long now = SystemClock.elapsedRealtimeNanos();
        return ref == null ? null : new RefView(ref, refSeconds, now, guard.settling(now));
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

    /** Appends rows to tilt_obs.csv in the session folder (header first when new). */
    private void writeTiltObs(List<String> lines) {
        run(() -> {
            File f = new File(dir, "tilt_obs.csv");
            boolean fresh = !f.exists() || f.length() == 0;
            try (Writer w = new OutputStreamWriter(new FileOutputStream(f, true), StandardCharsets.UTF_8)) {
                if (fresh) w.write(TiltSet.OBS_HEADER + "\n");
                for (String l : lines) w.write(l + "\n");
            } catch (IOException e) {
                svc.event("tilt_obs.csv write failed: " + e.getMessage());
            }
        });
    }

    private void run(Runnable r) {
        try { io.execute(r); } catch (java.util.concurrent.RejectedExecutionException ignored) { }
    }

    private void cue(Feedback.Cue c) { Feedback.play(svc, c); }

    /** Ends a running ref and finishes the file writes (session stop). */
    void close() {
        synchronized (this) {
            if (ref != null) cancelRef("cancelled (session stop)");
            if (tilt != null) cancelTilt("cancelled (session stop)");
        }
        io.shutdown();
        try { io.awaitTermination(5, TimeUnit.SECONDS); } catch (InterruptedException ignored) { }
    }
}
