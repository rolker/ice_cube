package rink.icecube.logger;

import android.content.SharedPreferences;
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

import rink.icecube.logger.core.AutoCapture;
import rink.icecube.logger.core.Epoch;
import rink.icecube.logger.core.EpochAverage;
import rink.icecube.logger.core.FixGuard;
import rink.icecube.logger.core.LocalFrame;
import rink.icecube.logger.core.PhotoRule;
import rink.icecube.logger.core.PointRow;
import rink.icecube.logger.core.PoleAxis;
import rink.icecube.logger.core.PoleCalibration;
import rink.icecube.logger.core.PoleCheck;
import rink.icecube.logger.core.RefOccupation;
import rink.icecube.logger.core.SurveyMode;
import rink.icecube.logger.core.TiltSet;
import rink.icecube.logger.core.TiltSolver;
import rink.icecube.logger.core.VoidRule;

/**
 * The captures of one session: MARK in Surface, Object or Ref mode and PHOTO, written to
 * points.csv in the session folder (header line, one row per capture; rewritten whole on
 * each change, which is tiny) and as "capture ..." events in events.csv. Refs are also
 * appended to refs.csv and checked against earlier visits (the pole check).
 *
 * The false-fix guard ({@link FixGuard}) follows the RTK fixed stretches: captures, refs
 * and tilt leans from a young fix go ahead with an amber notice and the YOUNG cue and are
 * marked (fix_young; logger 0.11.0, #15: until then they were held back), and rows from a
 * stretch that began with a large jump or right after a long float spell are flagged
 * (fix_suspect, suspect_reason) with an amber notice.
 *
 * Tilt capture ({@link TiltSet}): startTilt, then lean() per lean, finishTilt (MARK) or
 * cancelTilt; every lean goes to tilt_obs.csv.
 *
 * Auto capture ({@link AutoCapture}, logger 0.13.0, #35): every epoch goes to the arming
 * state machine with the pole's angle from plumb (phone gravity against the pole axis this
 * session's {@link PoleCalibration} found); when it fires, the capture takes the same path as
 * a Surface MARK ({@link #capture}), method auto. Every capture at a stop (auto, a Surface MARK,
 * a Surface tilt set's tip, an accepted calibration's tip) latches it, until the pole moves
 * away (see AutoCapture); undoing the latched capture clears it.
 * The calibration runs on the epochs too (startCalibration / cancelCalibration); MARK, tilt
 * sets and refs are refused while it runs, and it is refused while they run.
 *
 * Flagging ({@link #voidCapture}, {@link #unvoidCapture}, logger 0.9.0): a capture row is
 * marked void, never removed; its photos move (see {@link VoidRule}); the flag goes to
 * points.csv (rewritten), to events.csv (flag / unflag / photo moved, the audit trail) and,
 * for a ref, to its row in every refs.csv. The phone never reopens a session, so refs.csv
 * is the only void state read back (by the pole check and the earlier-refs layer).
 *
 * GGA epochs arrive on the GPS thread through {@link #onEpoch}; MARK and PHOTO come from
 * the screen. Everything that touches the rows is synchronized; files are written on a
 * single background thread in order.
 */
final class Survey {
    static final long WINDOW_NS = 1_000_000_000L, MAX_AGE_NS = 2_000_000_000L, KEEP_NS = 10_000_000_000L;
    static final long NOTICE_MS = 10_000;
    /** Auto capture: the plumb angle is from the gravity of the last 0.5 s before each epoch. */
    static final long PLUMB_WINDOW_NS = 500_000_000L;
    /**
     * Pole calibration: each sample's gravity is the mean over +-150 ms about 100 ms before
     * the GGA arrived (the receiver's output delay; a guess the first field run should check).
     */
    static final long CAL_LATENCY_NS = 100_000_000L, CAL_HALF_NS = 150_000_000L;
    /** The auto line reads "not RTK fixed" once the last epoch is older than this. */
    static final long AUTO_STALE_NS = 3_000_000_000L;

    /**
     * A short message for the map, green (ok), amber (warn) or red, shown until untilMs
     * (uptime). at is the capture it reports, or null; the map adds its x/y/z in the map frame.
     * undoable: the notice of a capture of its own (surface or object MARK, tilt end, ref
     * end), which offers UNDO of at while {@link VoidRule#undoOpen} holds for at.elapsedNs
     * (the same elapsedRealtime clock Survey checks again). A photo notice is never
     * undoable, so UNDO never flags the capture a photo landed on.
     */
    static final class Notice {
        final String text;
        final boolean ok, warn, undoable;
        final long untilMs;
        final PointRow at;
        Notice(String text, boolean ok) { this(text, ok, false); }
        Notice(String text, boolean ok, boolean warn) { this(text, ok, warn, null, false); }
        Notice(String text, boolean ok, boolean warn, PointRow at, boolean undoable) {
            this.text = text; this.ok = ok; this.warn = warn; this.at = at; this.undoable = undoable && at != null;
            untilMs = SystemClock.uptimeMillis() + NOTICE_MS;
        }
    }

    /**
     * The fix chip's guard state: the young fix's age in whole seconds (-1: not young), and
     * whether the stretch is suspect, by its jump (else after a float spell), and the jump.
     */
    static final class FixState {
        final int youngS;
        final boolean suspect, byJump;
        final double jump;
        FixState(int youngS, boolean suspect, boolean byJump, double jump) {
            this.youngS = youngS; this.suspect = suspect; this.byJump = byJump; this.jump = jump;
        }
    }

