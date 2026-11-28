package rink.icecube.logger.core;

import java.util.ArrayList;
import java.util.List;

/**
 * Auto capture (0.13.0, #35): takes a Surface capture when the pole has been held plumb and
 * still on a good fix, so the user never presses MARK with gloves on. Pure logic, fed one
 * {@link Input} per GGA epoch; Survey does the capture itself (the same path as MARK).
 *
 * Gates, in order (the first unmet one is the {@link Why} shown): the setting is on, Surface
 * mode, no pole calibration running, a pole axis calibrated this session, no tilt set, no ref
 * occupation; then the fix: RTK fixed, not young and not suspect (the false-fix guard,
 * {@link FixGuard}). Any unmet gate empties the hold window.
 *
 * Hold: with every gate met, each epoch must be plumb (the angle between the phone's gravity
 * and the calibrated pole axis at most plumbDeg) and still (every horizontal antenna position
 * in the window at most scatterM from the window's mean: a max deviation, not an sd, because
 * at 1 Hz a 2 s window holds only 3 epochs). A tilt or a move starts the window again at the
 * epoch; so does a gap of more than {@link #GAP_S} between epochs. Once the window spans holdS
 * (and has 2 epochs or more) it fires: one capture.
 *
 * One capture per stop: every capture at a stop is reported through {@link #taken} (the
 * caller does this once the capture really happened: an auto capture after its 1 s average,
 * a Surface MARK, a finished Surface tilt set at its tip, an accepted pole calibration at its
 * tip), which latches the stop at that position. A fire does not latch by itself, so a
 * capture the caller had to skip leaves nothing taken. The latch clears only when the
 * antenna has moved more than {@link #REARM_FACTOR} times scatterM horizontally from the
 * latched position (or on {@link #reset}, for an undone capture), read at a plumb epoch: a
 * plumb antenna is over its tip, while a leaning one is not (a 15° lean on a 1.8 m pole puts
 * it 47 cm off the tip), so tilting, losing plumb or a NaN gravity reading never clears it,
 * and a pole leaning about one point (a tilt set, a calibration) never re-shoots it. Then the
 * full hold must pass again.
 *
 * Cues: the armed state (every gate met) is debounced, so it changes only once the new state
 * has held {@link #CUE_S} s; a change is cued only when the fix caused it (NO_FIX, FIX_YOUNG,
 * FIX_SUSPECT on the off side), because the user already knows about a mode change, a tilt
 * set, a ref, a calibration or the setting, and each has its own cue. Plumb and still are not
 * gates and never cue (the capture tone covers them). Not thread-safe (Survey holds its lock).
 */
public final class AutoCapture {
    /** A gap between epochs over this (s) starts the hold again. */
    public static final double GAP_S = 2.0;
    /** Re-arm distance, in multiples of the still limit (6 cm at the 2 cm default). */
    public static final double REARM_FACTOR = 3.0;
    /** The armed state must hold this long (s) before it changes and cues. */
    public static final double CUE_S = 1.0;
    private static final double TOL_S = 1e-3;

    /** Why there is no capture now (or HOLDING / CAPTURED). */
    public enum Why {
        OFF_SETTING, NOT_SURFACE, CALIBRATING, NO_CALIBRATION, TILT_SET, REF_RUNNING,
        NO_FIX, FIX_YOUNG, FIX_SUSPECT,
        /** Armed: waiting for the pole to be plumb. */
        NOT_PLUMB,
        /** Armed: waiting for the pole to be still. */
        MOVING,
        /** Armed: plumb and still, counting the hold. */
        HOLDING,
        /** Armed: captured here (or MARK did); move on to the next point. */
        CAPTURED;

        /** Every gate met (the rest is the pole). */
        public boolean armed() { return ordinal() >= NOT_PLUMB.ordinal(); }

        /** An off state the fix caused. */
        public boolean byFix() { return this == NO_FIX || this == FIX_YOUNG || this == FIX_SUSPECT; }
    }

    /** A cue to play: none, armed (again), off. */
    public enum Cue { NONE, ARMED, OFF }

    /** One epoch and the context it came in. e and n are horizontal antenna metres in any fixed local frame. */
    public static final class Input {
        public long tNs;
        public boolean enabled = true, surface = true, calibrating, calibrated = true, tiltRunning, refRunning;
        public boolean fixed = true, young, suspect;
        public double e = Double.NaN, n = Double.NaN;
        /** Angle (deg) of the pole from plumb; NaN if unknown (no gravity, no axis). */
        public double plumbDeg = Double.NaN;
    }

    /** The answer to an epoch. */
    public static final class Decision {
        public final boolean fire;
        public final Why why;
        /** Seconds held so far (HOLDING, or the hold at a fire), else 0. */
        public final double heldS;
        /** At a fire: the window's largest horizontal deviation from its mean (m), and its epochs. */
        public final double spreadM;
        public final int epochs;
        /** At a fire: the window's mean horizontal position (the place to pass to {@link #taken}); else NaN. */
        public final double e, n;
        public final Cue cue;
        Decision(boolean fire, Why why, double heldS, double spreadM, int epochs, double e, double n, Cue cue) {
            this.fire = fire; this.why = why; this.heldS = heldS; this.spreadM = spreadM; this.epochs = epochs;
            this.e = e; this.n = n; this.cue = cue;
        }
    }

    private double plumbDeg, holdS, scatterM;
    private final List<double[]> window = new ArrayList<>();   // {tS, e, n}
    private long lastNs = Long.MIN_VALUE;
    private boolean latched;
    private double latchE, latchN;
    private boolean armedStable;
    private Why stableWhy;
    private long pendingSinceNs = Long.MIN_VALUE;
    private Why last;

