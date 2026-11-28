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
 *    mean gravity are one sample. Moving more than the lean scatter gate from the mean, or a
 *    non-fixed epoch, starts the phase again; no still spell within {@link #STILL_WAIT_S} s
 *    fails. (Extension point for #38: a gyro bias from this phase.)
 * 2. LEAN ({@link #LEAN_S} s): lean slowly all round the tip, as in a tilt set. Each RTK fixed
 *    epoch with a height and gravity is a sample (antenna position, gravity mean about it);
 *    an epoch moving faster than {@link #MAX_SPEED_MPS} is a swing and is skipped.
 * 3. TWIST ({@link #TWIST_S} s): two slow twists about the pole. Samples are counted only
 *    (extension point for #38: the phone's off-axis lever arm); the axis does not use them.
 *
 * Then the still sample and the lean samples are solved as a tilt set: the tip from
 * {@link TiltSolver#solve} (each sample's tilt from it), the axis from
 * {@link TiltSolver#fitAxis}. Accepted when there are at least {@link #MIN_SAMPLES} samples,
 * the solve's RMS is within the tilt RMS gate, the coverage of the samples leaning at least
 * the minimum tilt is within the tilt coverage gate, and the fit's RMS residual is at most
 * {@link #MAX_RESIDUAL_DEG}; otherwise it fails with the reason.
 *
 * Fixed epochs are required (as for tilt leans); a young or suspect fix is fine, because only
 * the relative geometry of one short spell is used. Fed in time order; not thread-safe.
 */
public final class PoleCalibration {
    public static final double STILL_S = 3, STILL_WAIT_S = 30, LEAN_S = 10, TWIST_S = 8;
    public static final double MAX_SPEED_MPS = 0.5, MAX_RESIDUAL_DEG = 1.0;
    public static final int MIN_SAMPLES = 4;

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
    private int twistSamples, skipped;
    private double[] prevP;
    private long prevNs;
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
    public int twistSamples() { return twistSamples; }
    /** Epochs skipped in the lean and twist phases (not fixed, no height or gravity, a swing). */
    public int skipped() { return skipped; }
    /** After DONE or FAILED: the tip solve (null if none), the lean coverage and the fit residual (NaN if not reached). */
    public TiltSolver.Solution solution() { return solution; }
    public double coverageDeg() { return coverage; }
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

    /**
     * Feeds one epoch and the phone's gravity about it (phone axes, null if none). Returns
     * what changed.
     */
    public Change onEpoch(Epoch e, double[] gravity) {
        if (phase == Phase.DONE || phase == Phase.FAILED || e == null) return Change.NONE;
        long t = e.elapsedNs;
        boolean good = e.fixed() && !Double.isNaN(e.h) && gravity != null;
        double[] enu = null;
        if (good) {
            if (frame == null) frame = new LocalFrame(e.lat, e.lon, e.h);
            enu = frame.toEnu(e.lat, e.lon, e.h);
        }
        switch (phase) {
            case STILL: return still(t, enu, gravity);
            case LEAN:
                if ((t - phaseNs) / 1e9 >= LEAN_S - 1e-6) {
                    phase = Phase.TWIST;
                    phaseNs = t;
                    if (enu != null) twistSamples++;
                    return Change.TWIST;
                }
                if (enu == null) { skipped++; return Change.NONE; }
                boolean swing = prevP != null && (t - prevNs) > 0
                        && TiltSolver.norm(sub(enu, prevP)) / ((t - prevNs) / 1e9) > MAX_SPEED_MPS;
                prevP = enu;
                prevNs = t;
                if (swing) { skipped++; return Change.NONE; }
                p.add(enu);
                g.add(new double[] {gravity[0], gravity[1], gravity[2]});
                return Change.NONE;
            default:   // TWIST
                if (enu != null) twistSamples++; else skipped++;
                if ((t - phaseNs) / 1e9 >= TWIST_S - 1e-6) return finish(t);
                return Change.NONE;
        }
    }

    private Change still(long t, double[] enu, double[] gravity) {
        if (enu == null) {
            boolean had = !stillP.isEmpty();
            restartStill();
            return timedOut(t) ? fail("could not hold the pole still on RTK fixed for " + (int) STILL_S + " s")
                    : had ? Change.RESTART : Change.NONE;
        }
        stillP.add(enu);
        stillG.add(new double[] {gravity[0], gravity[1], gravity[2]});
        double[] m = mean(stillP);
        double dev = 0;
        for (double[] x : stillP) dev = Math.max(dev, Math.hypot(x[0] - m[0], x[1] - m[1]));
        if (dev > gates.maxLeanScatter) {
            restartStill();
            stillP.add(enu);
            stillG.add(new double[] {gravity[0], gravity[1], gravity[2]});
            phaseNs = t;
            return timedOut(t) ? fail("could not hold the pole still for " + (int) STILL_S + " s") : Change.RESTART;
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
        if (timedOut(t)) return fail("could not hold the pole still for " + (int) STILL_S + " s");
        return Change.NONE;
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
        if (p.size() < MIN_SAMPLES) {
            return fail(String.format(Locale.US, "%d RTK fixed samples, need %d: lean slowly", p.size(), MIN_SAMPLES));
        }
        solution = TiltSolver.solve(p, pole);
        if (solution == null) return fail("no tip solution: keep the tip on the point and lean all round");
        if (solution.rms > gates.maxRms) {
            return fail(String.format(Locale.US, "tip solve RMS %.1f cm > %.1f cm: tip slipped or pole height wrong?",
                    solution.rms * 100, gates.maxRms * 100));
        }
        List<Double> az = new ArrayList<>();
        for (int i = 0; i < p.size(); i++) if (solution.tiltDeg[i] >= gates.minTiltDeg) az.add(solution.azimuthDeg[i]);
        double[] a = new double[az.size()];
        for (int i = 0; i < a.length; i++) a[i] = az.get(i);
        coverage = TiltSolver.coverage(a);
        if (coverage < gates.minCoverDeg) {
            return fail(String.format(Locale.US, "leans cover %.0f° < %.0f° (leans of %.0f° or more): lean all round",
                    coverage, gates.minCoverDeg, gates.minTiltDeg));
        }
        double[] axis = TiltSolver.fitAxis(g, solution.tiltDeg);
        if (axis == null) return fail("no pole axis fit");
        double ss = 0;
        for (int i = 0; i < g.size(); i++) ss += Math.pow(TiltSolver.angleDeg(g.get(i), axis) - solution.tiltDeg[i], 2);
        residual = Math.sqrt(ss / g.size());
        if (residual > MAX_RESIDUAL_DEG) {
            return fail(String.format(Locale.US, "phone and pole disagree by %.1f° RMS (> %.1f°): is the phone loose?",
                    residual, MAX_RESIDUAL_DEG));
        }
        result = new PoleAxis(axis, residual, p.size(), coverage, solution.rms, twistSamples, t,
                new LinkedHashMap<String, Double>());
        phase = Phase.DONE;
        return Change.DONE;
    }

    private static double[] sub(double[] a, double[] b) { return new double[] {a[0] - b[0], a[1] - b[1], a[2] - b[2]}; }

    private static double[] mean(List<double[]> xs) {
        double[] m = new double[3];
        for (double[] x : xs) for (int k = 0; k < 3; k++) m[k] += x[k] / xs.size();
        return m;
    }
}
