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
            + "n_epochs,sd_e_m,sd_n_m,sd_u_m,age_s,photo,note";
    public static final String REFS_HEADER = "session," + HEADER;
    public static final int COLUMNS = 17;

    public int n;
    public String cls = "";
    public long elapsedNs;
    public String utc = "";
    public double lat = Double.NaN, lon = Double.NaN, hAnt = Double.NaN, pole = Double.NaN, hGround = Double.NaN;
    public int fix, nEpochs;
    public double sdE = Double.NaN, sdN = Double.NaN, sdU = Double.NaN, age = Double.NaN;
    public String photo = "", note = "";
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
                + num(sdN, 4) + "," + num(sdU, 4) + "," + num(age, 1) + "," + text(photo) + "," + text(note);
    }

    public String toRefsCsv() { return text(session) + "," + toCsv(); }

    /** Parses the columns of HEADER starting at offset (1 for refs.csv). Throws IllegalArgumentException. */
    public static PointRow parse(List<String> f, int offset) {
        if (f.size() < offset + COLUMNS) throw new IllegalArgumentException("expected " + (offset + COLUMNS) + " columns");
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
            r.note = f.get(i);
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
