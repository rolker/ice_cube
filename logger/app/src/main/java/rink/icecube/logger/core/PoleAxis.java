package rink.icecube.logger.core;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * The pole's axis in phone axes, from a {@link PoleCalibration} (0.13.0, #35): a unit vector
 * along the pole, up, in the frame of the phone's gravity sensor, so the pole's angle from
 * plumb is {@link #plumbDeg} of a gravity reading. Mounting geometry only: no position.
 *
 * Held per session (Survey), never saved across sessions: the phone may sit differently in
 * its mount the next time.
 */
public final class PoleAxis {
    public final double[] axis;
    /** RMS (deg) of angle(g_i, axis) - tilt_i over the calibration samples. */
    public final double residualDeg;
    /** Samples used (still + lean), the lean coverage (deg) and the tip solve's RMS (m). */
    public final int samples;
    public final double coverageDeg, rmsM;
    /** Samples taken in the twist phase (recorded, not used for the axis). */
    public final int twistSamples;
    public final long timeNs;
    /**
     * Further calibration outputs, by name: empty in 0.13.0. The extension point for #38
     * (the phone's off-axis lever arm from the twists, a gyro bias from the still phase).
     */
    public final Map<String, Double> extras;

    public PoleAxis(double[] axis, double residualDeg, int samples, double coverageDeg, double rmsM, int twistSamples,
            long timeNs, Map<String, Double> extras) {
        this.axis = axis.clone();
        this.residualDeg = residualDeg; this.samples = samples; this.coverageDeg = coverageDeg; this.rmsM = rmsM;
        this.twistSamples = twistSamples; this.timeNs = timeNs;
        this.extras = Collections.unmodifiableMap(new LinkedHashMap<>(extras));
    }

    /**
     * The pole's angle from plumb (deg) for a gravity reading in phone axes (the "up"
     * reaction vector the gravity sensor gives, any length); NaN if g is null or zero.
     */
    public double plumbDeg(double[] g) {
        if (g == null) return Double.NaN;
        return TiltSolver.angleDeg(new double[] {g[0], g[1], g[2]}, axis);
    }
}
