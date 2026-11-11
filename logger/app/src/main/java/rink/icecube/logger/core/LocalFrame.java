package rink.icecube.logger.core;

/**
 * Local east/north/up tangent plane on the WGS84 ellipsoid around an origin.
 * Geodetic positions go to ECEF and are then rotated into ENU at the origin, so the
 * result is exact (no flat-earth approximation) at any distance. Angles in degrees,
 * heights ellipsoidal in metres.
 */
public final class LocalFrame {
    public static final double A = 6378137.0;
    public static final double F = 1 / 298.257223563;
    public static final double E2 = F * (2 - F);

    public final double lat0, lon0, h0;
    private final double x0, y0, z0, sinLat, cosLat, sinLon, cosLon;

    public LocalFrame(double lat0, double lon0, double h0) {
        if (Double.isNaN(h0)) h0 = 0;
        this.lat0 = lat0;
        this.lon0 = lon0;
        this.h0 = h0;
        double[] o = toEcef(lat0, lon0, h0);
        x0 = o[0]; y0 = o[1]; z0 = o[2];
        sinLat = Math.sin(Math.toRadians(lat0));
        cosLat = Math.cos(Math.toRadians(lat0));
        sinLon = Math.sin(Math.toRadians(lon0));
        cosLon = Math.cos(Math.toRadians(lon0));
    }

    public static double[] toEcef(double lat, double lon, double h) {
        double p = Math.toRadians(lat), l = Math.toRadians(lon);
        double sp = Math.sin(p), cp = Math.cos(p);
        double n = A / Math.sqrt(1 - E2 * sp * sp);
        return new double[] {(n + h) * cp * Math.cos(l), (n + h) * cp * Math.sin(l), (n * (1 - E2) + h) * sp};
    }

    /** ECEF to {lat, lon, h}, iterating latitude until it settles (well under a micrometre). */
    public static double[] fromEcef(double x, double y, double z) {
        double lon = Math.atan2(y, x);
        double p = Math.hypot(x, y);
        double lat = Math.atan2(z, p * (1 - E2));
        double h = 0;
        for (int i = 0; i < 10; i++) {
            double s = Math.sin(lat);
            double n = A / Math.sqrt(1 - E2 * s * s);
            h = p / Math.cos(lat) - n;
            double next = Math.atan2(z, p * (1 - E2 * n / (n + h)));
            if (Math.abs(next - lat) < 1e-14) { lat = next; break; }
            lat = next;
        }
        return new double[] {Math.toDegrees(lat), Math.toDegrees(lon), h};
    }

    /** Writes {east, north, up} in metres to out. A NaN height means the origin height. */
    public void toEnu(double lat, double lon, double h, double[] out) {
        double[] x = toEcef(lat, lon, Double.isNaN(h) ? h0 : h);
        double dx = x[0] - x0, dy = x[1] - y0, dz = x[2] - z0;
        out[0] = -sinLon * dx + cosLon * dy;
        out[1] = -sinLat * cosLon * dx - sinLat * sinLon * dy + cosLat * dz;
        out[2] = cosLat * cosLon * dx + cosLat * sinLon * dy + sinLat * dz;
    }

    public double[] toEnu(double lat, double lon, double h) {
        double[] out = new double[3];
        toEnu(lat, lon, h, out);
        return out;
    }

    /** Inverse of toEnu: {lat, lon, h}. */
    public double[] toGeodetic(double e, double n, double u) {
        double dx = -sinLon * e - sinLat * cosLon * n + cosLat * cosLon * u;
        double dy = cosLon * e - sinLat * sinLon * n + cosLat * sinLon * u;
        double dz = cosLat * n + sinLat * u;
        return fromEcef(x0 + dx, y0 + dy, z0 + dz);
    }
}