    /** What the map's tilt overlay shows. Arrays per lean; azimuths and tilts NaN before 3 leans. */
    static final class TiltView {
        final String cls;
        final int count;
        final double[] azimuth, tilt, imu;
        final boolean[] imuBad;
        final double coverage, rms, maxImu, nowAzimuth, nowTilt, pole;
        final double[] sd;
        final int youngS;
        TiltView(TiltSet t, double nowAzimuth, double nowTilt, int youngS) {
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
            this.youngS = youngS;
        }
    }

    /**
     * The map's auto capture line: text, and its colour kind (CALIBRATE: a calibration runs or
     * is needed, tap to start; OFF: off for the fix; IDLE: off for a tilt set or ref; READY:
     * armed; DONE: captured here). Null when the line is hidden (setting off, not Surface).
     */
    static final class AutoView {
        enum Kind { CALIBRATE, OFF, IDLE, READY, DONE }
        final String text;
        final Kind kind;
        final boolean calibrating, needsCalibration;
        AutoView(String text, Kind kind, boolean calibrating, boolean needsCalibration) {
            this.text = text; this.kind = kind; this.calibrating = calibrating; this.needsCalibration = needsCalibration;
        }
    }

    /** What the map shows while a ref occupation runs. */
    static final class RefView {
        final double remainingS, progress;
        final int epochs, durationS;
        final boolean paused;
        /** The ref will be marked young: a counted epoch was young (the latch), or the fix is young now. */
        final boolean young;
        RefView(RefOccupation r, int durationS, long nowNs, boolean young) {
            remainingS = r.remainingSeconds(); progress = r.progress(); epochs = r.epochs();
            this.durationS = durationS; this.young = young; paused = r.pausedAt(nowNs);
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
    /** Counts flag and unflag changes, so the coverage overlay rebuilds without voided rows. */
    private volatile int voidGeneration;
    /** The ref whose failed pole check raised poleWarning (0: none). Set under the lock. */
    private volatile int poleWarningN;
    private final FixGuard guard;
    private double refJump = Double.NaN;
    private boolean refSuspect, refYoung, refWhyJump, refWhySpell;
    private TiltSet tilt;
    private int tiltSets;
    private final AutoCapture auto;
    private PoleCalibration calibration;
    private PoleAxis poleAxis;
    private int calibrations;
    private LocalFrame autoFrame;          // horizontal positions for auto capture: any fixed local frame
    private AutoCapture.Decision autoLast;
    private long autoLastNs;
    private String autoLastLabel = "";
    private int autoLatchN;                // the capture row auto capture is latched on, 0 for none or the calibration

    Survey(LoggerService svc, File dir) {
        this.svc = svc;
        this.dir = dir;
        photos = new File(dir, "photos");
        guard = new FixGuard(Settings.settleNs(Settings.prefs(svc)), Settings.jumpM(Settings.prefs(svc)),
                Settings.floatSpellNs(Settings.prefs(svc)));
        SharedPreferences p = Settings.prefs(svc);
        auto = new AutoCapture(Settings.autoPlumbDeg(p), Settings.autoHoldS(p), Settings.autoStillM(p));
        writePoints(new ArrayList<String>());   // points.csv with its header from the start
    }

    /** Settings changed: the auto capture limits apply from the next epoch. */
    synchronized void applyAutoSettings() {
        SharedPreferences p = Settings.prefs(svc);
        auto.setLimits(Settings.autoPlumbDeg(p), Settings.autoHoldS(p), Settings.autoStillM(p));
    }

    /** Settings changed: the young limit applies at once, the jump and float spell thresholds from the next stretch. */
    synchronized void applyGuardSettings() {
        guard.setLimits(Settings.settleNs(Settings.prefs(svc)), Settings.jumpM(Settings.prefs(svc)),
                Settings.floatSpellNs(Settings.prefs(svc)));
    }

    // ---- GPS epochs ----

    void onEpoch(Epoch e) {
        if (e == null) return;
        synchronized (this) {
            epochs.addLast(e);
            while (epochs.peekFirst().elapsedNs < e.elapsedNs - KEEP_NS) epochs.removeFirst();
            FixGuard.Stretch st = guard.onEpoch(e);
            if (st != null) svc.event(st.eventText());
            calibrationEpoch(e);
            autoEpoch(e);
            RefOccupation r = ref;
            if (r == null) return;
            // every fixed epoch counts (0.11.0, #15: young epochs are no longer paused); the
            // ref is young if any counted epoch was (worst case, as for tilt rows). Latch only
            // for an epoch RefOccupation counted (not one stamped before the ref start, #15)
            int counted = r.epochs();
            RefOccupation.Change change = r.onEpoch(e);
            if (r.epochs() > counted) {
                FixGuard.Stretch cur = guard.current();
                if (cur != null) {
                    if (!Double.isNaN(cur.jump)) refJump = Double.isNaN(refJump) ? cur.jump : Math.max(refJump, cur.jump);
                    refSuspect |= cur.suspect;
                    refWhyJump |= cur.reason.contains(FixGuard.REASON_JUMP);
                    refWhySpell |= cur.reason.contains(FixGuard.REASON_FLOAT_SPELL);
                    refYoung |= guard.young(e.elapsedNs);
                }
            }
            switch (change) {
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
                    PointRow finished = PointRow.of(++counter, PointRow.REF, e.elapsedNs, r.average(), refPole,
                            System.currentTimeMillis());
                    finished.method = PointRow.OCCUPATION;
                    // fix_age_s, fix_start, float_spell_s: the last epoch's stretch; jump, suspect
                    // and young: worst over the counted epochs
                    finished.setStretch(guard.current(), e.elapsedNs, refYoung);
                    finished.fixJump = refJump;
                    finished.fixSuspect = refSuspect;
                    finished.suspectReason = reasons(refWhyJump, refWhySpell);
                    finished.fixYoung = refYoung ? 1 : 0;
                    addRow(finished);
                    svc.event(String.format(Locale.US, "ref end n=%d epochs=%d pauses=%d young=%d", finished.n, r.epochs(),
                            r.pauses(), finished.fixYoung));
                    // one fix cue: SUSPECT (from the pole check) wins over YOUNG, as for MARK
                    cue(refYoung && !refSuspect ? Feedback.Cue.YOUNG : Feedback.Cue.REF_END);
                    // Queued under the lock, like finishTilt: a voidCapture right after this
                    // epoch then queues its refs.csv rewrite after the append, never before
                    // (which would find no row and log a false error). The rewrite of a row
                    // not yet appended stays an error in VoidRule.refsVoidEvent, because a
                    // failed append is a real one.
                    PointRow f = finished;
                    run(() -> poleCheck(f));
                    break;
                default:
                    break;
            }
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

    private static String jumpText(double j) { return Double.isNaN(j) ? "?" : String.format(Locale.US, "%.1f m", j); }

    /** "jump", "float_spell", "jump;float_spell" or "". */
    private static String reasons(boolean jump, boolean spell) {
        return jump && spell ? FixGuard.REASON_JUMP + ";" + FixGuard.REASON_FLOAT_SPELL
                : jump ? FixGuard.REASON_JUMP : spell ? FixGuard.REASON_FLOAT_SPELL : "";
    }

    /** The amber notice text for a suspect fix: why, and the jump if that is a reason. */
    private static String suspectText(PointRow r) {
        if (FixGuard.REASON_FLOAT_SPELL.equals(r.suspectReason)) {
            return "  fix may be false (after a long float spell)";
        }
        return "  fix may be false (jumped " + jumpText(r.fixJump) + ")";
    }

    /**
     * "  fix young: 95 s" for a young row, else "". An occupation ref is young if any counted
     * epoch was, while its fix_age_s is the last epoch's (often past the limit), so it reads
     * "  fix young during the ref" without seconds; so does any row whose age is unknown or
     * not under the young limit.
     */
    private String youngText(PointRow r) {
        if (r.fixYoung != 1) return "";
        if (PointRow.OCCUPATION.equals(r.method)) return "  fix young during the ref";
        return Double.isNaN(r.fixAge) || r.fixAge * 1e9 >= guard.youngNs() ? "  fix young"
                : String.format(Locale.US, "  fix young: %.0f s", Math.floor(r.fixAge));
    }

    synchronized Outcome mark(SurveyMode mode) {
        long now = SystemClock.elapsedRealtimeNanos();
        if (calibration != null) {
            cue(Feedback.Cue.ERROR);
            return new Outcome(false, "Calibrating the pole: MENU cancels");
        }
        if (tilt != null) return finishTilt(false);
        if (ref != null) return cancelRef("cancelled by MARK");
        EpochAverage a = average(now);
        if (a == null) {
            cue(Feedback.Cue.ERROR);
            svc.event("capture refused class=" + mode.key + ": no fix");
            return new Outcome(false, "No fix: nothing captured");
        }
        if (mode == SurveyMode.REF) {
            refSeconds = Settings.refSeconds(Settings.prefs(svc));
            refPole = svc.poleHeight();
            refJump = Double.NaN;
            refSuspect = refYoung = refWhyJump = refWhySpell = false;
            ref = new RefOccupation(now, refSeconds * 1_000_000_000L);
            svc.event(String.format(Locale.US, "ref start n=%d duration_s=%d pole_height=%.3f quality=%d",
                    counter + 1, refSeconds, refPole, a.quality));
            cue(Feedback.Cue.REF_START);
            notice = new Notice(a.quality == 4 ? "Ref started: hold the pole still"
                    : "Ref started: waiting for RTK fixed", a.quality == 4);
            return new Outcome(true, null);
        }
        PointRow r = capture(mode, now, a, PointRow.SINGLE);
        if (mode == SurveyMode.SURFACE) autoTaken(r.lat, r.lon, r.hAnt, label(r.cls) + r.n, r.n);
        return new Outcome(true, null);
    }

    /**
     * A capture happened at this stop (Surface MARK, auto, a Surface tilt set's tip, an
     * accepted calibration's tip): latch auto capture there, so no auto capture follows until
     * the pole moves away. n is its row (0 for the calibration), for UNDO. Lock held.
     */
    private void autoTaken(double lat, double lon, double h, String label, int n) {
        double[] p = autoEnu(lat, lon, h);
        if (p == null) return;
        auto.taken(p[0], p[1]);
        autoLastLabel = label;
        autoLatchN = n;
    }

    /**
     * A Surface or Object capture from the 1 s average a (not null): the row (with the fix
     * guard's columns), its event, notice and cue. MARK (method single) and auto capture
     * (method auto) share it. Called with the lock held.
     */
    private PointRow capture(SurveyMode mode, long now, EpochAverage a, String method) {
        FixGuard.Stretch s = stretchOf(a);
        PointRow r = PointRow.of(counter + 1, mode.key, now, a, svc.poleHeight(), System.currentTimeMillis());
        r.method = method;
        r.setStretch(s, a.lastNs, guard.young(s, a.lastNs));   // young at the time fix_age_s is taken
        counter++;
        addRow(r);
        String text = String.format(Locale.US, "%s%d%s  %s  %d ep  ±%.1f cm", label(r.cls), r.n,
                PointRow.AUTO.equals(method) ? " auto" : "", fixName(r.fix), r.nEpochs, 100 * Math.max(r.sdE, r.sdN));
        if (r.fixSuspect) {
            cue(Feedback.Cue.SUSPECT);
            notice = new Notice(text + suspectText(r) + youngText(r), false, true, r, true);
        } else if (r.fixYoung == 1) {
            cue(Feedback.Cue.YOUNG);
            notice = new Notice(text + youngText(r), false, true, r, true);
        } else {
            cue(mode == SurveyMode.SURFACE ? Feedback.Cue.SURFACE : Feedback.Cue.OBJECT);
            notice = new Notice(text, r.fix == 4, false, r, true);
        }
        return r;
    }

    // ---- auto capture and pole calibration ----

    /** Horizontal position (e, n) in the auto capture frame, set at the first call; null without a position. */
    private double[] autoEnu(double lat, double lon, double h) {
        if (Double.isNaN(lat) || Double.isNaN(lon)) return null;
        double hh = Double.isNaN(h) ? 0 : h;
        if (autoFrame == null) autoFrame = new LocalFrame(lat, lon, hh);
        return autoFrame.toEnu(lat, lon, hh);
    }

    /** Feeds the epoch to auto capture and captures when it fires (lock held). */
    private void autoEpoch(Epoch e) {
        SharedPreferences p = Settings.prefs(svc);
        AutoCapture.Input in = new AutoCapture.Input();
        in.tNs = e.elapsedNs;
        in.enabled = Settings.autoCapture(p);
        in.surface = Settings.mode(p) == SurveyMode.SURFACE;
        in.calibrating = calibration != null;
        in.calibrated = poleAxis != null;
        in.tiltRunning = tilt != null;
        in.refRunning = ref != null;
        in.fixed = e.fixed();
        FixGuard.Stretch cur = guard.current();
        in.young = guard.young(e.elapsedNs);
        in.suspect = cur != null && cur.suspect;
        if (e.fixed()) {
            double[] h = autoEnu(e.lat, e.lon, e.h);
            in.e = h[0];
            in.n = h[1];
        }
        in.plumbDeg = poleAxis == null ? Double.NaN
                : poleAxis.plumbDeg(svc.gravityMean(e.elapsedNs - PLUMB_WINDOW_NS, e.elapsedNs));
        AutoCapture.Decision d = auto.onEpoch(in);
        autoLast = d;
        autoLastNs = e.elapsedNs;
        if (d.cue == AutoCapture.Cue.ARMED) {
            svc.event("auto capture armed");
            cue(Feedback.Cue.AUTO_ARMED);
        } else if (d.cue == AutoCapture.Cue.OFF) {
            svc.event("auto capture off: " + d.why.name().toLowerCase(Locale.US));
            cue(Feedback.Cue.AUTO_OFF);
        }
        if (!d.fire) return;
        long now = SystemClock.elapsedRealtimeNanos();
        EpochAverage a = average(now);
        if (a == null || a.quality != 4) {   // cannot happen after a fixed epoch; never capture a doubt
            svc.event("auto capture skipped: no RTK fixed average");
            return;
        }
        PointRow r = capture(SurveyMode.SURFACE, now, a, PointRow.AUTO);
        auto.taken(d.e, d.n);   // latched only now that the capture happened
        autoLastLabel = label(r.cls) + r.n;
        autoLatchN = r.n;
        svc.event(String.format(Locale.US, "auto capture n=%d plumb_deg=%.1f held_s=%.1f spread_cm=%.1f epochs=%d",
                r.n, in.plumbDeg, d.heldS, d.spreadM * 100, d.epochs));
    }

    /** Feeds the epoch to a running pole calibration (lock held). */
    private void calibrationEpoch(Epoch e) {
        PoleCalibration c = calibration;
        if (c == null) return;
        long at = e.elapsedNs - CAL_LATENCY_NS;
        double[] g = svc.gravityMean(at - CAL_HALF_NS, at + CAL_HALF_NS);
        double[] g3 = g == null ? null : new double[] {g[0], g[1], g[2]};
        switch (c.onEpoch(e, g3)) {
            case RESTART:
                cue(Feedback.Cue.ERROR);
                notice = new Notice("Calibrate: moved, hold the pole still again", false, true);
                break;
            case LEAN:
                svc.event("pole calibration lean set=" + calibrations);
                cue(Feedback.Cue.CAL_PHASE);
                break;
            case TWIST:
                svc.event(String.format(Locale.US, "pole calibration twist set=%d samples=%d skipped=%d", calibrations,
                        c.samples(), c.skipped()));
                cue(Feedback.Cue.CAL_PHASE);
                break;
            case DONE:
                calibration = null;
                poleAxis = c.result();
                auto.reset();
                double[] tip = c.tipGeodetic();   // the calibration point is this stop's capture
                if (tip != null) autoTaken(tip[0], tip[1], tip[2], "the calibration point", 0);
                PoleAxis a = poleAxis;
                // the axis is the phone's mounting on the pole (phone axes), not a position
                svc.event(String.format(Locale.US, "pole axis calibrated set=%d residual_deg=%.2f samples=%d"
                                + " twist_samples=%d skipped=%d cover_deg=%.0f rms_m=%.4f axis=%.4f;%.4f;%.4f", calibrations,
                        a.residualDeg, a.samples, a.twistSamples, c.skipped(), a.coverageDeg, a.rmsM, a.axis[0],
                        a.axis[1], a.axis[2]));
                cue(Feedback.Cue.CAL_DONE);
                notice = new Notice(String.format(Locale.US, "Pole calibrated: %d samples, %.1f° RMS. Auto capture on",
                        a.samples, a.residualDeg), true);
                break;
            case FAILED:
                calibration = null;
                svc.event("pole calibration failed set=" + calibrations + ": " + c.failure());
                cue(Feedback.Cue.ERROR);
                notice = new Notice("Calibration failed: " + c.failure(), false, true);
                break;
            default:
                break;
        }
    }

    /** Starts a pole calibration; the previous axis (if any) is dropped. */
    synchronized Outcome startCalibration() {
        if (calibration != null) return new Outcome(false, "Calibration already running");
        if (tilt != null || ref != null) {
            cue(Feedback.Cue.ERROR);
            return new Outcome(false, "Finish or cancel the " + (tilt != null ? "tilt set" : "ref") + " first");
        }
        long now = SystemClock.elapsedRealtimeNanos();
        calibration = new PoleCalibration(svc.poleHeight(), Settings.tiltGates(Settings.prefs(svc)), now);
        poleAxis = null;
        auto.reset();
        autoLatchN = 0;
        calibrations++;
        svc.event(String.format(Locale.US, "pole calibration start set=%d pole_height=%.3f", calibrations,
                calibration.pole));
        cue(Feedback.Cue.CAL_START);
        notice = new Notice("Calibrate: tip on a point, hold still, then lean all round, then twist twice", true);
        return new Outcome(true, null);
    }

    synchronized Outcome cancelCalibration(String why) {
        if (calibration == null) return new Outcome(false, null);
        calibration = null;
        svc.event("pole calibration " + why + " set=" + calibrations);
        cue(Feedback.Cue.REF_CANCEL);
        notice = new Notice("Calibration cancelled: auto capture stays off", false);
        return new Outcome(true, null);
    }

    synchronized boolean calibrating() { return calibration != null; }
    synchronized boolean calibrated() { return poleAxis != null; }

    /** The auto capture line for the map (see AutoView), or null when hidden. */
    synchronized AutoView autoView() {
        long now = SystemClock.elapsedRealtimeNanos();
        PoleCalibration c = calibration;
        if (c != null) {
            int left = (int) Math.ceil(c.remainingS(now));
            String text;
            switch (c.phase()) {
                case STILL: text = "CALIBRATE 1/3: tip on a point, hold still  " + left + " s"; break;
                case LEAN: text = "CALIBRATE 2/3: lean slowly all round  " + left + " s"; break;
                default: text = "CALIBRATE 3/3: twist the pole slowly, twice  " + left + " s"; break;
            }
            return new AutoView(text, AutoView.Kind.CALIBRATE, true, false);
        }
        SharedPreferences p = Settings.prefs(svc);
        if (!Settings.autoCapture(p) || Settings.mode(p) != SurveyMode.SURFACE) return null;
        if (poleAxis == null) return new AutoView("AUTO: calibrate to enable (tap)", AutoView.Kind.CALIBRATE, false, true);
        if (tilt != null) return new AutoView("AUTO: off during the tilt set", AutoView.Kind.IDLE, false, false);
        if (ref != null) return new AutoView("AUTO: off during the ref", AutoView.Kind.IDLE, false, false);
        AutoCapture.Decision d = autoLast;
        if (d == null || now - autoLastNs > AUTO_STALE_NS) {
            return new AutoView("AUTO: off, not RTK fixed", AutoView.Kind.OFF, false, false);
        }
        switch (d.why) {
            case NO_FIX: return new AutoView("AUTO: off, not RTK fixed", AutoView.Kind.OFF, false, false);
            case FIX_YOUNG:
                int y = guard.youngAgeSeconds(now);
                return new AutoView(y >= 0 ? "AUTO: off, fix young " + y + " s" : "AUTO: off, fix young",
                        AutoView.Kind.OFF, false, false);
            case FIX_SUSPECT: return new AutoView("AUTO: off, fix may be false", AutoView.Kind.OFF, false, false);
            case NOT_PLUMB: {
                double plumb = poleAxis.plumbDeg(svc.gravityMean(now - PLUMB_WINDOW_NS, now));
                return new AutoView(Double.isNaN(plumb) ? "AUTO: ready, hold plumb (no gravity)"
                        : String.format(Locale.US, "AUTO: ready, hold plumb  %.1f°", plumb), AutoView.Kind.READY, false, false);
            }
            case MOVING: return new AutoView("AUTO: ready, hold still", AutoView.Kind.READY, false, false);
            case HOLDING:
                return new AutoView(String.format(Locale.US, "AUTO: holding  %.0f / %.0f s", d.heldS, auto.holdS()),
                        AutoView.Kind.READY, false, false);
            case CAPTURED:
                if (!auto.latched()) {   // a skipped fire, or the capture was undone: not taken
                    return new AutoView("AUTO: ready, hold plumb and still", AutoView.Kind.READY, false, false);
                }
                return new AutoView("AUTO: " + autoLastLabel + " taken, move to the next point", AutoView.Kind.DONE,
                        false, false);
            default:   // the gates above, seen before this frame caught up
                return new AutoView("AUTO: off", AutoView.Kind.IDLE, false, false);
        }
    }

    // ---- tilt capture ----

    /** Starts a tilt set in the given mode (its class). */
    synchronized Outcome startTilt(SurveyMode mode) {
        if (tilt != null) return new Outcome(false, "Tilt capture already running");
        if (calibration != null) {
            cue(Feedback.Cue.ERROR);
            return new Outcome(false, "Calibrating the pole: MENU cancels");
        }
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
        boolean young = a != null && guard.young(s, a.lastNs);   // at the time the lean's fix_age_s is taken
        double[] g = svc.gravityMean(now - WINDOW_NS, now);
        double[] g3 = g == null ? null : new double[] {g[0], g[1], g[2]};
        TiltSet.Added added = t.add(a, g3, g == null ? 0 : (int) g[3], s, young, now, System.currentTimeMillis());
        if (!added.ok) {
            cue(Feedback.Cue.ERROR);
            svc.event("tilt lean refused set=" + tiltSets + ": " + added.text);
            return new Outcome(false, added.text);
        }
        TiltSet.Lean l = added.lean;
        svc.event(String.format(Locale.US, "tilt lean set=%d k=%d n_epochs=%d sd_h_m=%.4f tilt_deg=%.1f grav=%s young=%d",
                tiltSets, l.k, l.avg.n, Math.hypot(l.avg.sdE, l.avg.sdN), l.tiltDeg,
                g3 == null ? "none" : String.format(Locale.US, "%.3f;%.3f;%.3f", g3[0], g3[1], g3[2]), l.young ? 1 : 0));
        String youngNote = l.young ? String.format(Locale.US, "  fix young: %.0f s", Math.floor(l.fixAge)) : "";
        if (l.fixSuspect) {
            cue(Feedback.Cue.SUSPECT);
            String why = l.stretch != null && FixGuard.REASON_FLOAT_SPELL.equals(l.stretch.reason)
                    ? "  fix may be false (after a long float spell)" : "  fix may be false (jumped " + jumpText(l.fixJump) + ")";
            notice = new Notice(added.text + why + youngNote, false, true);
        } else if (l.young) {
            cue(Feedback.Cue.YOUNG);
            notice = new Notice(added.text + youngNote, false, true);
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
        // a Surface set is this stop's capture: standing the pole up on its tip takes no auto capture
        if (SurveyMode.SURFACE.key.equals(row.cls)) autoTaken(row.lat, row.lon, row.hAnt, label(row.cls) + row.n, row.n);
        String text = String.format(Locale.US, "%s%d tilt  %d leans  rms %.1f cm  cover %.0f°  σ %.1f cm", label(row.cls),
                row.n, r.used.size(), s.rms * 100, s.coverageDeg, s.maxSd() * 100);
        boolean warn = !r.warnings.isEmpty() || row.fixSuspect || row.fixYoung == 1;
        if (row.fixSuspect) text += suspectText(row);
        else if (!r.warnings.isEmpty()) text += "  (accepted with warnings)";
        text += youngText(row);
        notice = new Notice(text, !warn, warn, row, true);
        if (PointRow.REF.equals(row.cls)) {
            PointRow f = row;
            run(() -> poleCheck(f));
        }
        // a suspect or young ref gets its SUSPECT or YOUNG cue from the pole check
        boolean isRef = PointRow.REF.equals(row.cls);
        cue(row.fixSuspect && !isRef ? Feedback.Cue.SUSPECT : row.fixYoung == 1 && !isRef ? Feedback.Cue.YOUNG
                : Feedback.Cue.TILT_END);
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
        return new TiltView(t, az, tl, guard.youngAgeSeconds(now));
    }

    /** The guard's state for the fix chip, at now. */
    synchronized FixState fixState() {
        long now = SystemClock.elapsedRealtimeNanos();
        FixGuard.Stretch c = guard.current();
        return new FixState(guard.youngAgeSeconds(now), c != null && c.suspect,
                c != null && c.reason.contains(FixGuard.REASON_JUMP), c == null ? Double.NaN : c.jump);
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
        if (r.isLive()) { lastCapture = r; captures++; }
        svc.event("capture " + r.toCsv());
        savePoints();
        return r;
    }

    // ---- ref: pole check (background thread) ----

    private void poleCheck(PointRow r) {
        r.session = dir.getName();
        List<PointRow> earlier = RefsStore.readAll(svc);
        SharedPreferences p = Settings.prefs(svc);
        PoleCheck c = PoleCheck.check(r, earlier, Settings.poleTolerance(p));
        PoleCheck.Cues q = PoleCheck.Cues.of(r, earlier, c, Settings.placeM(p), Settings.steadyM(p));
        PoleCheck.Colour colour = PoleCheck.colour(c, q);
        try {
            File f = RefsStore.append(svc, r);
            svc.event("ref n=" + r.n + " appended to " + f);
        } catch (IOException e) {
            svc.event("ref n=" + r.n + " not appended to refs.csv: " + e.getMessage());
        }
        refsGeneration++;
        // The importer (analysis/qgis/logger_session.py) takes the text before the first
        // "; key=" as the message (importers before 0.12.0: before "; diff_last_m=", so
        // diff_last_m stays the first key of a check) and ok=false as a failed (red) check,
        // so ok is "not red": a doubtful reference outside the tolerance logs ok=true doubtful=true.
        // place_cm, sd_u_cm and colour are on every check and no-check line (not the voided one), a value left empty when not applicable.
        String cues = String.format(Locale.US, "place_cm=%s sd_u_cm=%s colour=%s", cmOrEmpty(q.placeOffset),
                cmOrEmpty(q.sdU), colour.name().toLowerCase(Locale.US));
        if (c != null) {
            svc.event(String.format(Locale.US, "pole check n=%d: %s%s; diff_last_m=%.4f diff_mean_m=%.4f visits=%d last=%s/%d"
                            + " ok=%b diff_ref_m=%.4f ref_kind=%s within_tol=%b doubtful=%b %s", r.n, c.describe(), q.describe(),
                    c.diffLast, c.diffMean, c.visits, c.last.session, c.last.n, !c.failed(), c.diffRef,
                    c.refKind.name().toLowerCase(Locale.US), c.ok, c.doubtful, cues));
        } else {
            svc.event("pole check n=" + r.n + ": " + (Double.isNaN(r.hGround) ? "no ground height" : "no earlier ref within 0.30 m")
                    + q.describe() + "; " + cues);
        }
        // Check and set under the lock: a voidCapture between the check and the update
        // would otherwise leave a notice, warning or alarm on a flagged ref.
        synchronized (this) {
            if (r.voided) {   // flagged while the check ran: no notice, no warning
                svc.event("pole check n=" + r.n + ": result not shown (voided)");
                return;
            }
            // a young ref is checked and shown, but never a reference for later checks (PoleCheck).
            // One notice lists every reason; the more severe colour wins (red over amber over green).
            boolean young = r.fixYoung == 1, fixAmber = r.fixSuspect || young;
            String sus = (r.fixSuspect ? suspectText(r) : "") + youngText(r);
            if (r.fixSuspect) cue(Feedback.Cue.SUSPECT);
            else if (young && PointRow.TILT.equals(r.method)) cue(Feedback.Cue.YOUNG);   // occupation: at ref end
            String text = c != null ? c.describe() : Double.isNaN(r.hGround) ? "Ref R" + r.n
                    + " saved (no ground height: no pole check)" : "Ref R" + r.n + " saved: first visit to this mark";
            text += q.describe() + sus;
            if (colour == PoleCheck.Colour.RED) {
                // the cues (off-centre, pole moved) may explain the failure, so they stay with it
                poleWarning = c.describe() + q.describe() + " (R" + r.n + "; check the pole height)";
                poleWarningN = r.n;
                notice = new Notice(text, false, false, r, true);
                cue(Feedback.Cue.POLE_ALARM);
            } else {
                // amber (doubtful reference, off-centre, pole moved) is a re-shoot hint: the
                // error buzz, no alarm and no warnings bar
                poleWarning = null;
                poleWarningN = 0;
                boolean amber = fixAmber || colour == PoleCheck.Colour.AMBER;
                notice = new Notice(text, !amber, amber, r, true);
                // one cue: a suspect or young ref already has its fix cue, which says more
                if (colour == PoleCheck.Colour.AMBER && !fixAmber) cue(Feedback.Cue.ERROR);
                else if (!amber && c != null) cue(Feedback.Cue.POLE_OK);
            }
        }
    }

    /** "2.5" (cm, one decimal) or "" for NaN, for the pole check event line. */
    private static String cmOrEmpty(double m) { return Double.isNaN(m) ? "" : String.format(Locale.US, "%.1f", m * 100); }

    // ---- flag (void) ----

    /** The row numbered n, or null (called with the lock held). */
    private PointRow row(int n) {
        for (PointRow r : rows) if (r.n == n) return r;
        return null;
    }

    /**
     * Flags capture n as bad. via is {@link VoidRule#VIA_UNDO} (refused once the undo window
     * since the capture has passed) or {@link VoidRule#VIA_LIST}. Refused for a photo row (a
     * photo row is not flagged), an already voided row, or while a ref occupation or a tilt
     * set runs (their cancel is the undo). Its photos move (see {@link VoidRule}).
     */
    synchronized Outcome voidCapture(int n, String reason, String via) {
        PointRow r = row(n);
        if (r == null) return new Outcome(false, "No capture " + n);
        if (!r.isCapture()) return new Outcome(false, "A photo row is not flagged");
        if (r.voided) return new Outcome(false, label(r.cls) + n + " is already flagged");
        if (ref != null || tilt != null) return new Outcome(false, "Finish or cancel the running capture first");
        if (VoidRule.VIA_UNDO.equals(via) && !VoidRule.undoOpen(r.elapsedNs, SystemClock.elapsedRealtimeNanos())) {
            return new Outcome(false, "Too late to undo: flag it from the recent captures");
        }
        String why = VoidRule.reason(reason);
        r.voidReason = why;
        r.voided = true;
        svc.event("flag n=" + n + " reason=" + why.replace(' ', '_') + " via=" + via);
        if (!r.photo.isEmpty()) movePhotos(r);
        recount();
        voidGeneration++;
        Notice cur = notice;
        if (cur != null && cur.at == r) notice = null;   // its capture notice no longer stands
        if (poleWarningN == n) { poleWarning = null; poleWarningN = 0; }
        if (autoLatchN == n) {   // the latched capture is undone: this stop is no longer taken
            auto.reset();
            autoLatchN = 0;
            autoLastLabel = "";
        }
        if (PointRow.REF.equals(r.cls)) refsVoid(r);
        savePoints();
        return new Outcome(true, label(r.cls) + n + " flagged: " + why);
    }

    /** Clears the flag of capture n. Its photos stay where they were moved. */
    synchronized Outcome unvoidCapture(int n) {
        PointRow r = row(n);
        if (r == null || !r.voided) return new Outcome(false, "Capture " + n + " is not flagged");
        r.voided = false;
        r.voidReason = "";
        svc.event("unflag n=" + n);
        recount();
        voidGeneration++;
        if (PointRow.REF.equals(r.cls)) refsVoid(r);
        savePoints();
        return new Outcome(true, label(r.cls) + n + " no longer flagged");
    }

    /** Moves the photos of voided row v to the nearest earlier live capture, else to a new photo row. */
    private void movePhotos(PointRow v) {
        PointRow to = VoidRule.photoTarget(rows, v.n);
        String files = v.photo;
        if (to != null) {
            for (String f : files.split(";")) if (!f.isEmpty()) to.photo = PhotoRule.add(to.photo, f);
            v.photo = "";
            svc.event("photo moved n=" + v.n + " to=" + to.n + " photos=" + files);
        } else {
            PointRow own = VoidRule.photoRowFor(v, ++counter);
            v.photo = "";
            rows.add(own);
            svc.event("photo moved n=" + v.n + " to=" + own.n + " own row photos=" + files);
            svc.event("capture " + own.toCsv());
        }
    }

    /** lastCapture and captures from the live rows (called with the lock held). */
    private void recount() {
        lastCapture = VoidRule.lastLive(rows);
        captures = VoidRule.liveCount(rows);
    }

    /** Writes a ref's void state to its refs.csv rows, after its append (same io thread). */
    private void refsVoid(PointRow r) {
        boolean v = r.voided;
        String why = r.voidReason;
        String session = dir.getName();
        run(() -> {
            // a partial write (some copies not rewritten) is an error event too; readers take
            // the flag from the newest copy (VoidRule.liveRefs), and a rewritten copy is newer
            svc.event(RefsStore.setVoid(svc, session, r.n, v, why).event(r.n, v));
            refsGeneration++;
        });
    }

    // ---- PHOTO ----

    /** Chooses the file and the capture for a photo about to be taken. Null if the folder cannot be made. */
    synchronized PhotoPlan planPhoto() {
        long now = SystemClock.elapsedRealtimeNanos();
        int attach = PhotoRule.attachTo(lastCapture, now);
        Epoch e = epochs.peekLast();
        String utc = PointRow.utcCompact(e == null ? Double.NaN : e.tod, System.currentTimeMillis());
        if (!photos.isDirectory() && !photos.mkdirs()) return null;
        File f = new File(photos, PhotoRule.fileName(attach > 0 ? attach : counter + 1, utc));
        for (int i = 2; f.exists(); i++) f = new File(photos, f.getName().replace(".jpg", "") + "_" + i + ".jpg");
        return new PhotoPlan(f, attach, now);
    }

    synchronized void photoTaken(PhotoPlan p) {
        String name = p.file.getName();
        // the planned capture may have been flagged while the camera was open: ask again
        int attachN = p.attachN;
        if (attachN > 0 && (row(attachN) == null || row(attachN).voided)) attachN = PhotoRule.attachTo(lastCapture, p.pressNs);
        PointRow target = attachN > 0 ? row(attachN) : null;
        if (target != null) {
            target.photo = PhotoRule.add(target.photo, name);
            svc.event("photo file=photos/" + name + " bytes=" + p.file.length() + " attached to n=" + target.n);
            savePoints();
            notice = new Notice("Photo on " + label(target.cls) + target.n, true, false, target, false);
        } else {
            EpochAverage a = average(SystemClock.elapsedRealtimeNanos());
            PointRow r = PointRow.of(++counter, PointRow.PHOTO, p.pressNs, a, svc.poleHeight(), System.currentTimeMillis());
            r.photo = name;
            rows.add(r);
            svc.event("photo file=photos/" + name + " bytes=" + p.file.length() + " own row n=" + r.n);
            svc.event("capture " + r.toCsv());
            savePoints();
            notice = new Notice("Photo saved" + (a == null ? " (no fix)" : ""), a != null, false, r, false);
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
    /** The k newest capture rows, flagged ones included, newest first (see VoidRule.recent). */
    synchronized List<PointRow> recent(int k) { return VoidRule.recent(rows, k); }
    synchronized int flagged() { return VoidRule.flaggedCount(rows); }
    /** Where PHOTO would put a photo now: "S12 (14 s)" or "own row" (PhotoRule.targetText). */
    synchronized String photoTarget() { return PhotoRule.targetText(lastCapture, SystemClock.elapsedRealtimeNanos()); }
    synchronized RefView refView() {
        long now = SystemClock.elapsedRealtimeNanos();
        // the refYoung latch keeps "(fix young)" on the ring once the saved ref will be young
        return ref == null ? null : new RefView(ref, refSeconds, now, refYoung || guard.young(now));
    }
    synchronized boolean refRunning() { return ref != null; }
    Notice notice() { Notice n = notice; return n != null && SystemClock.uptimeMillis() < n.untilMs ? n : null; }
    String poleWarning() { return poleWarning; }
    int refsGeneration() { return refsGeneration; }
    int voidGeneration() { return voidGeneration; }

    static String label(String cls) { return PointRow.label(cls); }

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
            if (calibration != null) cancelCalibration("cancelled (session stop)");
        }
        io.shutdown();
        try { io.awaitTermination(5, TimeUnit.SECONDS); } catch (InterruptedException ignored) { }
    }
}
