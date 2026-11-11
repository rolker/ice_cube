package rink.icecube.logger.core;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Parser for the map layer files: a GeoJSON FeatureCollection (a single Feature or a bare
 * geometry is accepted too) with Point, MultiPoint, LineString, MultiLineString, Polygon
 * or MultiPolygon geometries. Positions are [lon, lat] or [lon, lat, h] in WGS84 degrees,
 * h ellipsoidal metres. Feature properties used: name (label), kind (free text; "base"
 * marks the map origin), stroke (#rrggbb or #aarrggbb), fill (#rrggbb or #aarrggbb).
 *
 * A file that is not JSON or not GeoJSON throws {@link GeoJsonException}. A single bad
 * feature is skipped and described in {@link Layer#warnings}.
 */
public final class GeoJson {
    /** How a feature is drawn. */
    public enum Shape { POINTS, LINES, POLYGONS }

    public static final class GeoJsonException extends Exception {
        public GeoJsonException(String m) { super(m); }
    }

    public static final class Feature {
        public final Shape shape;
        public final String geometryType;
        /**
         * POINTS: one part holding every point. LINES: one part per line. POLYGONS: one part
         * per ring, all rings of all polygons (fill with the even-odd rule). Each part is
         * {lon, lat, h, lon, lat, h, ...} with h NaN where the file gave none.
         */
        public final List<double[]> parts;
        public final String name, kind;   // null if absent
        public final Integer stroke, fill; // ARGB, null if absent

        Feature(Shape shape, String geometryType, List<double[]> parts, String name, String kind, Integer stroke, Integer fill) {
            this.shape = shape; this.geometryType = geometryType; this.parts = parts;
            this.name = name; this.kind = kind; this.stroke = stroke; this.fill = fill;
        }

        public boolean isBase() { return kind != null && kind.trim().equalsIgnoreCase("base"); }

        public int vertexCount() {
            int n = 0;
            for (double[] p : parts) n += p.length / 3;
            return n;
        }
    }

    public static final class Layer {
        public final List<Feature> features = new ArrayList<>();
        public final List<String> warnings = new ArrayList<>();

        public int vertexCount() {
            int n = 0;
            for (Feature f : features) n += f.vertexCount();
            return n;
        }

        /** First position {lon, lat, h} of the first feature with kind "base", or null. */
        public double[] basePosition() {
            for (Feature f : features) {
                if (f.isBase() && !f.parts.isEmpty()) {
                    double[] p = f.parts.get(0);
                    return new double[] {p[0], p[1], p[2]};
                }
            }
            return null;
        }
    }

    private GeoJson() { }

    public static Layer parse(String text) throws GeoJsonException {
        JSONObject o;
        try {
            o = new JSONObject(text);
        } catch (JSONException e) {
            throw new GeoJsonException("not a valid JSON object: " + e.getMessage());
        }
        String type = o.optString("type", "");
        Layer layer = new Layer();
        if ("FeatureCollection".equals(type)) {
            JSONArray fs = o.optJSONArray("features");
            if (fs == null) throw new GeoJsonException("FeatureCollection has no features array");
            for (int i = 0; i < fs.length(); i++) {
                JSONObject f = fs.optJSONObject(i);
                if (f == null) { layer.warnings.add("feature " + i + ": not an object, skipped"); continue; }
                addFeature(layer, f, "feature " + i);
            }
        } else if ("Feature".equals(type)) {
            addFeature(layer, o, "feature 0");
        } else if (isGeometryType(type)) {
            JSONObject f = new JSONObject();
            try { f.put("type", "Feature").put("geometry", o); } catch (JSONException e) { throw new GeoJsonException(e.getMessage()); }
            addFeature(layer, f, "geometry");
        } else {
            throw new GeoJsonException(type.isEmpty() ? "no GeoJSON type" : "unsupported GeoJSON type " + type);
        }
        return layer;
    }

    private static boolean isGeometryType(String t) {
        switch (t) {
            case "Point": case "MultiPoint": case "LineString": case "MultiLineString":
            case "Polygon": case "MultiPolygon": case "GeometryCollection":
                return true;
            default:
                return false;
        }
    }

    private static void addFeature(Layer layer, JSONObject f, String where) {
        JSONObject g = f.optJSONObject("geometry");
        if (g == null) { layer.warnings.add(where + ": no geometry, skipped"); return; }
        JSONObject props = f.optJSONObject("properties");
        String name = text(props, "name"), kind = text(props, "kind");
        if (name != null) where += " (" + name + ")";
        Integer stroke = null, fill = null;
        try { stroke = color(text(props, "stroke")); }
        catch (IllegalArgumentException e) { layer.warnings.add(where + ": " + e.getMessage() + ", default stroke used"); }
        try { fill = color(text(props, "fill")); }
        catch (IllegalArgumentException e) { layer.warnings.add(where + ": " + e.getMessage() + ", default fill used"); }
        String type = g.optString("type", "");
        try {
            Object c = g.opt("coordinates");
            List<double[]> parts = new ArrayList<>();
            Shape shape;
            switch (type) {
                case "Point":
                    shape = Shape.POINTS;
                    parts.add(position(c));
                    break;
                case "MultiPoint": {
                    shape = Shape.POINTS;
                    JSONArray a = array(c, "MultiPoint coordinates");
                    if (a.length() == 0) throw new IllegalArgumentException("MultiPoint is empty");
                    double[] all = new double[a.length() * 3];
                    for (int i = 0; i < a.length(); i++) System.arraycopy(position(a.opt(i)), 0, all, i * 3, 3);
                    parts.add(all);
                    break;
                }
                case "LineString":
                    shape = Shape.LINES;
                    parts.add(positions(c, 2, "LineString"));
                    break;
                case "MultiLineString": {
                    shape = Shape.LINES;
                    JSONArray a = array(c, "MultiLineString coordinates");
                    for (int i = 0; i < a.length(); i++) parts.add(positions(a.opt(i), 2, "LineString"));
                    break;
                }
                case "Polygon":
                    shape = Shape.POLYGONS;
                    addRings(parts, c);
                    break;
                case "MultiPolygon": {
                    shape = Shape.POLYGONS;
                    JSONArray a = array(c, "MultiPolygon coordinates");
                    for (int i = 0; i < a.length(); i++) addRings(parts, a.opt(i));
                    break;
                }
                default:
                    layer.warnings.add(where + ": geometry type " + (type.isEmpty() ? "missing" : type) + " not supported, skipped");
                    return;
            }
            if (parts.isEmpty()) throw new IllegalArgumentException(type + " is empty");
            layer.features.add(new Feature(shape, type, parts, name, kind, stroke, fill));
        } catch (IllegalArgumentException e) {
            layer.warnings.add(where + ": " + e.getMessage() + ", skipped");
        }
    }

    private static void addRings(List<double[]> parts, Object c) {
        JSONArray rings = array(c, "Polygon coordinates");
        if (rings.length() == 0) throw new IllegalArgumentException("Polygon has no rings");
        for (int i = 0; i < rings.length(); i++) parts.add(positions(rings.opt(i), 3, "Polygon ring"));
    }

    private static JSONArray array(Object o, String what) {
        if (!(o instanceof JSONArray)) throw new IllegalArgumentException(what + " is not an array");
        return (JSONArray) o;
    }

    /** A list of at least min positions, flattened to {lon, lat, h, ...}. */
    private static double[] positions(Object o, int min, String what) {
        JSONArray a = array(o, what);
        if (a.length() < min) throw new IllegalArgumentException(what + " needs at least " + min + " positions");
        double[] out = new double[a.length() * 3];
        for (int i = 0; i < a.length(); i++) System.arraycopy(position(a.opt(i)), 0, out, i * 3, 3);
        return out;
    }

    /** [lon, lat] or [lon, lat, h] to {lon, lat, h}, h NaN if absent. */
    private static double[] position(Object o) {
        JSONArray a = array(o, "position");
        if (a.length() < 2) throw new IllegalArgumentException("position needs lon and lat");
        double lon = number(a.opt(0)), lat = number(a.opt(1));
        double h = a.length() > 2 && a.opt(2) instanceof Number ? ((Number) a.opt(2)).doubleValue() : Double.NaN;
        if (Double.isNaN(lon) || Double.isNaN(lat) || Math.abs(lon) > 180 || Math.abs(lat) > 90) {
            throw new IllegalArgumentException("position out of range (expected [lon, lat] in degrees)");
        }
        return new double[] {lon, lat, h};
    }

    private static double number(Object o) {
        if (!(o instanceof Number)) throw new IllegalArgumentException("coordinate is not a number");
        return ((Number) o).doubleValue();
    }

    private static String text(JSONObject props, String key) {
        if (props == null || props.isNull(key)) return null;
        Object v = props.opt(key);
        if (v == null) return null;
        String s = String.valueOf(v).trim();
        return s.isEmpty() ? null : s;
    }

    /** "#rrggbb" (opaque) or "#aarrggbb" to ARGB; null for null. */
    public static Integer color(String s) {
        if (s == null) return null;
        String h = s.trim();
        if (h.startsWith("#")) h = h.substring(1);
        if ((h.length() != 6 && h.length() != 8) || !h.matches("[0-9a-fA-F]+")) {
            throw new IllegalArgumentException(String.format(Locale.US, "bad color \"%s\" (use #rrggbb or #aarrggbb)", s));
        }
        long v = Long.parseLong(h, 16);
        if (h.length() == 6) v |= 0xFF000000L;
        return (int) v;
    }
}
