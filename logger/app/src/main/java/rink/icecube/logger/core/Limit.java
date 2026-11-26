package rink.icecube.logger.core;

import java.util.Locale;

/**
 * A numeric setting typed as text: its range, default and unit, with the parse check the
 * Settings screen shows. The instances are the 0.6 settings for the false-fix guard and
 * tilt capture (see the README, Settings > Survey), and the 0.7 coverage overlay settings
 * (Settings > Coverage).
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
    public static final Limit COV_TARGET_CM = new Limit("Coverage: sigma target", "cm", 0.5, 50, 3.0, 1);
    public static final Limit COV_CELL_M = new Limit("Coverage: grid spacing", "m", 0.1, 2, 0.5, 2);
    public static final Limit COV_RADIUS_M = new Limit("Coverage: capture radius", "m", 0.1, 5, 0.75, 2);
    public static final Limit COV_REACH_M = new Limit("Coverage: reach", "m", 0.1, 20, 2.0, 2);
    public static final Limit COV_GROUND_CM = new Limit("Coverage: ground sigma", "cm", 0, 10, 1.5, 1);
    public static final Limit COV_WANDER_CM = new Limit("Coverage: pass wander", "cm", 0, 10, 1.5, 1);
    /** 0.8.1 (#30): how long Log on GPS connect waits for the rover before it gives up, with no session. */
    public static final Limit GPS_WAIT_MIN = new Limit("Wait for the rover", "min", 0.5, 60, 2, 1);

    /**
     * Capture radius and reach: at most this many grid spacings, so one capture visits at
     * most about (2 * 50 + 1)^2 = 10k grid cells and the node cap takes dozens of captures to reach.
     */
    public static final int COV_MAX_CELLS = 50;
    /** Capture radius: at least this many grid spacings (half the cell diagonal), so every capture feeds a node. */
    public static final double COV_MIN_RADIUS_CELLS = Math.sqrt(0.5);

    /**
     * The problem with this grid spacing, capture radius and reach together (metres), or "".
     * Each value is assumed in its own range already (COV_CELL_M, COV_RADIUS_M, COV_REACH_M).
     */
    public static String coverageProblem(double cell, double radius, double reach) {
        double tol = 1e-9;
        if (radius > COV_MAX_CELLS * cell + tol) {
            return String.format(Locale.US, "capture radius %s m is over %d grid spacings (%s m at %s m spacing)",
                    COV_RADIUS_M.format(radius), COV_MAX_CELLS, COV_RADIUS_M.format(COV_MAX_CELLS * cell),
                    COV_CELL_M.format(cell));
        }
        if (reach > COV_MAX_CELLS * cell + tol) {
            return String.format(Locale.US, "reach %s m is over %d grid spacings (%s m at %s m spacing)",
                    COV_REACH_M.format(reach), COV_MAX_CELLS, COV_REACH_M.format(COV_MAX_CELLS * cell),
                    COV_CELL_M.format(cell));
        }
        if (radius < COV_MIN_RADIUS_CELLS * cell - tol) {
            return String.format(Locale.US, "capture radius %s m is under 0.71 grid spacings (%s m at %s m spacing):"
                    + " some captures would feed no node", COV_RADIUS_M.format(radius),
                    COV_RADIUS_M.format(Math.ceil(COV_MIN_RADIUS_CELLS * cell * 100) / 100), COV_CELL_M.format(cell));
        }
        return "";
    }

    /**
     * {@link #coverageProblem} for the grid spacing, capture radius and reach as typed on the
     * Settings screen, each text that does not parse (or is null) replaced by `cell`, `radius`
     * or `reach`, the value that stays stored for it. A field that parses is thus never saved
     * into a combination that breaks the bounds, whatever state the other two fields are in.
     */
    public static String coverageProblem(String cellText, String radiusText, String reachText,
            double cell, double radius, double reach) {
        return coverageProblem(parseOr(COV_CELL_M, cellText, cell), parseOr(COV_RADIUS_M, radiusText, radius),
                parseOr(COV_REACH_M, reachText, reach));
    }

    private static double parseOr(Limit l, String text, double stored) {
        try { return l.parse(text); } catch (IllegalArgumentException e) { return stored; }
    }

    /**
     * The coverage values brought within {@link #coverageProblem}'s bounds, for values saved
     * before the bounds existed: radius and reach cut to COV_MAX_CELLS spacings, the radius
     * raised to COV_MIN_RADIUS_CELLS. Values already within them come back unchanged.
     */
    public static Coverage.Params boundCoverage(Coverage.Params p) {
        double max = COV_MAX_CELLS * p.cell;
        double radius = Math.max(Math.min(p.captureRadius, max), COV_MIN_RADIUS_CELLS * p.cell);
        double reach = Math.min(p.reach, max);
        if (radius == p.captureRadius && reach == p.reach) return p;
        return new Coverage.Params(p.cell, radius, reach, p.target, p.wander, p.ground);
    }

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
