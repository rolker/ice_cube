package rink.icecube.logger.core;

import java.util.List;
import java.util.Locale;

/**
 * The map's origin and the local coordinates shown in it.
 *
 * Choice, first that exists: the first point of a feature with kind "origin" in the
 * switched-on layers (the project's frame, e.g. lot monument G); else the first point of a
 * kind "base" feature; else the session's first fix; else, so layers can still be viewed,
 * the first vertex of the first layer. Layers are searched in the order given (file name
 * order), features in file order.
 *
 * The frame is exact ENU about the origin ({@link LocalFrame}): x east, y north, z up in
 * metres. z is the ENU up component, i.e. the ellipsoidal height above the origin's
 * height less the earth-curvature drop (d^2 / 2R: 0.8 mm at 100 m), the same as the
 * session importer's z. An origin without a height (a 2D point) gives x and y only.
 */
public final class MapOrigin {
    public enum Source { ORIGIN, BASE, FIX, VERTEX }

    public final Source source;
    public final double lat, lon, h;   // h NaN if the origin has no height
    /** The frame's short name: the origin or base feature's name, else "base", "first fix" or "first vertex". */
    public final String name;
    /** Where it came from, for the info box, e.g. "origin: G in origin.geojson". */
    public final String note;
    /** Changes when the origin changes (a new first fix, another layer set). */
    public final String key;
    public final LocalFrame frame;

    private MapOrigin(Source source, double lat, double lon, double h, String name, String note, String key) {
        this.source = source; this.lat = lat; this.lon = lon; this.h = h;
        this.name = name; this.note = note; this.key = key;
        this.frame = new LocalFrame(lat, lon, h);
    }

    public boolean hasHeight() { return !Double.isNaN(h); }

    /** A point taken from a layer: {lon, lat, h} plus the feature name and file. */
    public static final class Candidate {
        public final double lon, lat, h;
        public final String featureName, file;
        public Candidate(double lon, double lat, double h, String featureName, String file) {
            this.lon = lon; this.lat = lat; this.h = h; this.featureName = featureName; this.file = file;
        }
    }

    /** The first point of the first feature of this kind (any case) in the layers, or null. */
    public static Candidate find(List<GeoJson.Layer> layers, List<String> files, String kind) {
        for (int i = 0; i < layers.size(); i++) {
            for (GeoJson.Feature f : layers.get(i).features) {
                if (f.kind == null || !f.kind.trim().equalsIgnoreCase(kind) || f.parts.isEmpty()) continue;
                double[] p = f.parts.get(0);
                if (p.length < 3) continue;
                return new Candidate(p[0], p[1], p[2], f.name, files.get(i));
            }
        }
        return null;
    }

    /** The first vertex of the first non-empty layer, or null. */
    public static Candidate firstVertex(List<GeoJson.Layer> layers, List<String> files) {
        for (int i = 0; i < layers.size(); i++) {
            for (GeoJson.Feature f : layers.get(i).features) {
                if (f.parts.isEmpty() || f.parts.get(0).length < 3) continue;
                double[] p = f.parts.get(0);
                return new Candidate(p[0], p[1], p[2], f.name, files.get(i));
            }
        }
        return null;
    }

    /** Picks the origin in the order origin, base, first fix, first vertex; null if none. */
    public static MapOrigin choose(Candidate origin, Candidate base, TrackPoint firstFix, Candidate vertex) {
        if (origin != null) {
            String n = blank(origin.featureName) ? "origin" : origin.featureName.trim();
            return new MapOrigin(Source.ORIGIN, origin.lat, origin.lon, origin.h, n,
                    "origin: " + n + " in " + origin.file, "origin|" + coords(origin));
        }
        if (base != null) {
            String n = blank(base.featureName) ? "base" : base.featureName.trim();
            return new MapOrigin(Source.BASE, base.lat, base.lon, base.h, n,
                    "origin: base point " + (blank(base.featureName) ? "" : "'" + n + "' in ") + base.file,
                    "base|" + coords(base));
        }
        if (firstFix != null) {
            return new MapOrigin(Source.FIX, firstFix.lat, firstFix.lon, firstFix.h, "first fix",
                    "origin: first fix of this session", "fix|" + firstFix.elapsedMs);
        }
        if (vertex != null) {
            return new MapOrigin(Source.VERTEX, vertex.lat, vertex.lon, vertex.h, "first vertex",
                    "origin: first layer vertex (no origin or base point, no fix yet)", "vertex|" + coords(vertex));
        }
        return null;
    }

    /** {x, y, z} of a point in this frame; z NaN if the point or the origin has no height. */
    public double[] xyz(double lat, double lon, double h) {
        double[] e = frame.toEnu(lat, lon, Double.isNaN(h) ? this.h : h);
        if (Double.isNaN(h) || !hasHeight()) e[2] = Double.NaN;
        return e;
    }

    /** "G x 12.345 y -3.210 z 0.123" (z omitted when unknown); null for a point without a position. */
    public String readout(double lat, double lon, double h) {
        if (Double.isNaN(lat) || Double.isNaN(lon)) return null;
        double[] p = xyz(lat, lon, h);
        String s = String.format(Locale.US, "%s x %.3f y %.3f", name, p[0], p[1]);
        if (!Double.isNaN(p[2])) s += String.format(Locale.US, " z %.3f", p[2]);
        return s;
    }

    private static boolean blank(String s) { return s == null || s.trim().isEmpty(); }

    private static String coords(Candidate c) {
        return String.format(Locale.US, "%.10f %.10f %.5f", c.lat, c.lon, c.h);
    }
}
