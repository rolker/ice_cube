package rink.icecube.logger.core;

import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * A reference frame name and optional epoch (decimal year, NaN if none). RTCM 1005/1006
 * carry ECEF coordinates but no datum label, so the frame must come from somewhere else:
 * the caster's sourcetable (STR misc field) or the user's setting. This class never guesses.
 */
public final class ReferenceFrame {
    /** Choices in Settings; index 0 means unknown. */
    public static final String[] SETTING_NAMES = {"Unknown", "ITRF2020", "ITRF2014", "ITRF2008", "NAD83(2011)", "WGS84"};

    public final String name;
    public final double epoch;

    public ReferenceFrame(String name, double epoch) { this.name = name; this.epoch = epoch; }

    private static final Pattern ITRF = Pattern.compile("\\bITRF\\s*[-_ ]?\\s*(\\d{4}|\\d{2})(?!\\d)", Pattern.CASE_INSENSITIVE);
    private static final Pattern NAD83 = Pattern.compile("\\bNAD\\s*[-_ ]?\\s*83(?:\\s*[(\\[]?\\s*(2011|CSRS|HARN|CORS96|NSRS2007|MA11|PA11)\\s*[)\\]]?)?", Pattern.CASE_INSENSITIVE);
    private static final Pattern WGS84 = Pattern.compile("\\bWGS\\s*[-_ ]?\\s*84(?:\\s*[(\\[]?\\s*(G\\s*\\d{3,4})\\s*[)\\]]?)?", Pattern.CASE_INSENSITIVE);
    private static final Pattern EPOCH_WORD = Pattern.compile("(?:\\bepoch|\\bep|@|\\bt0)\\s*[:=]?\\s*((?:19|20)\\d{2}(?:\\.\\d+)?)(?!\\d)", Pattern.CASE_INSENSITIVE);
    private static final Pattern DECIMAL_YEAR = Pattern.compile("(?<![\\d.])((?:19|20)\\d{2}\\.\\d+)(?![\\d.])");

    /**
     * Finds a frame name and optional epoch in free text such as "ITRF2020 epoch 2026.85",
     * "itrf 2014 @2010.0", "NAD83(2011) epoch 2010.00" or "WGS84 (G2139)". Returns null if no
     * known frame name is present.
     */
    public static ReferenceFrame parse(String text) {
        if (text == null) return null;
        String name = null;
        int nameEnd = -1;
        Matcher m = ITRF.matcher(text);
        if (m.find()) {
            String y = m.group(1);
            if (y.length() == 2) y = (Integer.parseInt(y) >= 80 ? "19" : "20") + y;
            name = "ITRF" + y;
            nameEnd = m.end();
        } else if ((m = NAD83.matcher(text)).find()) {
            name = m.group(1) == null ? "NAD83" : "NAD83(" + m.group(1).toUpperCase(Locale.US) + ")";
            nameEnd = m.end();
        } else if ((m = WGS84.matcher(text)).find()) {
            name = m.group(1) == null ? "WGS84" : "WGS84(" + m.group(1).replace(" ", "").toUpperCase(Locale.US) + ")";
            nameEnd = m.end();
        }
        if (name == null) return null;
        double epoch = Double.NaN;
        Matcher e = EPOCH_WORD.matcher(text);
        if (e.find()) epoch = Double.parseDouble(e.group(1));
        else {
            Matcher d = DECIMAL_YEAR.matcher(text);
            if (d.find(Math.max(0, nameEnd))) epoch = Double.parseDouble(d.group(1));
        }
        return new ReferenceFrame(name, epoch);
    }

    /** Parses an optional typed epoch: empty is NaN; otherwise a decimal year 1980-2100. */
    public static double parseEpoch(String text) {
        String s = text == null ? "" : text.trim().replace(',', '.');
        if (s.isEmpty()) return Double.NaN;
        double v;
        try { v = Double.parseDouble(s); } catch (NumberFormatException x) { throw new IllegalArgumentException("not a number: " + s); }
        if (!(v >= 1980 && v <= 2100)) throw new IllegalArgumentException("epoch must be a decimal year, 1980 to 2100");
        return v;
    }

    /**
     * Same frame family and realization. "WGS84" without a realization matches any
     * WGS84(Gxxxx), and "NAD83" matches any NAD83(...), since the less specific name
     * does not contradict the other. Epochs are compared when both are given (0.01 yr).
     */
    public static boolean compatible(ReferenceFrame a, ReferenceFrame b) {
        String x = a.name.toUpperCase(Locale.US), y = b.name.toUpperCase(Locale.US);
        boolean names = x.equals(y)
                || ((x.equals("WGS84") || y.equals("WGS84")) && x.startsWith("WGS84") && y.startsWith("WGS84"))
                || ((x.equals("NAD83") || y.equals("NAD83")) && x.startsWith("NAD83") && y.startsWith("NAD83"));
        boolean epochs = Double.isNaN(a.epoch) || Double.isNaN(b.epoch) || Math.abs(a.epoch - b.epoch) < 0.01;
        return names && epochs;
    }

    public String epochText() { return Double.isNaN(epoch) ? "none" : String.format(Locale.US, "%.2f", epoch); }

    /** "ITRF2020 epoch 2026.85" or "ITRF2020". */
    public String describe() { return Double.isNaN(epoch) ? name : name + " epoch " + epochText(); }

    @Override public boolean equals(Object o) {
        if (!(o instanceof ReferenceFrame)) return false;
        ReferenceFrame f = (ReferenceFrame) o;
        return name.equals(f.name) && (Double.isNaN(epoch) ? Double.isNaN(f.epoch) : epoch == f.epoch);
    }

    @Override public int hashCode() { return name.hashCode(); }

    /** The frame in use, where it came from, and a warning when the two sources disagree. */
    public static final class Resolved {
        public final ReferenceFrame frame;   // null if unknown
        public final String source;          // "sourcetable", "setting" or "none"
        public final String warning;         // null unless setting and sourcetable disagree

        Resolved(ReferenceFrame frame, String source, String warning) { this.frame = frame; this.source = source; this.warning = warning; }

        /** The events.csv line for this choice. */
        public String eventText() {
            return frame == null ? "frame=unknown source=none"
                    : "frame=" + frame.name + " epoch=" + frame.epochText() + " source=" + source;
        }

        public String describe() { return frame == null ? "frame unknown" : frame.describe() + " (" + source + ")"; }

        public boolean sameAs(Resolved o) {
            return o != null && source.equals(o.source)
                    && (frame == null ? o.frame == null : frame.equals(o.frame))
                    && (warning == null ? o.warning == null : warning.equals(o.warning));
        }
    }

    /** The sourcetable wins when it names a frame; otherwise the setting; otherwise unknown. */
    public static Resolved resolve(ReferenceFrame setting, ReferenceFrame sourcetable) {
        if (sourcetable != null) {
            String w = setting != null && !compatible(setting, sourcetable)
                    ? "frame mismatch: setting " + setting.describe() + ", sourcetable " + sourcetable.describe() : null;
            return new Resolved(sourcetable, "sourcetable", w);
        }
        if (setting != null) return new Resolved(setting, "setting", null);
        return new Resolved(null, "none", null);
    }

    /** The setting from its drop-down name and epoch; null for Unknown. */
    public static ReferenceFrame fromSetting(String name, double epoch) {
        if (name == null || name.isEmpty() || name.equals(SETTING_NAMES[0])) return null;
        return new ReferenceFrame(name, epoch);
    }
}
