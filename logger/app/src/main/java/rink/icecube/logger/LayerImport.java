package rink.icecube.logger;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.provider.DocumentsContract;
import android.provider.OpenableColumns;
import android.widget.Toast;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

import rink.icecube.logger.core.GeoJson;

/**
 * Importing layer files, shared by Settings > Map > Import layer files… and MENU > Layers >
 * Import…: the system file picker (Drive and Downloads appear there), then each picked file
 * is checked to parse as GeoJSON and copied to {@link Layers#importDir} under a sanitised
 * name (a file of the same name is replaced). A report dialog lists one line per file
 * (features, skipped features, or why it was not imported).
 *
 * The picker is asked to open where the last import came from (EXTRA_INITIAL_URI, the last
 * picked document); pickers that ignore it open at their default.
 */
final class LayerImport {
    private LayerImport() { }

    /** Starts the picker; the result comes back to the activity's onActivityResult with `request`. */
    static void start(Activity a, int request) {
        try { a.startActivityForResult(pickerIntent(a), request); }
        catch (android.content.ActivityNotFoundException e) { Toast.makeText(a, "No file picker", Toast.LENGTH_LONG).show(); }
    }

    static Intent pickerIntent(Context c) {
        Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
                .setType("*/*").putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
        String last = Settings.prefs(c).getString(Settings.LAST_LAYER_IMPORT_URI, null);
        if (last != null) i.putExtra(DocumentsContract.EXTRA_INITIAL_URI, Uri.parse(last));
        return i;
    }

    /**
     * Imports the picked files on a background thread, then on the UI thread runs `imported`
     * (layers already reloaded) and shows the report; `dismissed` runs when the report is
     * closed. Either may be null. Nothing happens for a cancelled pick.
     */
    static void run(Activity a, int result, Intent data, Runnable imported, Runnable dismissed) {
        if (result != Activity.RESULT_OK || data == null) return;
        List<Uri> uris = new ArrayList<>();
        if (data.getClipData() != null) {
            for (int i = 0; i < data.getClipData().getItemCount(); i++) uris.add(data.getClipData().getItemAt(i).getUri());
        } else if (data.getData() != null) uris.add(data.getData());
        if (uris.isEmpty()) return;
        Settings.prefs(a).edit().putString(Settings.LAST_LAYER_IMPORT_URI, uris.get(uris.size() - 1).toString()).apply();
        Context app = a.getApplicationContext();   // the file I/O does not hold on to the activity
        File dir = Layers.importDir(app);
        new Thread(() -> {
            StringBuilder report = new StringBuilder();
            for (Uri u : uris) report.append(importOne(app, u, dir)).append('\n');
            Layers.reload();
            a.runOnUiThread(() -> {
                if (a.isFinishing() || a.isDestroyed()) return;
                if (imported != null) imported.run();
                AlertDialog d = new AlertDialog.Builder(a).setTitle("Import layers").setMessage(report.toString().trim())
                        .setPositiveButton("OK", null).create();
                if (dismissed != null) d.setOnDismissListener(x -> dismissed.run());
                d.show();
            });
        }, "import-layers").start();
    }

    /** Copies one picked file after checking that it parses. Returns a line for the report. */
    private static String importOne(Context c, Uri u, File dir) {
        String name = displayName(c, u);
        try {
            String text;
            try (InputStream in = c.getContentResolver().openInputStream(u)) {
                if (in == null) throw new IOException("cannot open");
                text = Layers.readText(in, Layers.MAX_BYTES);
            }
            GeoJson.Layer l = GeoJson.parse(text);
            String safe = name.replaceAll("[^A-Za-z0-9._-]", "_");
            if (!Layers.isLayerName(safe)) safe = safe + ".geojson";
            File out = new File(dir, safe);
            try (OutputStream o = new FileOutputStream(out)) { o.write(text.getBytes(StandardCharsets.UTF_8)); }
            return String.format(Locale.US, "%s: %d features%s", safe, l.features.size(),
                    l.warnings.isEmpty() ? "" : ", " + l.warnings.size() + " skipped");
        } catch (IOException | SecurityException e) {
            return name + ": not imported (" + e.getMessage() + ")";
        } catch (GeoJson.GeoJsonException e) {
            return name + ": not imported, not a GeoJSON layer (" + e.getMessage() + ")";
        }
    }

    private static String displayName(Context ctx, Uri u) {
        try (Cursor c = ctx.getContentResolver().query(u, new String[] {OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (c != null && c.moveToFirst() && !c.isNull(0)) return c.getString(0);
        } catch (RuntimeException ignored) { }
        String last = u.getLastPathSegment();
        return last == null ? "layer.geojson" : last.substring(last.lastIndexOf('/') + 1);
    }
}
