package rink.icecube.logger.core;

/**
 * What MARK captures: a surface point, an object point or a reference (an occupation that
 * is averaged for a while and checked against earlier visits). MODE cycles
 * Surface, Object, Ref, Surface. The key is the class written to points.csv; beeps is
 * how many beeps announce the mode when it is selected.
 */
public enum SurveyMode {
    SURFACE("surface", "SURFACE", 0xFF0097A7, 1),   // cyan
    OBJECT("object", "OBJECT", 0xFFC2185B, 2),      // magenta
    REF("ref", "REF", 0xFF37474F, 3);               // dark slate

    public final String key, label;
    public final int color, beeps;

    SurveyMode(String key, String label, int color, int beeps) {
        this.key = key; this.label = label; this.color = color; this.beeps = beeps;
    }

    /** Surface, Object, Ref, then back to Surface. */
    public SurveyMode next() { return values()[(ordinal() + 1) % values().length]; }

    /** The mode for a stored key; unknown or missing means Surface. */
    public static SurveyMode fromKey(String key) {
        for (SurveyMode m : values()) if (m.key.equals(key)) return m;
        return SURFACE;
    }
}
