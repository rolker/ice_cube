package rink.icecube.logger.core;

/**
 * The per-sounding error budget of one capture: a port of {@code sigma_terms} in
 * {@code analysis/qgis/soundings.py} (with {@code soundings.DEFAULTS["sigma"]} and
 * {@code settle_s}), used by the live coverage overlay ({@link Coverage}). All in metres.
 *
 * <pre>
 * rtk      = max(FLOOR, sd_u)
 * age      = AGE_RATE * max(0, age_s - AGE_FREE_S)
 * settle   = SETTLE_EXTRA * (1 - fix_age_s / SETTLE_S)^2   when fix_age_s &lt; SETTLE_S
 * pole     = pole_m * (1 - cos(tilt)) + TIP_SINK
 * ground   = the Settings > Coverage ground sigma
 * sigma_z  = sqrt(rtk^2 + age^2 + settle^2 + pole^2 + ground^2)
 * sigma_xy = hypot(max(FLOOR_H, sd_h), pole_m * sin(tilt))
 * </pre>
 *
 * Differences from soundings.py: the GST height and horizontal sigmas are not inputs (the
 * logger does not parse GST), so a capture whose GST exceeds its 1 s scatter gets a smaller
 * sigma here. A missing value (NaN) is treated as soundings.py treats None: sd 0 (the floor
 * holds), age 0, no settle term without a fix age, pole 0.
 *
 * The rule picking the inputs mirrors {@code capture_sigma_inputs} in soundings.py. Single
 * captures take the bubble tilt (TILT_BUBBLE_DEG). Tilt captures take the tilt solution's own
 * sigmas (tilt_sd_u_m for sd_u, hypot(tilt_sd_e_m, tilt_sd_n_m) / sqrt 2 for sd_h) and tilt
 * 0, keeping tip sink and ground; a tilt capture missing any of the three tilt sds (NaN)
 * falls back to the single rule. In soundings.py GST still floors a tilt capture's sigmas.
 *
 * When soundings.py changes, regenerate the test vectors
 * ({@code python3 -m icecube.logger_vectors}); SoundingSigmaTest checks these constants
 * against the values pinned there.
 */
public final class SoundingSigma {
    public static final double FLOOR = 0.01, FLOOR_H = 0.01, AGE_FREE_S = 2.0, AGE_RATE = 0.001,
            SETTLE_EXTRA = 0.08, TIP_SINK = 0.01, TILT_BUBBLE_DEG = 1.0, SETTLE_S = 90.0;

    /** The terms of one budget, metres. */
    public static final class Terms {
        public final double rtk, age, settle, pole, ground, sigmaZ, sigmaXy;

        Terms(double rtk, double age, double settle, double pole, double ground, double sigmaZ, double sigmaXy) {
            this.rtk = rtk; this.age = age; this.settle = settle; this.pole = pole; this.ground = ground;
            this.sigmaZ = sigmaZ; this.sigmaXy = sigmaXy;
        }
    }

    private SoundingSigma() { }

    /** sigma_terms with on_mark false and settle inflation on; NaN for a missing input. */
    public static Terms terms(double sdU, double sdH, double ageS, double fixAgeS, double poleM, double tiltDeg,
                              double ground) {
        double rtk = Math.max(FLOOR, orZero(sdU));
        double a = AGE_RATE * Math.max(0.0, orZero(ageS) - AGE_FREE_S);
        double s = 0.0;
        if (!Double.isNaN(fixAgeS) && fixAgeS < SETTLE_S) {
            double f = 1.0 - fixAgeS / SETTLE_S;
            s = SETTLE_EXTRA * f * f;
        }
        double tilt = Math.toRadians(tiltDeg), pole = orZero(poleM);
        double p = pole * (1.0 - Math.cos(tilt)) + TIP_SINK;
        double h = Math.max(FLOOR_H, orZero(sdH));
        double z = Math.sqrt(rtk * rtk + a * a + s * s + p * p + ground * ground);
        return new Terms(rtk, a, s, p, ground, z, Math.hypot(h, pole * Math.sin(tilt)));
    }

    /** The budget of a capture row: the single or the tilt rule (see the class comment). */
    public static Terms of(PointRow r, double ground) {
        if (PointRow.TILT.equals(r.method) && !Double.isNaN(r.tiltSdU) && !Double.isNaN(r.tiltSdE)
                && !Double.isNaN(r.tiltSdN)) {
            return terms(r.tiltSdU, hypotH(r.tiltSdE, r.tiltSdN), r.age, r.fixAge, r.pole, 0.0, ground);
        }
        return terms(r.sdU, hypotH(r.sdE, r.sdN), r.age, r.fixAge, r.pole, TILT_BUBBLE_DEG, ground);
    }

    /** hypot(e, n) / sqrt 2, missing values as 0 (soundings.py's sd_h). */
    static double hypotH(double e, double n) { return Math.hypot(orZero(e), orZero(n)) / Math.sqrt(2); }

    private static double orZero(double v) { return Double.isNaN(v) ? 0.0 : v; }
}
