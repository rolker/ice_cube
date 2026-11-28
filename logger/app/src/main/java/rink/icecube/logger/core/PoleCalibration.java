package rink.icecube.logger.core;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;

/**
 * The pole calibration (0.13.0, #35): finds the pole's axis in phone axes ({@link PoleAxis}),
 * which auto capture needs to tell plumb, because the phone is not mounted square to the pole.
 * Once per session, the pole tip on one point throughout, in three timed phases:
 *
 * 1. STILL ({@link #STILL_S} s): hold the pole still. The samples' mean antenna position and
 *    mean gravity are one sample. Moving more than the lean scatter gate from the mean, an
 *    epoch that is not RTK fixed or lacks a height or gravity, or a gap of more than
 *    {@link #GAP_S} s between epochs starts the phase again ({@link #restartCause} says
 *    which); no still spell within {@link #STILL_WAIT_S} s fails, naming a missing height
 *    or gravity when that was the cause. (Extension point for #38: a gyro bias from this phase.)
 * 2. LEAN ({@link #LEAN_S} s): lean slowly all round the tip, as in a tilt set. Each RTK fixed
 *    epoch with a height and gravity is a sample (antenna position, gravity mean about it);
 *    an epoch moving faster than {@link #MAX_SPEED_MPS} is a swing and is skipped.
 * 3. TWIST ({@link #TWIST_S} s): two slow twists about the pole. The axis does not use these
 *    epochs (extension point for #38: the phone's off-axis lever arm); the gyro increments
 *    fed with them must show the pole turned at least {@link #MIN_TWIST_DEG} about the fitted
 *    axis (the range of the running angle, so twisting back and forth counts), or it fails.
 *
 * No epoch for {@link #NO_EPOCH_S} s (the rover gone) fails it through {@link #onTick}, so
 * a calibration never waits for ever with MARK refused.
 *
 * Then the still sample and the lean samples are solved as a tilt set: the tip from
 * {@link TiltSolver#solve} (each sample's tilt from it), the axis from
 * {@link TiltSolver#fitAxis}. Accepted when there are at least {@link #MIN_LEANS} leans (the
 * still sample not counted), the solve's RMS is within the tilt RMS gate, the coverage of
 * the samples leaning at least the minimum tilt is within the tilt coverage gate, the fit's
 * RMS residual is at most {@link #MAX_RESIDUAL_DEG}, and the twist reached
 * {@link #MIN_TWIST_DEG}; otherwise it fails with the reason. Every gate refuses a NaN.
 *
 * Fixed epochs are required (as for tilt leans); a young or suspect fix is fine, because only
 * the relative geometry of one short spell is used. Fed in time order; not thread-safe.
 */
public final class PoleCalibration {
    public static final double STILL_S = 3, STILL_WAIT_S = 30, LEAN_S = 10, TWIST_S = 8;
    public static final double MAX_SPEED_MPS = 0.5, MAX_RESIDUAL_DEG = 1.0;
    /** A gap between still epochs over this (s) starts the still phase again (as for auto capture). */
    public static final double GAP_S = AutoCapture.GAP_S;
    /** No epoch for this long (s) fails the calibration. */
    public static final double NO_EPOCH_S = 5;
    /** The least rotation (deg) about the pole axis the twist phase must show. */
    public static final double MIN_TWIST_DEG = 90;
    /** Leans needed (the still sample is not one). */
    public static final int MIN_LEANS = 4;

    static final String MOVED = "moved", NOT_FIXED = "lost RTK fixed", NO_HEIGHT = "no height in the GGA",
            NO_GRAVITY = "no gravity reading", GAP = "GPS epochs stopped";

    public enum Phase { STILL, LEAN, TWIST, DONE, FAILED }

    /** What an epoch changed. */
    public enum Change { NONE, RESTART, LEAN, TWIST, DONE, FAILED }

    public final double pole;
    public final TiltSet.Gates gates;
    public final long startNs;
    private Phase phase = Phase.STILL;
    private long phaseNs;          // start of the current phase (STILL: of the current still spell, MIN_VALUE before one)
    private LocalFrame frame;
    private final List<double[]> stillP = new ArrayList<>(), stillG = new ArrayList<>();
    private final List<double[]> p = new ArrayList<>(), g = new ArrayList<>();
    private int twistSamples, skipped, skippedNoHeight, skippedNoGravity;
    private double[] prevP;
    private long prevNs;
    private long lastEpochNs = Long.MIN_VALUE, lastStillNs = Long.MIN_VALUE;
    private String restartCause = "", stillBadCause = "";
    private final List<double[]> twistGyro = new ArrayList<>();
    private final List<Double> leanSpeeds = new ArrayList<>();
    private double twist = Double.NaN;
    private PoleAxis result;
    private String failure = "";
    private TiltSolver.Solution solution;
    private double coverage = Double.NaN, residual = Double.NaN;