    public AutoCapture(double plumbDeg, double holdS, double scatterM) { setLimits(plumbDeg, holdS, scatterM); }

    /** New settings, applied from the next epoch. */
    public void setLimits(double plumbDeg, double holdS, double scatterM) {
        this.plumbDeg = plumbDeg; this.holdS = holdS; this.scatterM = scatterM;
    }

    public double plumbDeg() { return plumbDeg; }
    public double holdS() { return holdS; }
    public double scatterM() { return scatterM; }

    /** The re-arm distance (m). */
    public double rearmM() { return REARM_FACTOR * scatterM; }

    /** Whether a capture was taken here and the pole has not yet moved away. */
    public boolean latched() { return latched; }

    /** The Why of the last epoch, or null before the first. */
    public Why last() { return last; }

    /**
     * A capture was taken at (e, n): an auto capture (the fire's e, n), a Surface MARK, a
     * Surface tilt set's tip or a calibration's tip. This stop counts as taken, so no auto
     * capture follows until the antenna moves away. A NaN position is ignored.
     */
    public void taken(double e, double n) {
        if (Double.isNaN(e) || Double.isNaN(n)) return;
        latched = true;
        latchE = e;
        latchN = n;
        window.clear();
    }

    /** Forgets the hold window and the latch (a new calibration, or the latched capture undone). */
    public void reset() {
        window.clear();
        latched = false;
    }

    public Decision onEpoch(Input in) {
        boolean gap = lastNs != Long.MIN_VALUE && (in.tNs - lastNs) / 1e9 > GAP_S + TOL_S;
        lastNs = in.tNs;
        if (gap) window.clear();
        Why why = gate(in);
        if (why != null) {
            window.clear();
            return decide(in, why, false, 0, 0, 0);
        }
        boolean plumb = in.plumbDeg <= plumbDeg;   // false for NaN
        if (latched) {
            // only a plumb antenna clearly away clears it: never a tilt, a NaN gravity or a NaN position
            boolean away = plumb && Math.hypot(in.e - latchE, in.n - latchN) > rearmM();
            if (!away) {
                window.clear();
                return decide(in, Why.CAPTURED, false, 0, 0, 0);
            }
            latched = false;
        }
        if (!plumb || Double.isNaN(in.e) || Double.isNaN(in.n)) {
            window.clear();
            return decide(in, Why.NOT_PLUMB, false, 0, 0, 0);
        }
        window.add(new double[] {in.tNs / 1e9, in.e, in.n});
        double spread = spread();
        if (!(spread <= scatterM)) {
            window.clear();
            window.add(new double[] {in.tNs / 1e9, in.e, in.n});
            return decide(in, Why.MOVING, false, 0, 0, 0);
        }
        double held = window.get(window.size() - 1)[0] - window.get(0)[0];
        if (window.size() >= 2 && held >= holdS - TOL_S) {
            double[] m = mean();
            int n = window.size();
            window.clear();
            last = Why.CAPTURED;
            return new Decision(true, Why.CAPTURED, held, spread, n, m[0], m[1], cue(in.tNs, Why.CAPTURED));
        }
        return decide(in, Why.HOLDING, false, held, 0, 0);
    }

    /** The first unmet gate, or null. */
    private static Why gate(Input in) {
        if (!in.enabled) return Why.OFF_SETTING;
        if (!in.surface) return Why.NOT_SURFACE;
        if (in.calibrating) return Why.CALIBRATING;
        if (!in.calibrated) return Why.NO_CALIBRATION;
        if (in.tiltRunning) return Why.TILT_SET;
        if (in.refRunning) return Why.REF_RUNNING;
        if (!in.fixed) return Why.NO_FIX;
        if (in.young) return Why.FIX_YOUNG;
        if (in.suspect) return Why.FIX_SUSPECT;
        return null;
    }

    private Decision decide(Input in, Why why, boolean fire, double held, double spread, int n) {
        last = why;
        return new Decision(fire, why, held, spread, n, Double.NaN, Double.NaN, cue(in.tNs, why));
    }

    /** Debounces the armed state; the cue for a change, if the fix caused it. */
    private Cue cue(long tNs, Why why) {
        boolean armed = why.armed();
        if (stableWhy == null) {   // the first epoch sets the state without a cue
            armedStable = armed;
            stableWhy = why;
            return Cue.NONE;
        }
        if (armed == armedStable) {
            pendingSinceNs = Long.MIN_VALUE;
            if (!armed) stableWhy = why;   // the latest off reason, for the cue when it comes back
            return Cue.NONE;
        }
        if (pendingSinceNs == Long.MIN_VALUE) pendingSinceNs = tNs;
        if ((tNs - pendingSinceNs) / 1e9 < CUE_S - TOL_S) return Cue.NONE;
        Why offWhy = armed ? stableWhy : why;
        armedStable = armed;
        stableWhy = why;
        pendingSinceNs = Long.MIN_VALUE;
        if (!offWhy.byFix()) return Cue.NONE;
        return armed ? Cue.ARMED : Cue.OFF;
    }

    private double[] mean() {
        double e = 0, n = 0;
        for (double[] w : window) { e += w[1]; n += w[2]; }
        return new double[] {e / window.size(), n / window.size()};
    }

    /** The largest horizontal distance of a window epoch from the window's mean (m). */
    private double spread() {
        double[] m = mean();
        double s = 0;
        for (double[] w : window) s = Math.max(s, Math.hypot(w[1] - m[0], w[2] - m[1]));
        return s;
    }
}
