package rink.icecube.logger.core;

/** One GGA position for the map: trail point, current fix or control mark. */
public final class TrackPoint {
    public final long elapsedMs;
    public final double lat, lon, h;   // h ellipsoidal, NaN if unknown
    public final int quality;          // GGA fix quality
    public final String label;         // control marks only, else null

    public TrackPoint(long elapsedMs, double lat, double lon, double h, int quality, String label) {
        this.elapsedMs = elapsedMs; this.lat = lat; this.lon = lon; this.h = h; this.quality = quality; this.label = label;
    }

    /** A usable position from a GGA, or null if it has no fix. */
    public static TrackPoint from(Gga g, long elapsedMs) {
        if (g == null || g.quality <= 0 || Double.isNaN(g.lat) || Double.isNaN(g.lon)) return null;
        return new TrackPoint(elapsedMs, g.lat, g.lon, g.height, g.quality, null);
    }

    public TrackPoint withLabel(String l) { return new TrackPoint(elapsedMs, lat, lon, h, quality, l); }
}
