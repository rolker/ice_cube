package rink.icecube.logger.core;

import java.time.Instant;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * One row of points.csv (and, with the session name in front, of refs.csv). See the
 * README, "points.csv", for the meaning of each column. Missing numbers are written as
 * empty fields. Text fields (photo, note, session) are quoted when they hold a comma,
 * quote or line break.
 */
public final class PointRow {
    public static final String SURFACE = "surface", OBJECT = "object", REF = "ref", PHOTO = "photo";
    public static final String HEADER = "n,class,elapsed_ns,utc,lat,lon,h_ant_ellips,pole_m,h_ground_ellips,fix,"
            + "n_epochs,sd_e_m,sd_n_m,sd_u_m,age_s,photo,note,"
            + "fix_age_s,fix_jump_m,fix_suspect,method,tilt_n,tilt_rms_m,tilt_cover_deg,tilt_sd_e_m,tilt_sd_n_m,tilt_sd_u_m";
    public static final String REFS_HEADER = "session," + HEADER;
    /** Columns of HEADER; rows written before 0.6 have only the first BASE_COLUMNS (up to note). */
    public static final int COLUMNS = 27, BASE_COLUMNS = 17;
    /** method: one averaged capture, a tilt capture, or a ref occupation. */
    public static final String SINGLE = "single", TILT = "tilt", OCCUPATION = "ref";

    public int n;
    public String cls = "";
    public long elapsedNs;
    public String utc = "";
    public double lat = Double.NaN, lon = Double.NaN, hAnt = Double.NaN, pole = Double.NaN, hGround = Double.NaN;
    public int fix, nEpochs;
    public double sdE = Double.NaN, sdN = Double.NaN, sdU = Double.NaN, age = Double.NaN;
    public String photo = "", note = "";
    /** 0.6: seconds since the RTK fixed stretch began, its start jump (m), and whether it is suspect. */
    public double fixAge = Double.NaN, fixJump = Double.NaN;
    public boolean fixSuspect;
    /** 0.6: single, tilt or ref (empty for photo rows), and the tilt solution (tiltN 0 when not tilt). */
    public String method = "";
    public int tiltN;
    public double tiltRms = Double.NaN, tiltCover = Double.NaN, tiltSdE = Double.NaN, tiltSdN = Double.NaN,
            tiltSdU = Double.NaN;
    /** refs.csv only: the session folder name. */
    public String session = "";

    /**
     * A row from an average (null: no position; the position fields stay empty and fix is 0).
     * h_ground = h_ant - pole. utc comes from the GGA time of the last averaged epoch, dated
     * with the phone's clock (see {@link #utc}).
     */
    public static PointRow of(int n, String cls, long elapsedNs, EpochAverage a, double pole, long phoneMs) {
        PointRow r = new PointRow();
        r.n = n;
        r.cls = cls;
        r.elapsedNs = elapsedNs;
        r.pole = pole;
        if (a != null) {
            r.utc = utc(a.tod, phoneMs);
            r.lat = a.lat;
            r.lon = a.lon;
            r.hAnt = a.h;
            r.hGround = a.h - pole;
            r.fix = a.quality;
            r.nEpochs = a.n;
            r.sdE = a.sdE;
            r.sdN = a.sdN;
            r.sdU = a.sdU;
            r.age = a.age;
        }
        return r;
    }

    public boolean hasPosition() { return !Double.isNaN(lat) && !Double.isNaN(lon); }

    /** Surface, object and ref rows; not photo rows. */
    public boolean isCapture() { return !PHOTO.equals(cls); }

    public String toCsv() {
        return n + "," + cls + "," + elapsedNs + "," + utc + "," + num(lat, 9) + "," + num(lon, 9) + "," + num(hAnt, 4)
                + "," + num(pole, 3) + "," + num(hGround, 4) + "," + fix + "," + nEpochs + "," + num(sdE, 4) + ","
                + num(sdN, 4) + "," + num(sdU, 4) + "," + num(age, 1) + "," + text(photo) + "," + text(note)
                + "," + num(fixAge, 1) + "," + num(fixJump, 3) + "," + (fixSuspect ? 1 : 0) + "," + text(method) + ","
                + (tiltN > 0 ? String.valueOf(tiltN) : "") + "," + num(tiltRms, 4) + "," + num(tiltCover, 0) + ","
                + num(tiltSdE, 4) + "," + num(tiltSdN, 4) + "," + num(tiltSdU, 4);
    }

    public String toRefsCsv() { return text(session) + "," + toCsv(); }