    public PoleCalibration(double pole, TiltSet.Gates gates, long startNs) {
        this.pole = pole; this.gates = gates; this.startNs = startNs;
        phaseNs = Long.MIN_VALUE;
    }

    public Phase phase() { return phase; }
    public PoleAxis result() { return result; }
    /** Why it failed ("" unless FAILED). */
    public String failure() { return failure; }
    /** Samples so far (the still sample counts once it is taken). */
    public int samples() { return p.size(); }
    /** Leans so far (samples without the still one). */
    public int leans() { return Math.max(0, p.size() - 1); }
    /** Why the still phase last started again: moved, lost RTK fixed, no height, no gravity, a GPS gap. */
    public String restartCause() { return restartCause; }
    /** After the twist check: the twist reached about the axis (deg, the running angle's range); else NaN. */
    public double twistDeg() { return twist; }
    public int twistSamples() { return twistSamples; }
    /** Epochs skipped in the lean and twist phases (not fixed, no height or gravity, a swing). */
    public int skipped() { return skipped; }
    /**
     * The antenna speed (m/s) of each lean-phase epoch with a fixed predecessor, in order,
     * those over {@link #MAX_SPEED_MPS} (skipped as swings) included: for retuning the gate
     * from field runs. "none" when there are none.
     */
    public String leanSpeedsText() {
        if (leanSpeeds.isEmpty()) return "none";
        StringBuilder b = new StringBuilder();
        for (double v : leanSpeeds) b.append(b.length() == 0 ? "" : ";").append(String.format(Locale.US, "%.2f", v));
        return b.toString();
    }
    /** After DONE or FAILED: the tip solve (null if none), the lean coverage and the fit residual (NaN if not reached). */
    public TiltSolver.Solution solution() { return solution; }
    public double coverageDeg() { return coverage; }

    /** After DONE: the solved tip as {lat, lon, h} (auto capture latches this stop there); else null. */
    public double[] tipGeodetic() {
        if (phase != Phase.DONE || solution == null || frame == null) return null;
        return frame.toGeodetic(solution.tip[0], solution.tip[1], solution.tip[2]);
    }
    public double residualDeg() { return residual; }

    /** Seconds left in the current phase at nowNs (the full still time before a still spell begins); 0 when over. */
    public double remainingS(long nowNs) {
        switch (phase) {
            case STILL: return phaseNs == Long.MIN_VALUE ? STILL_S : Math.max(0, STILL_S - (nowNs - phaseNs) / 1e9);
            case LEAN: return Math.max(0, LEAN_S - (nowNs - phaseNs) / 1e9);
            case TWIST: return Math.max(0, TWIST_S - (nowNs - phaseNs) / 1e9);
            default: return 0;
        }
    }

    /** {@link #onEpoch(Epoch, double[], double[])} without a gyro reading. */
    public Change onEpoch(Epoch e, double[] gravity) { return onEpoch(e, gravity, null); }

