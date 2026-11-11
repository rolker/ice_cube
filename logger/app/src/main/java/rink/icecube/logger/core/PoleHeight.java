package rink.icecube.logger.core;

import java.util.Locale;

/**
 * Antenna pole height: the vertical distance from the ground mark to the antenna
 * reference point, in metres. Ground height = antenna height - pole height.
 */
public final class PoleHeight {
    public static final double[] PRESETS = {1.8, 1.7, 1.6, 1.5, 1.4};
    public static final double DEFAULT = 1.8, MIN = 0, MAX = 5;

    private PoleHeight() { }

    /**
     * Parses a typed value in metres ("1.75", "1,75" or " 2 "). At most three decimals,
     * between 0 and 5 m inclusive. Throws IllegalArgumentException with a short reason.
     */
    public static double parse(String text) {
        if (text == null) throw new IllegalArgumentException("enter a height in metres");
        String s = text.trim().replace(',', '.');
        if (s.isEmpty()) throw new IllegalArgumentException("enter a height in metres");
        if (!s.matches("[0-9]*\\.?[0-9]*") || s.equals(".")) throw new IllegalArgumentException("not a number: " + text.trim());
        int dot = s.indexOf('.');
        if (dot >= 0 && s.length() - dot - 1 > 3) throw new IllegalArgumentException("use at most 3 decimals (mm)");
        double v = Double.parseDouble(s);
        if (v < MIN || v > MAX) throw new IllegalArgumentException("must be between 0 and 5 m");
        return Math.round(v * 1000) / 1000.0;
    }

    /** Parses a stored value, falling back to DEFAULT if it is missing or invalid. */
    public static double parseOrDefault(String text) {
        try { return parse(text); } catch (IllegalArgumentException e) { return DEFAULT; }
    }

    /** Metres with three decimals, as written to events.csv and Settings. */
    public static String format(double m) { return String.format(Locale.US, "%.3f", m); }

    /** Index into PRESETS, or -1 if the value is not one of them. */
    public static int presetIndex(double m) {
        for (int i = 0; i < PRESETS.length; i++) if (Math.abs(PRESETS[i] - m) < 5e-4) return i;
        return -1;
    }
}
