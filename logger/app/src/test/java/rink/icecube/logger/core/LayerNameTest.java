package rink.icecube.logger.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import org.junit.Test;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Locale;

/** Names for imported layer files (#8, review round 2 of #6). */
public class LayerNameTest {
    private static final List<String> NONE = Collections.emptyList();

    private static String problem(String original, List<String> existing) {
        try { LayerName.forImport(original, existing); fail(original); return null; }
        catch (IllegalArgumentException e) { return e.getMessage(); }
    }

    /** What Layers.isLayerName accepts (Layers needs Android, so it is repeated here). */
    private static boolean listed(String n) {
        String l = n.toLowerCase(Locale.US);
        return !l.startsWith(".") && (l.endsWith(".geojson") || l.endsWith(".json"));
    }

    @Test public void aSafeNameIsKeptSoAReimportReplacesIt() {
        assertEquals("rink_outline.geojson", LayerName.forImport("rink_outline.geojson", NONE));
        assertEquals("a-b.2.json", LayerName.forImport("a-b.2.json", NONE));
        assertEquals("Lot.GeoJSON", LayerName.forImport("Lot.GeoJSON", NONE));
        assertEquals("a_b.geojson", LayerName.forImport("a_b.geojson", Arrays.asList("a_b.geojson")));
    }

    @Test public void aSanitisedNameNeverCollidesWithAnotherOriginal() {
        String spaced = LayerName.forImport("a b.geojson", NONE);
        assertTrue(spaced, spaced.matches("a_b-[0-9a-f]{8}\\.geojson"));
        assertNotEquals("a_b.geojson", spaced);
        assertNotEquals(spaced, LayerName.forImport("a\tb.geojson", NONE));
        assertNotEquals(spaced, LayerName.forImport("a&b.geojson", NONE));
        assertEquals(spaced, LayerName.forImport("a b.geojson", Arrays.asList("a_b.geojson", spaced)));   // same original: same name
        assertTrue(listed(spaced));
    }

    @Test public void aNameWithoutALayerExtensionGetsGeojson() {
        String n = LayerName.forImport("parcels.txt", NONE);
        assertTrue(n, n.matches("parcels\\.txt-[0-9a-f]{8}\\.geojson"));
        assertNotEquals(LayerName.forImport("parcels.txt.geojson", NONE), n);
        assertTrue(listed(n));
    }

    @Test public void leadingDotsAreDroppedSoTheFileIsListed() {
        String n = LayerName.forImport(".hidden.geojson", NONE);
        assertTrue(n, n.matches("hidden-[0-9a-f]{8}\\.geojson"));
        assertTrue(listed(n));
    }

    @Test public void anEmptyStemIsRejectedWithAReason() {
        String why = "no name before the extension; rename the file and import it again";
        assertEquals(why, problem(".geojson", NONE));
        assertEquals(why, problem("..json", NONE));
        assertEquals(why, problem("", NONE));
        assertEquals(why, problem(null, NONE));
    }

    @Test public void aNameDifferingOnlyInCaseIsReported() {
        assertEquals("a layer named rink.geojson is already there (names differ only in upper/lower case);"
                + " rename one of them", problem("Rink.geojson", Arrays.asList("rink.geojson")));
        assertEquals("Rink.geojson", LayerName.forImport("Rink.geojson", Arrays.asList("Rink.geojson", "other.json")));
        assertFalse(LayerName.forImport("x.geojson", null).isEmpty());
    }
}
