package rink.icecube.logger.core;

/**
 * Minimal NMEA GGA fields. alt is field 9 (height above the receiver's geoid model), sep
 * is field 11 (geoid separation), and height = alt + sep is the ellipsoidal height of the
 * antenna, NaN unless both fields are present.
 */
public final class Gga {
    public double tod = Double.NaN, lat = Double.NaN, lon = Double.NaN, age = Double.NaN;
    public double alt = Double.NaN, sep = Double.NaN, height = Double.NaN;
    public int quality, sats;

    public static Gga parse(String s) {
        int star = s.indexOf('*');
        String[] f = (star > 0 ? s.substring(0, star) : s).split(",", -1);
        if (f.length < 14) return null;
        Gga g = new Gga();
        try {
            if (f[1].length() >= 6) {
                g.tod = Integer.parseInt(f[1].substring(0, 2)) * 3600
                      + Integer.parseInt(f[1].substring(2, 4)) * 60
                      + Double.parseDouble(f[1].substring(4));
            }
            if (f[2].length() > 2 && f[4].length() > 3) {
                g.lat = Integer.parseInt(f[2].substring(0, 2)) + Double.parseDouble(f[2].substring(2)) / 60;
                if ("S".equals(f[3])) g.lat = -g.lat;
                g.lon = Integer.parseInt(f[4].substring(0, 3)) + Double.parseDouble(f[4].substring(3)) / 60;
                if ("W".equals(f[5])) g.lon = -g.lon;
            }
            g.quality = f[6].isEmpty() ? 0 : Integer.parseInt(f[6]);
            g.sats = f[7].isEmpty() ? 0 : Integer.parseInt(f[7]);
            if (!f[9].isEmpty()) g.alt = Double.parseDouble(f[9]);
            if (!f[11].isEmpty()) g.sep = Double.parseDouble(f[11]);
            g.height = g.alt + g.sep;
            if (!f[13].isEmpty()) g.age = Double.parseDouble(f[13]);
        } catch (NumberFormatException e) {
            return null;
        }
        return g;
    }

    public String qualityName() {
        switch (quality) {
            case 4: return "RTK FIXED";
            case 5: return "RTK float";
            case 2: return "DGPS";
            case 1: return "GPS only";
            case 0: return "no fix";
            default: return "quality " + quality;
        }
    }
}
