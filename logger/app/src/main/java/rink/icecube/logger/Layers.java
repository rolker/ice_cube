package rink.icecube.logger;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.function.Consumer;

import rink.icecube.logger.core.GeoJson;

/**
 * The map layer files: *.geojson and *.json in layers/ under each storage root (see
 * {@link Storage}). Parsed files are cached until {@link #reload()}; a file that fails
 * to parse keeps its error and is skipped on the map.
 */
final class Layers {
    static final long MAX_BYTES = 20L << 20;

    static final class LayerFile {
        final File file;
        final GeoJson.Layer layer;   // null if the file could not be read or parsed
        final String error;

        LayerFile(File file, GeoJson.Layer layer, String error) { this.file = file; this.layer = layer; this.error = error; }

        String name() { return file.getName(); }

        String summary() {
            if (layer == null) return "error: " + error;
            String s = String.format(Locale.US, "%d features, %d vertices", layer.features.size(), layer.vertexCount());
            if (layer.basePosition() != null) s += ", has base";
            if (!layer.warnings.isEmpty()) s += String.format(Locale.US, ", %d skipped: %s", layer.warnings.size(), layer.warnings.get(0));
            return s;
        }
    }

    private static List<LayerFile> cache;
    private static int generation;
    private static final Handler UI = new Handler(Looper.getMainLooper());

    private Layers() { }

    /** Where imported layer files go. */
    static File importDir(Context ctx) { return Storage.writableDir(ctx, "layers"); }

    static synchronized void reload() { cache = null; generation++; }

    static synchronized int generation() { return generation; }

    /** Cached files, or a fresh scan. Runs on any thread; call from a background thread. */
    static List<LayerFile> get(Context ctx) {
        int gen;
        synchronized (Layers.class) {
            if (cache != null) return cache;
            gen = generation;
        }
        List<LayerFile> out = scan(ctx.getApplicationContext());
        synchronized (Layers.class) {
            if (gen == generation) cache = out;
        }
        return out;
    }

    /** Loads on a background thread and calls back on the UI thread. */
    static void getAsync(Context ctx, Consumer<List<LayerFile>> done) {
        Context app = ctx.getApplicationContext();
        new Thread(() -> {
            List<LayerFile> l = get(app);
            UI.post(() -> done.accept(l));
        }, "layers").start();
    }

    private static List<LayerFile> scan(Context ctx) {
        List<File> files = new ArrayList<>();
        for (File root : Storage.roots(ctx)) {
            File[] fs = new File(root, "layers").listFiles(f -> f.isFile() && isLayerName(f.getName()));
            if (fs != null) Collections.addAll(files, fs);
        }
        files.sort((a, b) -> a.getName().compareToIgnoreCase(b.getName()));
        List<LayerFile> out = new ArrayList<>();
        for (File f : files) out.add(parseFile(f));
        return Collections.unmodifiableList(out);
    }

    static boolean isLayerName(String n) {
        String l = n.toLowerCase(Locale.US);
        return !l.startsWith(".") && (l.endsWith(".geojson") || l.endsWith(".json"));
    }

    private static LayerFile parseFile(File f) {
        try {
            if (f.length() > MAX_BYTES) return new LayerFile(f, null, "larger than 20 MB");
            try (InputStream in = new FileInputStream(f)) {
                return new LayerFile(f, GeoJson.parse(readText(in, MAX_BYTES)), null);
            }
        } catch (IOException | SecurityException e) {
            return new LayerFile(f, null, "cannot read: " + e.getMessage());
        } catch (GeoJson.GeoJsonException e) {
            return new LayerFile(f, null, e.getMessage());
        } catch (RuntimeException | OutOfMemoryError e) {
            return new LayerFile(f, null, e.toString());
        }
    }

    /** Reads UTF-8 text, refusing more than max bytes. */
    static String readText(InputStream in, long max) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        byte[] buf = new byte[1 << 16];
        int n;
        while ((n = in.read(buf)) > 0) {
            b.write(buf, 0, n);
            if (b.size() > max) throw new IOException("larger than " + (max >> 20) + " MB");
        }
        return new String(b.toByteArray(), StandardCharsets.UTF_8);
    }
}
