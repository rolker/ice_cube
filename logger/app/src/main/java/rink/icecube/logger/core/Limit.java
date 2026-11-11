package rink.icecube.logger.core;

import java.util.Locale;

/**
 * A numeric setting typed as text: its range, default and unit, with the parse check the
 * Settings screen shows. The instances are the 0.6 settings for the false-fix guard and
 * tilt capture (see the README, Settings > Survey).
 */
public final class Limit {
    public static final Limit SETTLE_S = new Limit("Fix settle time", "s", 0, 60, 10, 0);
    public static final Limit JUMP_M = new Limit("Fix jump warning", "m", 0.1, 10, 1.0, 2);
    public static final Limit TILT_COVER_DEG = new Limit("Tilt: minimum coverage", "°", 0, 360, 180, 0);
    public static final Limit TILT_RMS_CM = new Limit("Tilt: maximum RMS", "cm", 0.1, 20, 2, 1);
    public static final Limit TILT_MIN_DEG = new Limit("Tilt: minimum lean", "°", 0, 30, 5, 1);
    public static final Limit TILT_MAX_DEG = new Limit("Tilt: maximum lean", "°", 5, 60, 35, 1);
    public static final Limit TILT_SIGMA_CM = new Limit("Tilt: maximum sigma", "cm", 0.1, 50, 5, 1);
    public static final Limit TILT_IMU_DEG = new Limit("Tilt: IMU disagreement", "°", 0.5, 20, 3, 1);
    public static final Limit LEAN_SCATTER_CM = new Limit("Tilt: lean scatter", "cm", 0.2, 20, 2, 1);

    public final String name, unit;
    public final double min, max, def;
    public final int decimals;

    public Limit(String name, String unit, double min, double max, double def, int decimals) {
        this.name = name; this.unit = unit; this.min = min; this.max = max; this.def = def; this.decimals = decimals;
    }

    public String format(double v) { return String.format(Locale.US, "%." + decimals + "f", v); }

    /** "0 to 60 s" */
    public String range() { return format(min) + " to " + format(max) + " " + unit; }

    /** A number in range with at most `decimals` decimals ("," accepted for "."). Throws IllegalArgumentException. */
    public double parse(String text) {
        String s = text == null ? "" : text.trim().replace(',', '.');
        String pattern = decimals == 0 ? "[0-9]{1,4}" : "[0-9]{1,4}(\\.[0-9]{1," + decimals + "})?";
        if (!s.matches(pattern)) {
            throw new IllegalArgumentException(decimals == 0 ? "enter a whole number" : "enter a number, up to "
                    + decimals + " decimal" + (decimals > 1 ? "s" : ""));
        }
        double v = Double.parseDouble(s);
        if (v < min - 1e-9 || v > max + 1e-9) throw new IllegalArgumentException("must be " + range());
        return v;
    }

    public double orDefault(String text) {
        try { return parse(text); } catch (IllegalArgumentException e) { return def; }
    }
}