    /**
     * Feeds one epoch, the phone's gravity about it (phone axes, null if none) and the gyro
     * rotation since the previous epoch (the integral of the angular rate, rad, phone axes;
     * null if none: only the twist phase uses it). Returns what changed.
     */
    public Change onEpoch(Epoch e, double[] gravity, double[] gyroDelta) {
        if (phase == Phase.DONE || phase == Phase.FAILED || e == null) return Change.NONE;
        long t = e.elapsedNs;
        lastEpochNs = t;
        boolean good = e.fixed() && !Double.isNaN(e.h) && gravity != null;
        String bad = good ? null : !e.fixed() ? NOT_FIXED : Double.isNaN(e.h) ? NO_HEIGHT : NO_GRAVITY;
        double[] enu = null;
        if (good) {
            if (frame == null) frame = new LocalFrame(e.lat, e.lon, e.h);
            enu = frame.toEnu(e.lat, e.lon, e.h);
        }
        switch (phase) {
            case STILL: return still(t, enu, gravity, bad);
            case LEAN:
                if ((t - phaseNs) / 1e9 >= LEAN_S - 1e-6) {
                    phase = Phase.TWIST;
                    phaseNs = t;
                    if (enu != null) twistSamples++;
                    return Change.TWIST;
                }
                if (enu == null) { skip(bad); return Change.NONE; }
                double speed = prevP != null && (t - prevNs) > 0
                        ? TiltSolver.norm(sub(enu, prevP)) / ((t - prevNs) / 1e9) : Double.NaN;
                if (!Double.isNaN(speed)) leanSpeeds.add(speed);
                boolean swing = !Double.isNaN(speed) && !(speed <= MAX_SPEED_MPS);
                prevP = enu;
                prevNs = t;
                if (swing) { skipped++; return Change.NONE; }
                p.add(enu);
                g.add(new double[] {gravity[0], gravity[1], gravity[2]});
                return Change.NONE;
            default:   // TWIST
                if (enu != null) twistSamples++; else skip(bad);
                if (gyroDelta != null) twistGyro.add(new double[] {gyroDelta[0], gyroDelta[1], gyroDelta[2]});
                if ((t - phaseNs) / 1e9 >= TWIST_S - 1e-6) return finish(t);
                return Change.NONE;
        }
    }

    /**
     * No epoch since the last one (or the start) for {@link #NO_EPOCH_S} s fails it. Call it
     * now and then (Survey: every service tick) with the epochs' clock; returns FAILED or NONE.
     */
    public Change onTick(long nowNs) {
        if (phase == Phase.DONE || phase == Phase.FAILED) return Change.NONE;
        long since = lastEpochNs == Long.MIN_VALUE ? startNs : lastEpochNs;
        if ((nowNs - since) / 1e9 <= NO_EPOCH_S) return Change.NONE;
        return fail(String.format(Locale.US, "no GPS epochs for %.0f s: is the rover connected?", NO_EPOCH_S));
    }

    private void skip(String bad) {
        skipped++;
        if (NO_HEIGHT.equals(bad)) skippedNoHeight++;
        if (NO_GRAVITY.equals(bad)) skippedNoGravity++;
    }

    private Change still(long t, double[] enu, double[] gravity, String bad) {
        boolean gap = lastStillNs != Long.MIN_VALUE && (t - lastStillNs) / 1e9 > GAP_S + 1e-3;
        lastStillNs = t;
        if (enu == null) {
            stillBadCause = bad;
            boolean had = !stillP.isEmpty();
            restartStill();
            if (timedOut(t)) return failStill();
            if (!had) return Change.NONE;
            restartCause = bad;
            return Change.RESTART;
        }
        stillBadCause = "";   // a good epoch: a later timeout is not the old missing height, gravity or fix
        if (gap && !stillP.isEmpty()) {   // two fixed epochs either side of a stall are not a still spell
            restartStill();
            restartCause = GAP;
            stillP.add(enu);
            stillG.add(new double[] {gravity[0], gravity[1], gravity[2]});
            phaseNs = t;
            return timedOut(t) ? failStill() : Change.RESTART;
        }
        stillP.add(enu);
        stillG.add(new double[] {gravity[0], gravity[1], gravity[2]});
        double[] m = mean(stillP);
        double dev = 0;
        for (double[] x : stillP) dev = Math.max(dev, Math.hypot(x[0] - m[0], x[1] - m[1]));
        if (!(dev <= gates.maxLeanScatter)) {
            restartStill();
            restartCause = MOVED;
            stillP.add(enu);
            stillG.add(new double[] {gravity[0], gravity[1], gravity[2]});
            phaseNs = t;
            return timedOut(t) ? failStill() : Change.RESTART;
        }
        if (phaseNs == Long.MIN_VALUE) phaseNs = t;
        if ((t - phaseNs) / 1e9 >= STILL_S - 1e-6 && stillP.size() >= 2) {
            p.add(m);
            g.add(mean(stillG));
            phase = Phase.LEAN;
            phaseNs = t;
            prevP = enu;
            prevNs = t;
            return Change.LEAN;
        }
        if (timedOut(t)) return failStill();
        return Change.NONE;
    }

