package rink.icecube.logger.core;

/** The Survey settings for captures: ref duration and pole check tolerance, with their checks. */
public final class SurveyLimits {
    public static final int REF_SECONDS_DEFAULT = 30, REF_SECONDS_MIN = 10, REF_SECONDS_MAX = 300;
    public static final double TOLERANCE_CM_DEFAULT = 3, TOLERANCE_CM_MIN = 0.5, TOLERANCE_CM_MAX = 20;

    private SurveyLimits() { }

    /** Whole seconds, 10 to 300. Throws IllegalArgumentException with a short reason. */
    public static int parseRefSeconds(String text) {
        String s = text == null ? "" : text.trim();
        if (!s.matches("[0-9]{1,4}")) throw new IllegalArgumentException("enter whole seconds");
        int v = Integer.parseInt(s);
        if (v < REF_SECONDS_MIN || v > REF_SECONDS_MAX) throw new IllegalArgumentException("must be 10 to 300 s");
        return v;
    }

    /** Centimetres, 0.5 to 20, at most one decimal ("3", "2.5", "2,5"). */
    public static double parseToleranceCm(String text) {
        String s = text == null ? "" : text.trim().replace(',', '.');
        if (!s.matches("[0-9]{1,2}(\\.[0-9])?")) throw new IllegalArgumentException("enter centimetres, e.g. 3 or 2.5");
        double v = Double.parseDouble(s);
        if (v < TOLERANCE_CM_MIN || v > TOLERANCE_CM_MAX) throw new IllegalArgumentException("must be 0.5 to 20 cm");
        return v;
    }

    public static int refSecondsOrDefault(String text) {
        try { return parseRefSeconds(text); } catch (IllegalArgumentException e) { return REF_SECONDS_DEFAULT; }
    }

    public static double toleranceCmOrDefault(String text) {
        try { return parseToleranceCm(text); } catch (IllegalArgumentException e) { return TOLERANCE_CM_DEFAULT; }
    }
}
