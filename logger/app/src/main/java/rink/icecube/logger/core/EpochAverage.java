package rink.icecube.logger.core;

import java.util.ArrayList;
import java.util.List;

/**
 * The mean of a set of GGA epochs and their scatter. sdE, sdN and sdU are sample standard
 * deviations (n - 1) of the epochs about their mean, in metres east, north and up on the
 * local tangent plane at the mean (0 for one epoch). h is the mean ellipsoidal antenna
 * height over the epochs that have one (NaN if none). age is the mean correction age
 * (NaN if none given), tod the GGA time of the last epoch and quality its fix quality.
 */
public final class EpochAverage {
    public final int n, quality;
    public final double lat, lon, h, sdE, sdN, sdU, age, tod;
    public final long lastNs;

    private EpochAverage(int n, int quality, double lat, double lon, double h, double sdE, double sdN, double sdU,
                         double age, double tod, long lastNs) {
        this.n = n; this.quality = quality; this.lat = lat; this.lon = lon; this.h = h;
        this.sdE = sdE; this.sdN = sdN; this.sdU = sdU; this.age = age; this.tod = tod; this.lastNs = lastNs;
    }

    /** Preference among GGA qualities: RTK fixed, then float, DGPS, single, anything else with a position. */
    public static int rank(int quality) {
        switch (quality) {
            case 4: return 5;
            case 5: return 4;
            case 2: return 3;
            case 1: return 2;
            default: return quality > 0 ? 1 : 0;
        }
    }

    /**
     * Averages the best epochs of the last windowNs: of the epochs with a position whose
     * time is within windowNs before the latest such epoch (that one included), only those
     * of the best quality (see rank) are used. Null if there is no epoch with a position, or
     * the latest is more than maxAgeNs older than nowNs. epochs are oldest first.
     */
    public static EpochAverage recent(List<Epoch> epochs, long nowNs, long windowNs, long maxAgeNs) {
        Epoch latest = null;
        for (int i = epochs.size() - 1; i >= 0; i--) {
            if (epochs.get(i).hasPosition()) { latest = epochs.get(i); break; }
        }
        if (latest == null || nowNs - latest.elapsedNs > maxAgeNs) return null;
        int best = 0;
        List<Epoch> window = new ArrayList<>();
        for (Epoch e : epochs) {
            if (!e.hasPosition() || e.elapsedNs <= latest.elapsedNs - windowNs || e.elapsedNs > latest.elapsedNs) continue;
            window.add(e);
            best = Math.max(best, rank(e.quality));
        }
        List<Epoch> use = new ArrayList<>();
        for (Epoch e : window) if (rank(e.quality) == best) use.add(e);
        return of(use);
    }

    /** Mean and scatter of the given epochs (all assumed to have a position). Null if empty. */
    public static EpochAverage of(List<Epoch> es) {
        if (es.isEmpty()) return null;
        double sLat = 0, sLon = 0, sH = 0, sAge = 0;
        int nH = 0, nAge = 0;
        for (Epoch e : es) {
            sLat += e.lat;
            sLon += e.lon;
            if (!Double.isNaN(e.h)) { sH += e.h; nH++; }
            if (!Double.isNaN(e.age)) { sAge += e.age; nAge++; }
        }
        int n = es.size();
        double lat = sLat / n, lon = sLon / n, h = nH > 0 ? sH / nH : Double.NaN;
        double sdE = 0, sdN = 0, sdU = nH > 1 || (nH == 1 && n == 1) ? 0 : Double.NaN;
        if (n > 1) {
            LocalFrame f = new LocalFrame(lat, lon, Double.isNaN(h) ? 0 : h);
            double[] enu = new double[3];
            double ee = 0, nn = 0, uu = 0;
            for (Epoch e : es) {
                f.toEnu(e.lat, e.lon, Double.isNaN(e.h) ? (Double.isNaN(h) ? 0 : h) : e.h, enu);
                ee += enu[0] * enu[0];
                nn += enu[1] * enu[1];
                if (!Double.isNaN(e.h)) uu += enu[2] * enu[2];
            }
            sdE = Math.sqrt(ee / (n - 1));
            sdN = Math.sqrt(nn / (n - 1));
            if (nH > 1) sdU = Math.sqrt(uu / (nH - 1));
        }
        Epoch last = es.get(n - 1);
        return new EpochAverage(n, last.quality, lat, lon, h, sdE, sdN, sdU, nAge > 0 ? sAge / nAge : Double.NaN,
                last.tod, last.elapsedNs);
    }
}