    /**
     * Parses the columns of HEADER starting at offset (1 for refs.csv). Rows from before 0.6
     * (BASE_COLUMNS, ending at note) are accepted; the 0.6 columns then stay empty.
     * Throws IllegalArgumentException.
     */
    public static PointRow parse(List<String> f, int offset) {
        if (f.size() < offset + BASE_COLUMNS) {
            throw new IllegalArgumentException("expected " + (offset + BASE_COLUMNS) + " columns");
        }
        PointRow r = new PointRow();
        try {
            if (offset > 0) r.session = f.get(0);
            int i = offset;
            r.n = Integer.parseInt(f.get(i++).trim());
            r.cls = f.get(i++).trim();
            r.elapsedNs = Long.parseLong(f.get(i++).trim());
            r.utc = f.get(i++).trim();
            r.lat = dbl(f.get(i++));
            r.lon = dbl(f.get(i++));
            r.hAnt = dbl(f.get(i++));
            r.pole = dbl(f.get(i++));
            r.hGround = dbl(f.get(i++));
            r.fix = Integer.parseInt(f.get(i++).trim());
            r.nEpochs = Integer.parseInt(f.get(i++).trim());
            r.sdE = dbl(f.get(i++));
            r.sdN = dbl(f.get(i++));
            r.sdU = dbl(f.get(i++));
            r.age = dbl(f.get(i++));
            r.photo = f.get(i++);
            r.note = f.get(i++);
            if (f.size() >= offset + COLUMNS) {
                r.fixAge = dbl(f.get(i++));
                r.fixJump = dbl(f.get(i++));
                r.fixSuspect = "1".equals(f.get(i++).trim());
                r.method = f.get(i++).trim();
                String t = f.get(i++).trim();
                r.tiltN = t.isEmpty() ? 0 : Integer.parseInt(t);
                r.tiltRms = dbl(f.get(i++));
                r.tiltCover = dbl(f.get(i++));
                r.tiltSdE = dbl(f.get(i++));
                r.tiltSdN = dbl(f.get(i++));
                r.tiltSdU = dbl(f.get(i));
            }
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("bad number: " + e.getMessage());
        }
        return r;
    }

    private static double dbl(String s) {
        s = s.trim();
        return s.isEmpty() ? Double.NaN : Double.parseDouble(s);
    }

    static String num(double v, int decimals) {
        if (Double.isNaN(v) || Double.isInfinite(v)) return "";
        return String.format(Locale.US, "%." + decimals + "f", v);
    }

    /** A CSV text field, quoted (with doubled quotes) only when needed. */
    public static String text(String s) {
        if (s == null) return "";
        if (s.indexOf(',') < 0 && s.indexOf('"') < 0 && s.indexOf('\n') < 0 && s.indexOf('\r') < 0) return s;
        return '"' + s.replace("\"", "\"\"") + '"';
    }

    /** Splits one CSV line (RFC 4180 quoting, no line breaks inside fields). */
    public static List<String> split(String line) {
        List<String> out = new ArrayList<>();
        StringBuilder b = new StringBuilder();
        boolean quoted = false;
        for (int i = 0; i < line.length(); i++) {
            char c = line.charAt(i);
            if (quoted) {
                if (c == '"' && i + 1 < line.length() && line.charAt(i + 1) == '"') { b.append('"'); i++; }
                else if (c == '"') quoted = false;
                else b.append(c);
            } else if (c == '"') quoted = true;
            else if (c == ',') { out.add(b.toString()); b.setLength(0); }
            else b.append(c);
        }
        out.add(b.toString());
        return out;
    }

    /**
     * UTC date and time "yyyy-MM-ddTHH:mm:ss.ssZ" from a GGA time of day (seconds), dated
     * with the phone's clock: of the previous, same and next UTC day, the one that puts the
     * time nearest phoneMs. Correct while the phone clock is within 12 h. Empty if tod is NaN.
     */
    public static String utc(double tod, long phoneMs) {
        long t = utcMillis(tod, phoneMs);
        if (t == Long.MIN_VALUE) return "";
        long cs = Math.floorMod(t, 60_000L) / 10;   // centiseconds within the minute
        ZonedDateTime z = Instant.ofEpochMilli(t).atZone(ZoneOffset.UTC);
        return String.format(Locale.US, "%04d-%02d-%02dT%02d:%02d:%02d.%02dZ", z.getYear(), z.getMonthValue(),
                z.getDayOfMonth(), z.getHour(), z.getMinute(), cs / 100, cs % 100);
    }

    /** "yyyyMMddTHHmmssZ" for file names, from the same dating rule; phone time if tod is NaN. */
    public static String utcCompact(double tod, long phoneMs) {
        long t = utcMillis(tod, phoneMs);
        if (t == Long.MIN_VALUE) t = phoneMs;
        ZonedDateTime z = Instant.ofEpochMilli(t).atZone(ZoneOffset.UTC);
        return String.format(Locale.US, "%04d%02d%02dT%02d%02d%02dZ", z.getYear(), z.getMonthValue(), z.getDayOfMonth(),
                z.getHour(), z.getMinute(), z.getSecond());
    }

    static long utcMillis(double tod, long phoneMs) {
        if (Double.isNaN(tod) || tod < 0 || tod >= 86401) return Long.MIN_VALUE;
        long day = 86_400_000L;
        long todMs = Math.round(tod * 100) * 10;   // GGA gives at most centiseconds
        long base = Math.floorDiv(phoneMs, day) * day;
        long best = 0, bestDiff = Long.MAX_VALUE;
        for (int k = -1; k <= 1; k++) {
            long t = base + k * day + todMs;
            long d = Math.abs(t - phoneMs);
            if (d < bestDiff) { bestDiff = d; best = t; }
        }
        return best;
    }
}
