package rink.icecube.logger.core;

import java.util.Locale;

/**
 * Compares a map layer's base point (kind "base", [lon, lat, h] WGS84 ellipsoid) with the
 * ARP the base broadcasts in RTCM 1005/1006 (ECEF). Both are the antenna reference point,
 * so the 1006 antenna height is not applied. The difference is received ARP minus layer
 * point, in east/north/up at the layer point. Over 0.05 m horizontally or vertically means
 * the layers are not in the base's frame (or the base or the layer is wrong).
 */
public final class BaseCheck {
    public static final double TOLERANCE_M = 0.05;

    public final double dE, dN, dU;   // dU NaN when the layer point has no height
    public final boolean ok;

    private BaseCheck(double dE, double dN, double dU) {
        this.dE = dE; this.dN = dN; this.dU = dU;
        ok = Math.hypot(dE, dN) <= TOLERANCE_M && (Double.isNaN(dU) || Math.abs(dU) <= TOLERANCE_M);
    }

    public static BaseCheck compare(double lon, double lat, double h, StationArp arp) {
        LocalFrame f = new LocalFrame(lat, lon, Double.isNaN(h) ? 0 : h);
        double[] llh = LocalFrame.fromEcef(arp.x, arp.y, arp.z);
        double[] enu = f.toEnu(llh[0], llh[1], llh[2]);
        return new BaseCheck(enu[0], enu[1], Double.isNaN(h) ? Double.NaN : enu[2]);
    }

    public String describe() {
        String d = String.format(Locale.US, "dE %.3f dN %.3f dU %s m", dE, dN,
                Double.isNaN(dU) ? "n/a (layer base has no height)" : String.format(Locale.US, "%.3f", dU));
        return ok ? "layers match base (" + d + ")" : "map layers are not in this base's frame: " + d;
    }
}
