package rink.icecube.logger.core;

/**
 * One GGA epoch with the phone time it was read: what captures average. h is the
 * ellipsoidal antenna height (GGA alt + geoid separation), NaN if the GGA has no
 * separation. tod is the GGA UTC time of day in seconds, age the correction age in s.
 */
public final class Epoch {
    public final long elapsedNs;
    public final double tod, lat, lon, h, age;
    public final int quality;

    public Epoch(long elapsedNs, double tod, double lat, double lon, double h, double age, int quality) {
        this.elapsedNs = elapsedNs; this.tod = tod; this.lat = lat; this.lon = lon; this.h = h; this.age = age;
        this.quality = quality;
    }

    /** Null if there is no GGA. */
    public static Epoch from(Gga g, long elapsedNs) {
        if (g == null) return null;
        return new Epoch(elapsedNs, g.tod, g.lat, g.lon, g.height, g.age, g.quality);
    }

    public boolean hasPosition() { return quality > 0 && !Double.isNaN(lat) && !Double.isNaN(lon); }

    public boolean fixed() { return quality == 4 && hasPosition(); }
}
