package rink.icecube.logger.core;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import org.junit.Test;

/** Synthetic layers near 0N 0E. */
public class GeoJsonTest {
    static final String ALL =
            "{\"type\":\"FeatureCollection\",\"features\":["
            + "{\"type\":\"Feature\",\"properties\":{\"name\":\"Base\",\"kind\":\"base\"},"
            + "  \"geometry\":{\"type\":\"Point\",\"coordinates\":[0.001,0.002,30.5]}},"
            + "{\"type\":\"Feature\",\"properties\":{\"name\":\"Pins\",\"stroke\":\"#ff0000\"},"
            + "  \"geometry\":{\"type\":\"MultiPoint\",\"coordinates\":[[0.0,0.0],[0.0001,0.0001]]}},"
            + "{\"type\":\"Feature\",\"properties\":{\"kind\":\"survey\"},"
            + "  \"geometry\":{\"type\":\"LineString\",\"coordinates\":[[0,0],[0.0002,0],[0.0002,0.0002]]}},"
            + "{\"type\":\"Feature\",\"properties\":null,"
            + "  \"geometry\":{\"type\":\"MultiLineString\",\"coordinates\":[[[0,0],[1e-4,0]],[[0,1e-4],[1e-4,1e-4],[2e-4,2e-4]]]}},"
            + "{\"type\":\"Feature\",\"properties\":{\"name\":\"Rink\",\"kind\":\"rink\",\"fill\":\"#4000ff00\"},"
            + "  \"geometry\":{\"type\":\"Polygon\",\"coordinates\":[[[0,0],[1e-4,0],[1e-4,1e-4],[0,0]],"
            + "                                                      [[2e-5,2e-5],[5e-5,2e-5],[5e-5,5e-5],[2e-5,2e-5]]]}},"
            + "{\"type\":\"Feature\",\"geometry\":{\"type\":\"MultiPolygon\",\"coordinates\":["
            + "  [[[0,0],[1e-4,0],[1e-4,1e-4],[0,0]]], [[[1e-3,1e-3],[2e-3,1e-3],[2e-3,2e-3],[1e-3,1e-3]]]]}}"
            + "]}";

    @Test public void everyGeometryType() throws Exception {
        GeoJson.Layer l = GeoJson.parse(ALL);
        assertEquals(l.warnings.toString(), 0, l.warnings.size());
        assertEquals(6, l.features.size());

        GeoJson.Feature base = l.features.get(0);
        assertEquals(GeoJson.Shape.POINTS, base.shape);
        assertEquals("Base", base.name);
        assertTrue(base.isBase());
        assertArrayEquals(new double[] {0.001, 0.002, 30.5}, base.parts.get(0), 0);
        assertArrayEquals(new double[] {0.001, 0.002, 30.5}, l.basePosition(), 0);

        GeoJson.Feature pins = l.features.get(1);
        assertEquals(1, pins.parts.size());
        assertEquals(2, pins.vertexCount());
        assertTrue(Double.isNaN(pins.parts.get(0)[2]));
        assertEquals(Integer.valueOf(0xFFFF0000), pins.stroke);

        GeoJson.Feature line = l.features.get(2);
        assertEquals(GeoJson.Shape.LINES, line.shape);
        assertNull(line.name);
        assertEquals("survey", line.kind);
        assertEquals(3, line.vertexCount());

        GeoJson.Feature multiLine = l.features.get(3);
        assertEquals(2, multiLine.parts.size());
        assertNull(multiLine.kind);
        assertNull(multiLine.stroke);

        GeoJson.Feature rink = l.features.get(4);
        assertEquals(GeoJson.Shape.POLYGONS, rink.shape);
        assertEquals(2, rink.parts.size());      // outer ring and hole
        assertEquals(Integer.valueOf(0x4000FF00), rink.fill);

        GeoJson.Feature multiPoly = l.features.get(5);
        assertEquals(2, multiPoly.parts.size());
        assertNull(multiPoly.name);
        assertEquals(1 + 2 + 3 + 5 + 8 + 8, l.vertexCount());
    }

    @Test public void singleFeatureAndBareGeometry() throws Exception {
        GeoJson.Layer f = GeoJson.parse("{\"type\":\"Feature\",\"geometry\":{\"type\":\"Point\",\"coordinates\":[1,2]}}");
        assertEquals(1, f.features.size());
        GeoJson.Layer g = GeoJson.parse("{\"type\":\"LineString\",\"coordinates\":[[1,2],[3,4]]}");
        assertEquals(GeoJson.Shape.LINES, g.features.get(0).shape);
        assertNull(g.basePosition());
    }

    @Test public void badFeaturesAreSkippedWithWarnings() throws Exception {
        GeoJson.Layer l = GeoJson.parse("{\"type\":\"FeatureCollection\",\"features\":["
                + "{\"type\":\"Feature\",\"geometry\":null},"
                + "{\"type\":\"Feature\",\"geometry\":{\"type\":\"Point\",\"coordinates\":[\"a\",2]}},"
                + "{\"type\":\"Feature\",\"geometry\":{\"type\":\"Point\",\"coordinates\":[2,95]}},"
                + "{\"type\":\"Feature\",\"geometry\":{\"type\":\"LineString\",\"coordinates\":[[1,2]]}},"
                + "{\"type\":\"Feature\",\"geometry\":{\"type\":\"GeometryCollection\",\"geometries\":[]}},"
                + "{\"type\":\"Feature\",\"properties\":{\"stroke\":\"red\"},\"geometry\":{\"type\":\"Point\",\"coordinates\":[1,2]}},"
                + "42]}");
        assertEquals(1, l.features.size());          // only the last point, with the default stroke
        assertNull(l.features.get(0).stroke);
        assertEquals(7, l.warnings.size());
    }

    @Test public void malformedFilesThrow() {
        String[] bad = {"", "not json", "{\"type\":\"FeatureCollection\",\"features\":[", "[1,2,3]",
                "{\"type\":\"Topology\"}", "{\"type\":\"FeatureCollection\"}", "{}"};
        for (String s : bad) {
            try { GeoJson.parse(s); fail("accepted: " + s); } catch (GeoJson.GeoJsonException expected) { }
        }
    }

    @Test public void colors() {
        assertEquals(Integer.valueOf(0xFF112233), GeoJson.color("#112233"));
        assertEquals(Integer.valueOf(0x80112233), GeoJson.color("#80112233"));
        assertNull(GeoJson.color(null));
        try { GeoJson.color("#12345"); fail(); } catch (IllegalArgumentException expected) { }
    }
}
