package rink.icecube.logger.core;

import java.nio.charset.StandardCharsets;
import java.util.Collection;
import java.util.Locale;
import java.util.zip.CRC32;

/**
 * The file name an imported layer is saved under in the layers folder (logger 0.7, issue
 * #8), without Android so it is JVM-tested.
 *
 * A picked file whose name is already safe (letters, digits, '.', '_', '-', not starting with
 * '.', ending in .geojson or .json) keeps it, so importing the same file again replaces the
 * earlier copy. Any other name is sanitised (other characters to '_', leading dots dropped,
 * .geojson added when the name has no layer extension) and gets '-' and 8 hex digits of a
 * CRC-32 of the original name before the extension: "a b.geojson" becomes
 * "a_b-xxxxxxxx.geojson", so it never replaces an "a_b.geojson" imported from another file,
 * and still replaces its own earlier import.
 */
public final class LayerName {
    private LayerName() { }

    /**
     * The name to save the layer picked as `original` under, given the names already in the
     * layers folder (`existing`, may be null). Throws IllegalArgumentException with the reason
     * when there is no usable name: nothing before the extension (".geojson"), or a different
     * layer whose name differs only in letter case (shared storage may ignore case, so it
     * could be replaced).
     */
    public static String forImport(String original, Collection<String> existing) {
        String name = original == null ? "" : original;
        String lower = name.toLowerCase(Locale.US);
        String ext, stem;
        if (lower.endsWith(".geojson") || lower.endsWith(".json")) {
            int dot = name.lastIndexOf('.');
            ext = name.substring(dot);
            stem = name.substring(0, dot);
        } else {
            ext = ".geojson";
            stem = name;
        }
        String safe = stem.replaceAll("[^A-Za-z0-9._-]", "_").replaceFirst("^\\.+", "");
        if (safe.isEmpty()) {
            throw new IllegalArgumentException("no name before the extension; rename the file and import it again");
        }
        String target = safe + ext;
        if (!target.equals(name)) target = safe + "-" + crc(name) + ext;
        if (existing != null) {
            for (String e : existing) {
                if (e.equalsIgnoreCase(target) && !e.equals(target)) {
                    throw new IllegalArgumentException("a layer named " + e + " is already there (names differ only in"
                            + " upper/lower case); rename one of them");
                }
            }
        }
        return target;
    }

    private static String crc(String s) {
        CRC32 c = new CRC32();
        c.update(s.getBytes(StandardCharsets.UTF_8));
        return String.format(Locale.US, "%08x", c.getValue());
    }
}