    /** The still phase timed out: name a missing height or gravity, which holding still cannot fix. */
    private Change failStill() {
        if (NO_HEIGHT.equals(stillBadCause)) return fail("the GGA has no height: check the receiver's output");
        if (NO_GRAVITY.equals(stillBadCause)) {
            return fail("no gravity reading from the phone (no gravity sensor?): auto capture needs one");
        }
        if (NOT_FIXED.equals(stillBadCause)) return fail("no RTK fixed: calibrate with a fixed rover");
        return fail("could not hold the pole still for " + (int) STILL_S + " s");
    }

    private boolean timedOut(long t) { return (t - startNs) / 1e9 > STILL_WAIT_S; }

    private void restartStill() {
        stillP.clear();
        stillG.clear();
        phaseNs = Long.MIN_VALUE;
    }

    private Change fail(String why) {
        phase = Phase.FAILED;
        failure = why;
        return Change.FAILED;
    }

    private Change finish(long t) {
        if (leans() < MIN_LEANS) {
            String why = skippedNoGravity > 0 && skippedNoGravity >= skippedNoHeight ? " (no gravity reading)"
                    : skippedNoHeight > 0 ? " (no height in the GGA)" : "";
            return fail(String.format(Locale.US, "%d RTK fixed leans%s, need %d: lean slowly", leans(), why,
                    MIN_LEANS));
        }
        solution = TiltSolver.solve(p, pole);
        if (solution == null) return fail("no tip solution: keep the tip on the point and lean all round");
        if (!(solution.rms <= gates.maxRms)) {
            return fail(String.format(Locale.US, "tip solve RMS %.1f cm > %.1f cm: tip slipped or pole height wrong?",
                    solution.rms * 100, gates.maxRms * 100));
        }
        List<Double> az = new ArrayList<>();
        for (int i = 0; i < p.size(); i++) if (solution.tiltDeg[i] >= gates.minTiltDeg) az.add(solution.azimuthDeg[i]);
        double[] a = new double[az.size()];
        for (int i = 0; i < a.length; i++) a[i] = az.get(i);
        coverage = TiltSolver.coverage(a);
        if (!(coverage >= gates.minCoverDeg)) {
            return fail(String.format(Locale.US, "leans cover %.0f° < %.0f° (leans of %.0f° or more): lean all round",
                    coverage, gates.minCoverDeg, gates.minTiltDeg));
        }
        double[] axis = TiltSolver.fitAxis(g, solution.tiltDeg);
        if (axis == null) return fail("no pole axis fit");
        double ss = 0;
        for (int i = 0; i < g.size(); i++) ss += Math.pow(TiltSolver.angleDeg(g.get(i), axis) - solution.tiltDeg[i], 2);
        residual = Math.sqrt(ss / g.size());
        if (!(residual <= MAX_RESIDUAL_DEG)) {
            return fail(String.format(Locale.US, "phone and pole disagree by %.1f° RMS (> %.1f°): is the phone loose?",
                    residual, MAX_RESIDUAL_DEG));
        }
        if (twistGyro.isEmpty()) return fail("no gyro reading in the twist: cannot check it (no gyroscope?)");
        twist = twistRangeDeg(axis);
        if (!(twist >= MIN_TWIST_DEG)) {
            return fail(String.format(Locale.US, "twisted %.0f° about the pole, need %.0f°: twist it slowly, twice",
                    twist, MIN_TWIST_DEG));
        }
        result = new PoleAxis(axis, residual, p.size(), coverage, solution.rms, twistSamples, t,
                new LinkedHashMap<String, Double>());
        phase = Phase.DONE;
        return Change.DONE;
    }

    /** The range (deg) of the running rotation about the axis over the twist phase's gyro increments. */
    private double twistRangeDeg(double[] axis) {
        double n = TiltSolver.norm(axis), a = 0, lo = 0, hi = 0;
        for (double[] d : twistGyro) {
            a += (d[0] * axis[0] + d[1] * axis[1] + d[2] * axis[2]) / n;
            lo = Math.min(lo, a);
            hi = Math.max(hi, a);
        }
        return Math.toDegrees(hi - lo);
    }

    private static double[] sub(double[] a, double[] b) { return new double[] {a[0] - b[0], a[1] - b[1], a[2] - b[2]}; }

    private static double[] mean(List<double[]> xs) {
        double[] m = new double[3];
        for (double[] x : xs) for (int k = 0; k < 3; k++) m[k] += x[k] / xs.size();
        return m;
    }
}
