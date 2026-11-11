package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.Arrays;
import java.util.List;

public class MapOriginTest {
    // Made-up coordinates near the equator.
    private static final String BASE = "{\"type\":\"FeatureCollection\",\"features\":["
            + "{\"type\":\"Feature\",\"properties\":{\"name\":\"fence\"},\"geometry\":{\"type\":\"LineString\","
            + "\"coordinates\":[[0.0003,0.0004,10],[0.0005,0.0004,10]]}},"
            + "{\"type\":\"Feature\",\"properties\":{\"name\":\"Base\",\"kind\":\"base\"},"
            + "\"geometry\":{\"type\":\"Point\",\"coordinates\":[0.0001,0.0002,31.5]}}]}";
    private static final String ORIGIN = "{\"type\":\"FeatureCollection\",\"features\":["
            + "{\"type\":\"Feature\",\"properties\":{\"name\":\"G\",\"kind\":\"Origin\"},"
            + "\"geometry\":{\"type\":\"Point\",\"coordinates\":[0.0002,0.0001,30.0]}}]}";

    private static GeoJson.Layer parse(String s) throws Exception { return GeoJson.parse(s); }

    @Test public void originBeatsBaseBeatsFixBeatsVertex() throws Exception {
        List<GeoJson.Layer> layers = Arrays.asList(parse(BASE), parse(ORIGIN));
        List<String> names = Arrays.asList("base.geojson", "origin.geojson");
        MapOrigin.Candidate o = MapOrigin.find(layers, names, "origin");
        MapOrigin.Candidate b = MapOrigin.find(layers, names, "base");
        MapOrigin.Candidate v = MapOrigin.firstVertex(layers, names);
        TrackPoint fix = new TrackPoint(1234, 0.0003, 0.0003, 29.0, 4, null);

        MapOrigin m = MapOrigin.choose(o, b, fix, v);
        assertEquals(MapOrigin.Source.ORIGIN, m.source);
        assertEquals("G", m.name);
        assertEquals(0.0001, m.lat, 0);
        assertEquals(0.0002, m.lon, 0);
        assertEquals(30.0, m.h, 0);
        assertTrue(m.note, m.note.contains("G in origin.geojson"));

        m = MapOrigin.choose(null, b, fix, v);
        assertEquals(MapOrigin.Source.BASE, m.source);
        assertEquals("Base", m.name);
        assertEquals(31.5, m.h, 0);

        m = MapOrigin.choose(null, null, fix, v);
        assertEquals(MapOrigin.Source.FIX, m.source);
        assertEquals("fix|1234", m.key);

        m = MapOrigin.choose(null, null, null, v);
        assertEquals(MapOrigin.Source.VERTEX, m.source);
        assertEquals(0.0003, m.lon, 0);   // first vertex of the first layer: the fence

        assertNull(MapOrigin.choose(null, null, null, null));
    }

    @Test public void noOriginFeatureMeansNoCandidate() throws Exception {
        List<GeoJson.Layer> layers = Arrays.asList(parse(BASE));
        assertNull(MapOrigin.find(layers, Arrays.asList("base.geojson"), "origin"));
    }

    @Test public void readoutIsExactEnuAboutTheOrigin() {
        MapOrigin.Candidate g = new MapOrigin.Candidate(-71.0, 43.0, 25.0, "G", "origin.geojson");
        MapOrigin m = MapOrigin.choose(g, null, null, null);
        LocalFrame f = new LocalFrame(43.0, -71.0, 25.0);
        double lat = 43.0003, lon = -70.9996, h = 26.25;
        double[] want = f.toEnu(lat, lon, h);
        double[] got = m.xyz(lat, lon, h);
        for (int i = 0; i < 3; i++) assertEquals(want[i], got[i], 1e-9);
        // about 33 m north, 33 m east: z is h - h0 less the curvature drop (under 0.2 mm here)
        assertEquals(h - 25.0, got[2], 2e-4);
        assertEquals(String.format(java.util.Locale.US, "G x %.3f y %.3f z %.3f", want[0], want[1], want[2]),
                m.readout(lat, lon, h));
        assertEquals("G x 0.000 y 0.000 z 0.000", m.readout(43.0, -71.0, 25.0));
    }

    @Test public void readoutWithoutHeights() {
        MapOrigin m = MapOrigin.choose(new MapOrigin.Candidate(-71.0, 43.0, 25.0, "G", "o"), null, null, null);
        assertEquals("G x 0.000 y 0.000", m.readout(43.0, -71.0, Double.NaN));
        assertNull(m.readout(Double.NaN, Double.NaN, 1));
        MapOrigin flat = MapOrigin.choose(new MapOrigin.Candidate(-71.0, 43.0, Double.NaN, null, "o"), null, null, null);
        assertEquals("origin", flat.name);
        assertEquals("origin x 0.000 y 0.000", flat.readout(43.0, -71.0, 30.0));
    }
}
